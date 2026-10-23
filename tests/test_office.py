"""Tests for the Office COM adapter (Excel / Word / PowerPoint).

Hermetic: every adapter is given an injected ``com_factory`` returning a
fake COM application object, so no Office installation, pywin32 or
Windows machine is required. The fakes mimic the COM surface the adapter
uses:

- Excel: ``app.Workbooks.Open(path)`` -> workbook with a ``Worksheets``
  collection (iterable + callable by name + ``.Count``), each sheet's
  ``Range(addr)`` returning a range object whose ``.Value`` is backed by
  a plain dict.
- Word: ``app.Documents.Open(path)`` -> document with ``Content.Text`` /
  ``Content.End`` and ``Range(start, end).InsertAfter(text)``.
- PowerPoint: ``app.Presentations.Open(path)`` -> presentation with an
  iterable ``Slides`` collection.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from desktop_mcp import core
from desktop_mcp.adapters.office import (
    ExcelAdapter,
    OfficeAdapter,
    PowerPointAdapter,
    WordAdapter,
    register,
)

ALLOWED_FILE = str(Path.home() / "book.xlsx")
ALLOWED_DOC = str(Path.home() / "doc.docx")
ALLOWED_PPT = str(Path.home() / "deck.pptx")


# ---------------------------------------------------------------------------
# Fake Excel COM objects
# ---------------------------------------------------------------------------


class FakeRange:
    """A range whose .Value reads/writes the owning sheet's dict store."""

    def __init__(self, sheet: "FakeSheet", address: str):
        self._sheet = sheet
        self._address = address

    @property
    def Value(self):
        return self._sheet.store.get(self._address)

    @Value.setter
    def Value(self, value):
        self._sheet.store[self._address] = value


class FakeSheet:
    def __init__(self, name: str, store: dict | None = None):
        self.Name = name
        self.store = dict(store or {})

    def Range(self, address: str) -> FakeRange:
        return FakeRange(self, address)


class FakeWorksheets:
    def __init__(self, sheets: list[FakeSheet]):
        self._sheets = list(sheets)

    def __iter__(self):
        return iter(self._sheets)

    def __call__(self, name: str) -> FakeSheet:
        for sheet in self._sheets:
            if sheet.Name == name:
                return sheet
        raise KeyError(f"no sheet named {name!r}")

    @property
    def Count(self) -> int:
        return len(self._sheets)


class FakeWorkbook:
    def __init__(self, sheets: list[FakeSheet], name: str = "book.xlsx"):
        self.Worksheets = FakeWorksheets(sheets)
        self.Name = name
        self.saved = False

    def Save(self):
        self.saved = True


class FakeWorkbooks:
    def __init__(self, workbook: FakeWorkbook):
        self._workbook = workbook
        self.opened: list[str] = []

    def Open(self, path: str) -> FakeWorkbook:
        self.opened.append(path)
        return self._workbook


class FakeExcelApp:
    def __init__(self, workbook: FakeWorkbook):
        self.Workbooks = FakeWorkbooks(workbook)


def make_excel() -> tuple[ExcelAdapter, FakeWorkbook, FakeSheet, FakeSheet]:
    sheet1 = FakeSheet("Sheet1", {"A1": 42, "A1:B2": ((1, 2), (3, 4))})
    sheet2 = FakeSheet("Data")
    wb = FakeWorkbook([sheet1, sheet2])
    app = FakeExcelApp(wb)
    return ExcelAdapter(com_factory=lambda: app), wb, sheet1, sheet2


# ---------------------------------------------------------------------------
# Fake Word COM objects
# ---------------------------------------------------------------------------


class FakeWordContent:
    def __init__(self, doc: "FakeWordDoc"):
        self._doc = doc

    @property
    def Text(self) -> str:
        return self._doc._text

    @Text.setter
    def Text(self, value: str):
        self._doc._text = value

    @property
    def End(self) -> int:
        # Word positions are 1-based: End is one past the last character.
        return len(self._doc._text) + 1


class FakeWordRange:
    def __init__(self, doc: "FakeWordDoc", start: int, end: int):
        self._doc = doc
        self.start = start
        self.end = end

    def InsertAfter(self, text: str):
        self._doc._text += text


class FakeWordDoc:
    def __init__(self, text: str = "", name: str = "doc.docx"):
        self._text = text
        self.Name = name
        self.Content = FakeWordContent(self)
        self.saved = False

    def Range(self, start: int, end: int) -> FakeWordRange:
        return FakeWordRange(self, start, end)

    def Save(self):
        self.saved = True


class FakeWordDocNoRange(FakeWordDoc):
    """A document whose Range factory is unavailable (fallback path)."""

    Range = None  # type: ignore[assignment]


class FakeDocuments:
    def __init__(self, doc: FakeWordDoc):
        self._doc = doc
        self.opened: list[str] = []

    def Open(self, path: str) -> FakeWordDoc:
        self.opened.append(path)
        return self._doc


class FakeWordApp:
    def __init__(self, doc: FakeWordDoc):
        self.Documents = FakeDocuments(doc)


# ---------------------------------------------------------------------------
# Fake PowerPoint COM objects
# ---------------------------------------------------------------------------


class FakeSlide:
    def __init__(self, name: str, layout: int, index: int):
        self.Name = name
        self.Layout = layout
        self.SlideIndex = index


class FakeSlides:
    def __init__(self, slides: list[FakeSlide]):
        self._slides = list(slides)

    def __iter__(self):
        return iter(self._slides)

    @property
    def Count(self) -> int:
        return len(self._slides)


class FakePresentation:
    def __init__(self, slides: list[FakeSlide], name: str = "deck.pptx"):
        self.Slides = FakeSlides(slides)
        self.Name = name
        self.saved = False

    def Save(self):
        self.saved = True


class FakePresentations:
    def __init__(self, pres: FakePresentation):
        self._pres = pres
        self.opened: list[str] = []

    def Open(self, path: str) -> FakePresentation:
        self.opened.append(path)
        return self._pres


class FakePptApp:
    def __init__(self, pres: FakePresentation):
        self.Presentations = FakePresentations(pres)


def make_ppt() -> tuple[PowerPointAdapter, FakePresentation]:
    slides = [FakeSlide("Title Slide", 1, 1), FakeSlide("Agenda", 2, 2)]
    pres = FakePresentation(slides)
    app = FakePptApp(pres)
    return PowerPointAdapter(com_factory=lambda: app), pres


# ---------------------------------------------------------------------------
# Excel tests
# ---------------------------------------------------------------------------


def test_excel_open_and_list_sheets():
    adapter, wb, _s1, _s2 = make_excel()
    result = adapter.open_workbook(ALLOWED_FILE)
    assert result["ok"] is True
    assert result["workbook"] == "book.xlsx"

    result = adapter.list_sheets()
    assert result["ok"] is True
    assert result["sheets"] == ["Sheet1", "Data"]
    assert result["count"] == 2


def test_excel_read_range_normalizes_2d():
    adapter, _wb, _s1, _s2 = make_excel()
    adapter.open_workbook(ALLOWED_FILE)
    result = adapter.read_range("Sheet1", "A1:B2")
    assert result["ok"] is True
    assert result["values"] == [[1, 2], [3, 4]]


def test_excel_read_range_normalizes_scalar():
    adapter, _wb, _s1, _s2 = make_excel()
    adapter.open_workbook(ALLOWED_FILE)
    result = adapter.read_range("Sheet1", "A1")
    assert result["ok"] is True
    assert result["values"] == [[42]]


def test_excel_write_cell_persists_and_audits(tmp_path, monkeypatch):
    monkeypatch.setenv("DESKTOP_MCP_HOME", str(tmp_path))
    adapter, _wb, _s1, sheet2 = make_excel()
    adapter.open_workbook(ALLOWED_FILE)

    result = adapter.write_cell("Data", "B3", "hello")
    assert result["ok"] is True
    assert sheet2.store["B3"] == "hello"

    # Read it back through the adapter: persisted in the fake store.
    result = adapter.read_range("Data", "B3")
    assert result["ok"] is True
    assert result["values"] == [["hello"]]

    # The audit call happened and did not crash: log holds the record.
    log = tmp_path / "audit.jsonl"
    assert log.exists()
    records = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert any(r.get("tool") == "excel_write_cell" for r in records)


def test_excel_save():
    adapter, wb, _s1, _s2 = make_excel()
    adapter.open_workbook(ALLOWED_FILE)
    result = adapter.save()
    assert result["ok"] is True
    assert wb.saved is True


def test_excel_open_rejects_disallowed_path():
    factory_calls = []

    def factory():
        factory_calls.append(True)
        return FakeExcelApp(FakeWorkbook([FakeSheet("Sheet1")]))

    adapter = ExcelAdapter(com_factory=factory)
    result = adapter.open_workbook("/etc/passwd.xlsx")
    assert result["ok"] is False
    assert "not allowed" in result["error"]
    # The allowlist check happens before COM is even touched.
    assert factory_calls == []


def test_excel_list_sheets_without_open_workbook():
    adapter, _wb, _s1, _s2 = make_excel()
    result = adapter.list_sheets()
    assert result["ok"] is False
    assert "No workbook is open" in result["error"]


# ---------------------------------------------------------------------------
# Word tests
# ---------------------------------------------------------------------------


def test_word_open_read_and_truncate():
    doc = FakeWordDoc("Hello AppMesh world")
    app = FakeWordApp(doc)
    adapter = WordAdapter(com_factory=lambda: app)

    result = adapter.open_document(ALLOWED_DOC)
    assert result["ok"] is True
    assert result["document"] == "doc.docx"

    result = adapter.read_text()
    assert result["ok"] is True
    assert result["text"] == "Hello AppMesh world"
    assert result["truncated"] is False
    assert result["chars"] == len("Hello AppMesh world")

    result = adapter.read_text(max_chars=5)
    assert result["ok"] is True
    assert result["text"] == "Hello"
    assert result["truncated"] is True


def test_word_append_text_via_range():
    doc = FakeWordDoc("Start.")
    adapter = WordAdapter(com_factory=lambda: FakeWordApp(doc))
    adapter.open_document(ALLOWED_DOC)

    result = adapter.append_text(" More.")
    assert result["ok"] is True
    assert doc._text == "Start. More."


def test_word_append_text_fallback_without_range():
    doc = FakeWordDocNoRange("Base")
    adapter = WordAdapter(com_factory=lambda: FakeWordApp(doc))
    adapter.open_document(ALLOWED_DOC)

    result = adapter.append_text("+tail")
    assert result["ok"] is True
    assert doc._text == "Base+tail"


def test_word_save():
    doc = FakeWordDoc("x")
    adapter = WordAdapter(com_factory=lambda: FakeWordApp(doc))
    adapter.open_document(ALLOWED_DOC)
    result = adapter.save()
    assert result["ok"] is True
    assert doc.saved is True


def test_word_open_rejects_disallowed_path():
    adapter = WordAdapter(com_factory=lambda: FakeWordApp(FakeWordDoc()))
    result = adapter.open_document("/etc/passwd.docx")
    assert result["ok"] is False
    assert "not allowed" in result["error"]


# ---------------------------------------------------------------------------
# PowerPoint tests
# ---------------------------------------------------------------------------


def test_ppt_open_and_list_slides():
    adapter, _pres = make_ppt()
    result = adapter.open_presentation(ALLOWED_PPT)
    assert result["ok"] is True
    assert result["presentation"] == "deck.pptx"

    result = adapter.list_slides()
    assert result["ok"] is True
    assert result["count"] == 2
    assert result["slides"] == [
        {"index": 1, "name": "Title Slide", "layout": 1},
        {"index": 2, "name": "Agenda", "layout": 2},
    ]


def test_ppt_save():
    adapter, pres = make_ppt()
    adapter.open_presentation(ALLOWED_PPT)
    result = adapter.save()
    assert result["ok"] is True
    assert pres.saved is True


def test_ppt_open_rejects_disallowed_path():
    adapter, _pres = make_ppt()
    result = adapter.open_presentation("/etc/passwd.pptx")
    assert result["ok"] is False
    assert "not allowed" in result["error"]


# ---------------------------------------------------------------------------
# Degradation + registration tests
# ---------------------------------------------------------------------------


@pytest.mark.skipif(core.IS_WINDOWS, reason="no-factory path only degrades off Windows")
def test_no_factory_off_windows_returns_clear_error():
    for adapter in (ExcelAdapter(), WordAdapter(), PowerPointAdapter()):
        assert isinstance(adapter, OfficeAdapter)
        assert adapter.prog_id.endswith(".Application")
    # Opening a workbook connects first, so off Windows it hits the
    # platform guard in _app() and degrades to a clear error dict.
    result = ExcelAdapter().open_workbook(ALLOWED_FILE)
    assert result["ok"] is False
    assert "Windows" in result["error"]
    result = WordAdapter().open_document(ALLOWED_DOC)
    assert result["ok"] is False
    assert "Windows" in result["error"]


class FakeMCP:
    """Minimal stand-in for FastMCP: records @mcp.tool() registrations."""

    def __init__(self):
        self.tools: dict = {}

    def tool(self):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorator


def test_register_binds_twelve_tools():
    mcp = FakeMCP()
    adapters = register(mcp)
    assert set(mcp.tools) == {
        "excel_open_workbook",
        "excel_list_sheets",
        "excel_read_range",
        "excel_write_cell",
        "excel_save",
        "word_open_document",
        "word_read_text",
        "word_append_text",
        "word_save",
        "ppt_open_presentation",
        "ppt_list_slides",
        "ppt_save",
    }
    assert set(adapters) == {"excel", "word", "ppt"}
