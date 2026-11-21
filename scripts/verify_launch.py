"Windows smoke test: launch the real entrypoint, find its window, close cleanly."

import ctypes
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path


def process_family(parent_pid: int) -> set[int]:
    """Windows venv python.exe can forward execution to a child python process."""

    class ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("size", wintypes.DWORD),
            ("usage", wintypes.DWORD),
            ("pid", wintypes.DWORD),
            ("heap", ctypes.c_size_t),
            ("module", wintypes.DWORD),
            ("threads", wintypes.DWORD),
            ("parent_pid", wintypes.DWORD),
            ("priority", wintypes.LONG),
            ("flags", wintypes.DWORD),
            ("exe", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = (wintypes.HANDLE, ctypes.POINTER(ProcessEntry))
    kernel32.Process32NextW.argtypes = (wintypes.HANDLE, ctypes.POINTER(ProcessEntry))
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    snapshot = kernel32.CreateToolhelp32Snapshot(2, 0)
    if snapshot == wintypes.HANDLE(-1).value:
        raise ctypes.WinError()
    relationships = []
    try:
        entry = ProcessEntry()
        entry.size = ctypes.sizeof(entry)
        available = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while available:
            relationships.append((entry.pid, entry.parent_pid))
            available = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    family = {parent_pid}
    while True:
        descendants = {pid for pid, parent in relationships if parent in family}
        if descendants.issubset(family):
            return family
        family.update(descendants)


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("This smoke check requires Windows.")
    user32 = ctypes.windll.user32
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = (callback_type, wintypes.LPARAM)
    user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user32.IsWindowVisible.argtypes = (wintypes.HWND,)
    user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    root = Path(__file__).resolve().parents[1]
    process = subprocess.Popen(
        [sys.executable, str(root / "run.py")],
        cwd=root,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    found = []
    family = {process.pid}

    @callback_type
    def visit(hwnd, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in family and user32.IsWindowVisible(hwnd):
            title = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, title, len(title))
            if title.value.startswith("FileMind AI ·"):
                found.append((hwnd, title.value))
        return True

    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and process.poll() is None:
            family = process_family(process.pid)
            user32.EnumWindows(visit, 0)
            if found:
                break
            time.sleep(0.1)
        assert found, "The real application did not show its main window."
        user32.PostMessageW(found[0][0], 0x0010, 0, 0)  # WM_CLOSE, normal preference save
        assert process.wait(timeout=10) == 0
        print(f"PASS: native Windows main window opened and closed cleanly: {found[0][1]}")
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


if __name__ == "__main__":
    main()
