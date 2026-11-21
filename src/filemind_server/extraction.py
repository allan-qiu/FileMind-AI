"""Consent-scoped, bounded text excerpts. No image/audio/video content is transmitted."""

import zipfile
from pathlib import Path
from xml.etree import ElementTree


def extract_excerpt(path: Path, filename: str, mode: str):
    if mode == "metadata":
        return "", "metadata_only"
    suffix = Path(filename).suffix.lower()
    limit = 4000 if mode == "summary" else 16000
    if suffix in {
        ".txt",
        ".md",
        ".py",
        ".js",
        ".ts",
        ".tsx",
        ".json",
        ".csv",
        ".sql",
        ".html",
        ".css",
        ".java",
        ".c",
        ".cpp",
        ".rs",
        ".go",
        ".yaml",
        ".log",
    }:
        with path.open("rb") as stream:
            return stream.read(limit * 4).decode("utf-8", errors="replace")[:limit], "text_excerpt"
    if path.stat().st_size > 16 * 1024 * 1024:
        return "", "extraction_size_limit_metadata_fallback"
    try:
        if suffix == ".pdf":
            from pypdf import PdfReader

            reader = PdfReader(path)
            if reader.is_encrypted:
                return "", "encrypted_pdf_metadata_fallback"
            return "\n".join((p.extract_text() or "")[:limit] for p in reader.pages[:5])[
                :limit
            ], "pdf_excerpt"
        prefixes = {
            ".docx": ("word/document.xml",),
            ".xlsx": ("xl/sharedStrings.xml", "xl/worksheets/"),
            ".pptx": ("ppt/slides/slide",),
        }
        if suffix in prefixes:
            with zipfile.ZipFile(path) as archive:
                items = [
                    i
                    for i in archive.infolist()
                    if i.filename.endswith(".xml") and i.filename.startswith(prefixes[suffix])
                ]
                if len(items) > 100 or sum(i.file_size for i in items) > 8 * 1024 * 1024:
                    return "", "extraction_limit_metadata_fallback"
                text = []
                for item in items:
                    tree = ElementTree.fromstring(archive.read(item))
                    text.append(" ".join(tree.itertext())[:limit])
                    if sum(map(len, text)) >= limit:
                        break
                return "\n".join(text)[:limit], "office_xml_excerpt"
    except Exception:
        return "", "extraction_failed_metadata_fallback"
    return "", "unsupported_content_metadata_fallback"
