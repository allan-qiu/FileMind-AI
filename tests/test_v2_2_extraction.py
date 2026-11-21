import zipfile

import pytest

from filemind_server.extraction import extract_excerpt
from filemind_server.service_lock import ServiceLock


@pytest.mark.parametrize(
    "suffix,entry",
    [
        (".docx", "word/document.xml"),
        (".xlsx", "xl/sharedStrings.xml"),
        (".pptx", "ppt/slides/slide1.xml"),
    ],
)
def test_same_topic_office_xml_extraction_and_metadata_consent(tmp_path, suffix, entry):
    path = tmp_path / ("ambiguous" + suffix)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(entry, "<root><p>Python 学习 synthetic notes</p></root>")
    text, status = extract_excerpt(path, path.name, "content")
    assert "Python" in text and "学习" in text and status == "office_xml_excerpt"
    assert extract_excerpt(path, path.name, "metadata") == ("", "metadata_only")


def test_pdf_actual_text_and_bounded_excerpt(tmp_path):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=600, height=800)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 40 700 Td (Python synthetic learning notes) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    path = tmp_path / "invoice.pdf"
    writer.write(path)
    text, status = extract_excerpt(path, path.name, "summary")
    assert "Python synthetic learning" in text and status == "pdf_excerpt"


def test_oversized_office_expansion_is_metadata_fallback(tmp_path):
    path = tmp_path / "large.docx"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", b"x" * (8 * 1024 * 1024 + 1))
    assert extract_excerpt(path, path.name, "content")[1] == "extraction_limit_metadata_fallback"


def test_single_process_lock_released_without_deleting_data(tmp_path):
    path = tmp_path / "service.lock"
    with ServiceLock(path):
        with pytest.raises(RuntimeError, match="一个FileMind"):
            with ServiceLock(path):
                pass
    with ServiceLock(path):
        assert path.exists()


def test_storage_handles_deep_review_directory_with_real_io(tmp_path):
    import hashlib

    from filemind_server.storage import LocalStorageProvider

    storage = LocalStorageProvider(tmp_path / ("a" * 80) / ("b" * 80) / ("c" * 80))
    source = tmp_path / "source.txt"
    source.write_bytes(b"synthetic deep path")
    key = "user/学习资料/version/notes.txt"
    assert len(str(storage.path(key))) > 260
    result = storage.upload(source, key)
    assert result["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    with storage.read(key) as stream:
        assert stream.read() == source.read_bytes()
