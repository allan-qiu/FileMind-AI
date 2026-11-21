"""Verify a Windows MSI in a fresh test install and data directory."""

import argparse
import json
import os
import re
import subprocess
from pathlib import Path


def msi(action, package, log):
    args = ["msiexec.exe", action, str(package), "/qn", "/norestart", "/l*v", str(log)]
    result = subprocess.run(args, timeout=180)
    if result.returncode:
        raise SystemExit(f"Windows Installer {action} exit {result.returncode}. See {log}.")


def verify(installation, isolated_data, credentials):
    from pywinauto import Application
    from pywinauto.timings import wait_until_passes

    # Pass a separate LOCALAPPDATA so the test never opens the regular client database.
    process = subprocess.Popen(
        [str(installation / "FileMind.exe")],
        cwd=installation,
        env={**os.environ, "LOCALAPPDATA": str(isolated_data)},
    )
    try:
        app = Application(backend="uia").connect(process=process.pid, timeout=25)
        main = app.window(title_re=r"^FileMind AI · ")
        main.wait("visible ready", timeout=25)
        search_root = main

        def control(name, kind):
            def find():
                for item in search_root.descendants(control_type=kind):
                    if name.fullmatch(item.element_info.name) and item.is_visible():
                        return item
                raise LookupError(f"Missing visible {kind}: {name.pattern}")

            return wait_until_passes(15, 0.25, find, exceptions=(LookupError,))

        control(re.compile("服务器文件"), "Button").invoke()
        dialog = control(re.compile(r"FileMind · 服务器文件"), "Window")
        search_root = dialog
        dialog.set_focus()
        control(re.compile("连接设置.*"), "CheckBox").toggle()
        address = control(re.compile("中央服务地址"), "Edit").get_value()
        assert address == "http://127.0.0.1:18765"
        if credentials:
            for caption, value in (
                ("云端用户名", "filemind_user"),
                ("云端密码", credentials["filemind_user"]),
            ):
                control(re.compile(caption), "Edit").set_edit_text(value)
            control(re.compile("登录"), "Button").invoke()
            control(re.compile(r"已连接真实账户.*"), "Text")
        dialog.type_keys("{ESC}")
        main.close()
        assert process.wait(timeout=15) == 0
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("msi", type=Path)
    parser.add_argument("--sandbox", type=Path, required=True)
    parser.add_argument(
        "--credentials", type=Path, help="Optional private local test credential file"
    )
    opts = parser.parse_args()
    package, root = opts.msi.resolve(strict=True), opts.sandbox.resolve()
    # A clean per-user registration avoids modifying an existing installed copy.
    import winreg

    uninstall = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
    for scope in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        with winreg.OpenKey(scope, uninstall) as key:
            for i in range(winreg.QueryInfoKey(key)[0]):
                with winreg.OpenKey(key, winreg.EnumKey(key, i)) as item:
                    try:
                        name = winreg.QueryValueEx(item, "DisplayName")[0]
                    except FileNotFoundError:
                        continue
                    if name == "FileMind AI":
                        raise SystemExit("FileMind AI is already installed for this tester.")
    root.mkdir(parents=True, exist_ok=False)
    installation = Path(os.environ["LOCALAPPDATA"]) / "FileMind AI Program"
    if installation.exists():
        raise SystemExit("The normal FileMind AI installation directory already exists.")
    profile = root / "profile"
    profile.mkdir()
    shortcut = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/FileMind AI.lnk"
    if shortcut.exists():
        raise SystemExit("A FileMind AI Start Menu shortcut already exists.")
    credentials = (
        json.loads(opts.credentials.read_text(encoding="utf-8")) if opts.credentials else None
    )
    msi("/i", package, root / "install.log")
    try:
        assert (installation / "FileMind.exe").is_file()
        assert shortcut.is_file()
        verify(installation, profile, credentials)
        app_db = profile / "FileMind AI/Desktop/.filemind/filemind.sqlite3"
        assert app_db.is_file() and not (installation / ".filemind").exists()
    finally:
        msi("/x", package, root / "uninstall.log")
    assert not (installation / "FileMind.exe").exists()
    assert not shortcut.exists()
    assert app_db.is_file()
    raw_log = (root / "install.log").read_bytes()
    encoding = "utf-16" if raw_log.startswith(b"\xff\xfe") else "mbcs"
    log = raw_log.decode(encoding, errors="replace")
    assert not re.search(r"MainEngineThread is returning (?!0\b)\d+", log)
    report = {
        "windows_installer": "PASS",
        "start_menu_shortcut": "PASS",
        "native_pyside6_window": "PASS",
        "cloud_address": "http://127.0.0.1:18765",
        "server_login": "PASS_REAL_API" if credentials else "NOT_EXECUTED",
        "local_sqlite_separated_from_program_files": True,
        "uninstall_preserves_local_database": True,
    }
    (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
