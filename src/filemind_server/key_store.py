"""Server key vault: Windows user-bound DPAPI; owner-only file on POSIX."""

import ctypes
import os
from pathlib import Path

from dotenv import dotenv_values


def server_secret(name):
    # Only server-side use; never return this mapping through status/config APIs.
    if name in os.environ:
        return os.environ[name]
    return dotenv_values(Path(__file__).resolve().parents[2] / ".env").get(name) or ""


def protect(raw: bytes, decrypt=False):
    if os.name != "nt":
        return raw

    class Blob(ctypes.Structure):
        _fields_ = [("size", ctypes.c_ulong), ("data", ctypes.POINTER(ctypes.c_char))]

    buffer = ctypes.create_string_buffer(raw)
    source = Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    target = Blob()
    if decrypt:
        ok = ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)
        )
    else:
        ok = ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)
        )
    if not ok:
        raise ValueError("服务端密钥保护失败")
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(target.data, ctypes.c_void_p))


class KeyStore:
    def __init__(self, data_dir: Path):
        self.path = data_dir / "private" / "ai.key"

    def get(self):
        value = server_secret("FILEMIND_DEEPSEEK_API_KEY")
        if value:
            return value
        return protect(self.path.read_bytes(), True).decode() if self.path.exists() else ""

    def set(self, value: str):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temp = self.path.with_suffix(".new")
        raw = protect(value.encode())
        with temp.open("wb") as stream:
            if os.name != "nt":
                os.fchmod(stream.fileno(), 0o600)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, self.path)
