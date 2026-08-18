"""Unit tests for extension parsers — PdfParser, DocxParser, HtmlParser,
ExcelParser, PptxParser, LLMDocumentParser, OCRParser.

All tests use sys.modules mocks to simulate optional dependencies
(pymupdf, python-docx, beautifulsoup4, openpyxl, python-pptx, openai,
pytesseract, Pillow, pdf2image).
"""
from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

from agentbase.extensions.parsers import (
    DocxParser,
    ExcelParser,
    HtmlParser,
    LLMDocumentParser,
    OCRParser,
    PdfParser,
    PptxParser,
)

# ---------------------------------------------------------------------------
# PdfParser
# ---------------------------------------------------------------------------


class TestPdfParser:
    def test_parse_import_error(self, tmp_path):
        f = tmp_path / "test.pdf"
        f.write_bytes(b"fake pdf")
        with patch.dict(sys.modules, {"pymupdf": None, "fitz": None}):
            parser = PdfParser()
            with pytest.raises(ImportError, match="pymupdf"):
                parser.parse(f)

    def test_parse_success(self, tmp_path):
        f = tmp_path / "test.pdf"
        f.write_bytes(b"fake pdf")

        fake_pymupdf = ModuleType("pymupdf")
        mock_doc = MagicMock()
        mock_page = MagicMock()
        mock_page.get_text.return_value = "Page 1 text"
        mock_doc.__iter__ = MagicMock(return_value=iter([mock_page]))
        mock_doc.close = MagicMock()
        mock_doc.__enter__ = MagicMock(return_value=mock_doc)
        mock_doc.__exit__ = MagicMock(return_value=False)
        fake_pymupdf.open = MagicMock(return_value=mock_doc)

        with patch.dict(sys.modules, {"pymupdf": fake_pymupdf}):
            parser = PdfParser()
            result = parser.parse(f)
            assert "Page 1 text" in result

    def test_parse_multiple_pages(self, tmp_path):
        f = tmp_path / "multi.pdf"
        f.write_bytes(b"fake")

        fake_pymupdf = ModuleType("pymupdf")
        pages = [MagicMock() for _ in range(3)]
        for i, p in enumerate(pages):
            p.get_text.return_value = f"Page {i+1}"
        mock_doc = MagicMock()
        mock_doc.__iter__ = MagicMock(return_value=iter(pages))
        mock_doc.close = MagicMock()
        fake_pymupdf.open = MagicMock(return_value=mock_doc)

        with patch.dict(sys.modules, {"pymupdf": fake_pymupdf}):
            result = PdfParser().parse(f)
            assert "Page 1" in result
            assert "Page 2" in result
            assert "Page 3" in result
            assert result.count("\n\n") >= 2  # pages joined by \n\n


# ---------------------------------------------------------------------------
# DocxParser
# ---------------------------------------------------------------------------


class TestDocxParser:
    def test_parse_import_error(self, tmp_path):
        f = tmp_path / "test.docx"
        f.write_bytes(b"fake")
        with patch.dict(sys.modules, {"docx": None}):
            with pytest.raises(ImportError, match="python-docx"):
                DocxParser().parse(f)

    def test_parse_success_paragraphs(self, tmp_path):
        f = tmp_path / "test.docx"
        f.write_bytes(b"fake")

        fake_docx = ModuleType("docx")
        mock_doc = MagicMock()
        mock_para1 = MagicMock()
        mock_para1.text = "First paragraph"
        mock_para2 = MagicMock()
        mock_para2.text = "  "  # whitespace — should be filtered
        mock_para3 = MagicMock()
        mock_para3.text = "Second paragraph"
        mock_doc.paragraphs = [mock_para1, mock_para2, mock_para3]
        mock_doc.tables = []
        fake_docx.Document = MagicMock(return_value=mock_doc)

        with patch.dict(sys.modules, {"docx": fake_docx}):
            result = DocxParser().parse(f)
            assert "First paragraph" in result
            assert "Second paragraph" in result

    def test_parse_with_table(self, tmp_path):
        f = tmp_path / "table.docx"
        f.write_bytes(b"fake")

        fake_docx = ModuleType("docx")
        mock_doc = MagicMock()
        mock_doc.paragraphs = []
        mock_cell1 = MagicMock()
        mock_cell1.text = "Cell A"
        mock_cell2 = MagicMock()
        mock_cell2.text = "Cell B"
        mock_row = MagicMock()
        mock_row.cells = [mock_cell1, mock_cell2]
        mock_table = MagicMock()
        mock_table.rows = [mock_row]
        mock_doc.tables = [mock_table]
        fake_docx.Document = MagicMock(return_value=mock_doc)

        with patch.dict(sys.modules, {"docx": fake_docx}):
            result = DocxParser().parse(f)
            assert "Cell A" in result
            assert "Cell B" in result
            assert "|" in result


# ---------------------------------------------------------------------------
# HtmlParser
# ---------------------------------------------------------------------------


class TestHtmlParser:
    def test_parse_import_error(self, tmp_path):
        f = tmp_path / "test.html"
        f.write_text("<html><body>Hello</body></html>")
        with patch.dict(sys.modules, {"bs4": None}):
            with pytest.raises(ImportError, match="beautifulsoup4"):
                HtmlParser().parse(f)

    def test_parse_success(self, tmp_path):
        f = tmp_path / "test.html"
        f.write_text(
            "<html><head><style>body{color:red}</style></head>"
            "<body><script>alert('x')</script><h1>Title</h1><p>Content</p></body></html>"
        )
        try:
            from bs4 import BeautifulSoup  # noqa: F401
        except ImportError:
            pytest.skip("beautifulsoup4 not installed")

        result = HtmlParser().parse(f)
        assert "Title" in result
        assert "Content" in result
        # Script and style should be removed
        assert "alert" not in result
        assert "color:red" not in result


# ---------------------------------------------------------------------------
# ExcelParser
# ---------------------------------------------------------------------------


class TestExcelParser:
    def test_parse_import_error(self, tmp_path):
        f = tmp_path / "test.xlsx"
        f.write_bytes(b"fake")
        with patch.dict(sys.modules, {"openpyxl": None}):
            with pytest.raises(ImportError, match="openpyxl"):
                ExcelParser().parse(f)

    def test_parse_success(self, tmp_path):
        f = tmp_path / "test.xlsx"
        f.write_bytes(b"fake")

        fake_openpyxl = ModuleType("openpyxl")
        mock_sheet = MagicMock()
        mock_sheet.title = "Sheet1"
        mock_sheet.iter_rows.return_value = [
            ("A1", "B1", None),
            ("A2", None, "C2"),
        ]
        mock_wb = MagicMock()
        mock_wb.worksheets = [mock_sheet]
        mock_wb.close = MagicMock()
        fake_openpyxl.load_workbook = MagicMock(return_value=mock_wb)

        with patch.dict(sys.modules, {"openpyxl": fake_openpyxl}):
            result = ExcelParser().parse(f)
            assert "Sheet1" in result
            assert "A1" in result
            assert "B1" in result
            assert "C2" in result

    def test_parse_empty_rows_skipped(self, tmp_path):
        f = tmp_path / "empty.xlsx"
        f.write_bytes(b"fake")

        fake_openpyxl = ModuleType("openpyxl")
        mock_sheet = MagicMock()
        mock_sheet.title = "Empty"
        mock_sheet.iter_rows.return_value = [
            (None, None, None),
            ("Data", None, None),
        ]
        mock_wb = MagicMock()
        mock_wb.worksheets = [mock_sheet]
        mock_wb.close = MagicMock()
        fake_openpyxl.load_workbook = MagicMock(return_value=mock_wb)

        with patch.dict(sys.modules, {"openpyxl": fake_openpyxl}):
            result = ExcelParser().parse(f)
            assert "Data" in result
            # Empty row should not produce a line
            lines = result.split("\n")
            assert not any(ln.strip() == "" and ln.count("|") > 0 for ln in lines)


# ---------------------------------------------------------------------------
# PptxParser
# ---------------------------------------------------------------------------


class TestPptxParser:
    def test_parse_import_error(self, tmp_path):
        f = tmp_path / "test.pptx"
        f.write_bytes(b"fake")
        with patch.dict(sys.modules, {"pptx": None}):
            with pytest.raises(ImportError, match="python-pptx"):
                PptxParser().parse(f)

    def test_parse_success(self, tmp_path):
        f = tmp_path / "test.pptx"
        f.write_bytes(b"fake")

        fake_pptx = ModuleType("pptx")
        mock_shape = MagicMock()
        mock_shape.has_text_frame = True
        mock_para = MagicMock()
        mock_para.text = "Slide text"
        mock_shape.text_frame.paragraphs = [mock_para]
        mock_shape.has_table = False
        mock_slide = MagicMock()
        mock_slide.shapes = [mock_shape]
        mock_prs = MagicMock()
        mock_prs.slides = [mock_slide]
        fake_pptx.Presentation = MagicMock(return_value=mock_prs)

        with patch.dict(sys.modules, {"pptx": fake_pptx}):
            result = PptxParser().parse(f)
            assert "Slide 1" in result
            assert "Slide text" in result

    def test_parse_with_table(self, tmp_path):
        f = tmp_path / "table.pptx"
        f.write_bytes(b"fake")

        fake_pptx = ModuleType("pptx")
        mock_cell = MagicMock()
        mock_cell.text = "TableCell"
        mock_row = MagicMock()
        mock_row.cells = [mock_cell]
        mock_table = MagicMock()
        mock_table.rows = [mock_row]

        mock_shape = MagicMock()
        mock_shape.has_text_frame = False
        mock_shape.has_table = True
        mock_shape.table = mock_table

        mock_slide = MagicMock()
        mock_slide.shapes = [mock_shape]
        mock_prs = MagicMock()
        mock_prs.slides = [mock_slide]
        fake_pptx.Presentation = MagicMock(return_value=mock_prs)

        with patch.dict(sys.modules, {"pptx": fake_pptx}):
            result = PptxParser().parse(f)
            assert "TableCell" in result


# ---------------------------------------------------------------------------
# LLMDocumentParser
# ---------------------------------------------------------------------------


class TestLLMDocumentParser:
    def test_init_defaults(self):
        parser = LLMDocumentParser()
        assert parser._model == "gpt-4o"
        assert parser._api_key is None
        assert parser._base_url is None

    def test_init_custom(self):
        parser = LLMDocumentParser(model="claude-3", api_key="sk-test", base_url="http://custom")
        assert parser._model == "claude-3"
        assert parser._api_key == "sk-test"
        assert parser._base_url == "http://custom"

    def test_get_client_import_error(self):
        parser = LLMDocumentParser()
        with patch.dict(sys.modules, {"openai": None}):
            with pytest.raises(ImportError, match="openai"):
                parser._get_client()

    def test_get_client_with_explicit_key(self):
        parser = LLMDocumentParser(api_key="sk-explicit", base_url="http://custom")
        fake_openai = ModuleType("openai")
        fake_openai.OpenAI = MagicMock()
        with patch.dict(sys.modules, {"openai": fake_openai}):
            parser._get_client()
            fake_openai.OpenAI.assert_called_once_with(
                api_key="sk-explicit", base_url="http://custom"
            )

    def test_get_client_with_env_key(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "env-key")
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
        parser = LLMDocumentParser()
        fake_openai = ModuleType("openai")
        fake_openai.OpenAI = MagicMock()
        with patch.dict(sys.modules, {"openai": fake_openai}):
            parser._get_client()
            fake_openai.OpenAI.assert_called_once_with(api_key="env-key")

    def test_get_client_no_key_no_base(self, monkeypatch):
        for var in ("OPENAI_API_KEY", "SILICONFLOW_API_KEY", "OPENAI_BASE_URL"):
            monkeypatch.delenv(var, raising=False)
        parser = LLMDocumentParser()
        fake_openai = ModuleType("openai")
        fake_openai.OpenAI = MagicMock()
        with patch.dict(sys.modules, {"openai": fake_openai}):
            parser._get_client()
            fake_openai.OpenAI.assert_called_once_with()

    def test_parse_image(self, tmp_path):
        f = tmp_path / "test.png"
        f.write_bytes(b"fake image data")

        fake_openai = ModuleType("openai")
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = "Parsed markdown"
        mock_client.chat.completions.create.return_value = mock_resp
        fake_openai.OpenAI = MagicMock(return_value=mock_client)

        with patch.dict(sys.modules, {"openai": fake_openai}):
            parser = LLMDocumentParser(api_key="sk-test")
            result = parser.parse(f)
            assert result == "Parsed markdown"

    def test_parse_pdf(self, tmp_path):
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"fake pdf")

        fake_openai = ModuleType("openai")
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = "PDF markdown"
        mock_client.chat.completions.create.return_value = mock_resp
        fake_openai.OpenAI = MagicMock(return_value=mock_client)

        with patch.dict(sys.modules, {"openai": fake_openai}):
            parser = LLMDocumentParser(api_key="sk-test")
            result = parser.parse(f)
            assert result == "PDF markdown"

    def test_parse_unknown_mime(self, tmp_path):
        f = tmp_path / "doc.xyz"
        f.write_bytes(b"unknown")

        fake_openai = ModuleType("openai")
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = "Unknown"
        mock_client.chat.completions.create.return_value = mock_resp
        fake_openai.OpenAI = MagicMock(return_value=mock_client)

        with patch.dict(sys.modules, {"openai": fake_openai}):
            parser = LLMDocumentParser(api_key="sk-test")
            result = parser.parse(f)
            assert result == "Unknown"


# ---------------------------------------------------------------------------
# OCRParser
# ---------------------------------------------------------------------------


class TestOCRParser:
    def test_parse_import_error(self, tmp_path):
        f = tmp_path / "scan.png"
        f.write_bytes(b"fake")
        with patch.dict(sys.modules, {"pytesseract": None, "PIL": None}):
            with pytest.raises(ImportError, match="pytesseract"):
                OCRParser().parse(f)

    def test_parse_image(self, tmp_path):
        f = tmp_path / "scan.png"
        f.write_bytes(b"fake image")

        fake_pytesseract = ModuleType("pytesseract")
        fake_pytesseract.image_to_string = MagicMock(return_value="OCR text")
        fake_pil = ModuleType("PIL")
        fake_pil.Image = MagicMock()
        fake_pil.Image.open = MagicMock(return_value=MagicMock())

        with patch.dict(sys.modules, {
            "pytesseract": fake_pytesseract,
            "PIL": fake_pil,
        }):
            result = OCRParser().parse(f)
            assert result == "OCR text"

    def test_parse_pdf(self, tmp_path):
        f = tmp_path / "scan.pdf"
        f.write_bytes(b"fake pdf")

        fake_pytesseract = ModuleType("pytesseract")
        fake_pytesseract.image_to_string = MagicMock(return_value="PDF OCR text")
        fake_pil = ModuleType("PIL")
        fake_pil.Image = MagicMock()
        fake_pdf2image = ModuleType("pdf2image")
        fake_pdf2image.convert_from_path = MagicMock(return_value=[MagicMock()])

        with patch.dict(sys.modules, {
            "pytesseract": fake_pytesseract,
            "PIL": fake_pil,
            "pdf2image": fake_pdf2image,
        }):
            result = OCRParser().parse(f)
            assert "Page 1" in result
            assert "PDF OCR text" in result

    def test_parse_pdf_missing_pdf2image(self, tmp_path):
        f = tmp_path / "scan.pdf"
        f.write_bytes(b"fake")

        fake_pytesseract = ModuleType("pytesseract")
        fake_pytesseract.image_to_string = MagicMock(return_value="text")
        fake_pil = ModuleType("PIL")
        fake_pil.Image = MagicMock()

        with patch.dict(sys.modules, {
            "pytesseract": fake_pytesseract,
            "PIL": fake_pil,
            "pdf2image": None,
        }):
            with pytest.raises(ImportError, match="pdf2image"):
                OCRParser().parse(f)
