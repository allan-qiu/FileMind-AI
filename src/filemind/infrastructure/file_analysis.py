import fnmatch
import hashlib
import os
import zipfile
from pathlib import Path
from threading import Event
from xml.etree import ElementTree

from filemind.domain.local import LocalFile, ScanResult

CHUNK_SIZE = 1024 * 1024
TEXT_TYPES = {
    ".txt",
    ".md",
    ".csv",
    ".json",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".html",
    ".css",
    ".log",
    ".xml",
}
GROUPS = {
    "文档": {".txt", ".md", ".pdf", ".doc", ".docx", ".ppt", ".pptx", ".rtf"},
    "表格": {".xlsx", ".xls", ".csv"},
    "图片": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif"},
    "压缩包": {".zip", ".7z", ".rar", ".gz", ".tar"},
    "视频": {".mp4", ".mkv", ".mov", ".avi"},
    "音频": {".mp3", ".wav", ".flac", ".m4a"},
    "代码": {".py", ".js", ".ts", ".tsx", ".html", ".css", ".java", ".cpp", ".c", ".sql"},
}


class Cancelled(RuntimeError):
    pass


def check_cancel(cancel: Event | None):
    if cancel is not None and cancel.is_set():
        raise Cancelled("用户已取消。")


def linked(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def reject_links(path: Path):
    if any(linked(part) for part in (path, *path.parents)):
        raise ValueError("不处理符号链接或目录联接路径。")


def content_hash(path: Path, cancel: Event | None = None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(CHUNK_SIZE):
            check_cancel(cancel)
            digest.update(block)
    return digest.hexdigest()


class FileAnalyzer:
    def analyze(self, path: Path, cancel: Event | None = None) -> LocalFile:
        path = path.absolute()
        reject_links(path)
        before = path.stat()
        if not path.is_file():
            raise ValueError("不是普通文件。")
        digest = content_hash(path, cancel)
        summary = self.summary(path, before.st_size)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ino,
        ):
            raise ValueError("文件正在变化，请写入完成后重试。")
        ext = path.suffix.lower()
        kind = next((name for name, extensions in GROUPS.items() if ext in extensions), "其他")
        return LocalFile(
            path=str(path),
            name=path.name,
            extension=ext,
            size_bytes=after.st_size,
            modified_ns=after.st_mtime_ns,
            created_at=getattr(after, "st_birthtime", None),
            sha256=digest,
            device=after.st_dev,
            inode=after.st_ino,
            kind=kind,
            summary=summary,
        )

    def summary(self, path: Path, size: int) -> str:
        try:
            if path.suffix.lower() in TEXT_TYPES:
                with path.open("rb") as stream:
                    return stream.read(32768).decode("utf-8", errors="replace")[:4000]
            if path.suffix.lower() in {".docx", ".xlsx", ".pptx"} and size <= 32 * 1024 * 1024:
                with zipfile.ZipFile(path) as archive:
                    names = [
                        n
                        for n in archive.namelist()
                        if n in ("word/document.xml", "xl/sharedStrings.xml")
                        or n.startswith("ppt/slides/slide")
                        and n.endswith(".xml")
                    ][:8]
                    texts = []
                    for name in names:
                        if archive.getinfo(name).file_size <= 1024 * 1024:
                            root = ElementTree.fromstring(archive.read(name))
                            texts.extend(root.itertext())
                    return " ".join(texts)[:4000]
            if path.suffix.lower() == ".pdf" and size <= 8 * 1024 * 1024:
                from pypdf import PdfReader

                reader = PdfReader(path)
                return " ".join((page.extract_text() or "")[:2000] for page in reader.pages[:2])[
                    :4000
                ]
        except Exception:
            # Unsupported/encrypted/damaged documents still have real metadata and a hash.
            return ""
        return ""


class LocalScanner:
    def __init__(self):
        self.analyzer = FileAnalyzer()

    def scan_many(
        self, paths, cancel: Event | None = None, exclude_dirs=(), exclude_types=(), progress=None
    ):
        files, issues, seen = [], [], set()
        total_bytes = 0
        excluded = {str(value).casefold() for value in exclude_dirs}
        types = {
            value.lower() if value.startswith(".") else "." + value.lower()
            for value in exclude_types
        }
        defaults = {".git", ".venv", ".filemind", ".filemind-server", ".cache"}

        def excluded_dir(path):
            return (
                linked(path)
                or path.name.casefold() in defaults
                or any(
                    fnmatch.fnmatch(str(path).casefold(), pattern)
                    or fnmatch.fnmatch(path.name.casefold(), pattern)
                    for pattern in excluded
                )
            )

        def candidates():
            for value in paths:
                path = Path(value).absolute()
                try:
                    reject_links(path)
                except (OSError, ValueError) as exc:
                    issues.append(f"{path}: {exc}")
                    continue
                if path.is_dir():
                    if excluded_dir(path):
                        continue
                    for directory, dirs, names in os.walk(
                        path, followlinks=False, onerror=lambda e: issues.append(str(e))
                    ):
                        check_cancel(cancel)
                        dirs[:] = [d for d in dirs if not excluded_dir(Path(directory) / d)]
                        for name in sorted(names):
                            yield Path(directory) / name
                else:
                    yield path

        try:
            for path in candidates():
                check_cancel(cancel)
                key = os.path.normcase(str(path))
                if key in seen or path.suffix.lower() in types or linked(path):
                    continue
                seen.add(key)
                try:
                    files.append(self.analyzer.analyze(path, cancel))
                    total_bytes += files[-1].size_bytes
                    if progress:
                        progress(len(files), total_bytes)
                except (OSError, ValueError) as exc:
                    issues.append(f"{path}: {exc}")
                if len(files) >= 10000:
                    issues.append("本次最多导入 10000 个文件，请缩小导入范围。")
                    break
        except Cancelled:
            return ScanResult(files=files, issues=issues, cancelled=True)
        except (OSError, ValueError) as exc:
            issues.append(str(exc))
        return ScanResult(files=files, issues=issues)

    def scan(self, directory: Path):
        return self.scan_many([directory]).files
