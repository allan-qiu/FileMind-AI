"""Tiny valid fixtures for acceptance, containing only authored synthetic material."""

import base64
import zipfile

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject


def create(root):
    root.mkdir(parents=True, exist_ok=True)
    fixtures = [
        ("Python学习笔记.md", "# Python study\nFunctions, classes, pytest and type hints."),
        ("untitled.txt", "Networking laboratory: VLAN 10, OSPF, IPv4 subnetting experiment."),
        (
            "invoice.java",
            "// Synthetic Java learning exercise\nclass Example { int sum(int a, int b)"
            " { return a+b; } }",
        ),
        (
            "项目需求说明.md",
            "# File organizer requirements\nPreview plans before moving files. "
            "Use version conflicts and independent sharing quotas.",
        ),
    ]
    paths = []
    for name, text in fixtures:
        path = root / name
        path.write_text(text, encoding="utf-8")
        paths.append((path, text))
    word = root / "Word学习资料.docx"
    with zipfile.ZipFile(word, "w") as package:
        package.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types '
            'xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.'
            'openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        )
        package.writestr(
            "_rels/.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/'
            'package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.'
            'openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/></Relationships>',
        )
        package.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.'
            'openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>'
            "Synthetic Java course notes and practice exercises.</w:t></w:r></w:p></w:body>"
            "</w:document>",
        )
    paths.append((word, "Synthetic Java course notes and practice exercises."))
    excel = root / "Excel实验数据.xlsx"
    with zipfile.ZipFile(excel, "w") as package:
        package.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/'
            'package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-'
            'officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/'
            'sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.'
            'spreadsheetml.worksheet+xml"/></Types>',
        )
        package.writestr(
            "_rels/.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/'
            'package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.'
            'openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="xl/workbook.xml"/></Relationships>',
        )
        package.writestr(
            "xl/workbook.xml",
            '<workbook xmlns="http://schemas.openxmlformats.org/'
            'spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/'
            '2006/relationships"><sheets><sheet name="Synthetic" sheetId="1" r:id="rId1"/>'
            "</sheets></workbook>",
        )
        package.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships xmlns="http://schemas.'
            'openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            'Target="worksheets/sheet1.xml"/></Relationships>',
        )
        package.writestr(
            "xl/worksheets/sheet1.xml",
            '<worksheet xmlns="http://schemas.'
            'openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" '
            't="inlineStr"><is><t>Synthetic network latency</t></is></c><c r="B1"><v>12</v>'
            "</c></row></sheetData></worksheet>",
        )
    paths.append((excel, "Synthetic network latency experiment table: 12 ms."))
    pdf = root / "PDF课程笔记.pdf"
    writer = PdfWriter()
    page = writer.add_blank_page(width=400, height=250)
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {
                    NameObject("/F1"): writer._add_object(
                        DictionaryObject(
                            {
                                NameObject("/Type"): NameObject("/Font"),
                                NameObject("/Subtype"): NameObject("/Type1"),
                                NameObject("/BaseFont"): NameObject("/Helvetica"),
                            }
                        )
                    )
                }
            )
        }
    )
    stream = DecodedStreamObject()
    stream.set_data(
        b"BT /F1 12 Tf 20 200 Td (Synthetic Python course notes: functions and tests.) Tj ET"
    )
    page[NameObject("/Contents")] = writer._add_object(stream)
    writer.write(pdf)
    paths.append((pdf, "Synthetic Python course notes: functions and tests."))
    png = root / "PNG示例图片.png"
    png.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jIuoAAAAASUVORK5CYII="
        )
    )
    paths.append((png, "Synthetic 1x1 PNG sample; metadata only, no vision analysis claimed."))
    return paths
