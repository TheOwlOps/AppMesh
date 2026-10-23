"""Microsoft Office adapter — controls Excel, Word and PowerPoint via COM.

Office applications expose a full COM/ActiveX automation API on Windows
(``win32com.client.Dispatch("Excel.Application")`` etc.), so this adapter
is the same "deep" tier as AutoCAD: no clicking, everything goes through
the application's own object model.

Connection is LAZY per application: constructing an adapter never
touches Office; the first tool call dispatches the COM application
(starting it if needed) and caches the instance.

Every public method returns a plain dict via :func:`desktop_mcp.core.ok`
/ :func:`desktop_mcp.core.err` and NEVER raises across the MCP boundary
— on non-Windows machines or when ``pywin32`` is not installed, methods
return ``{"ok": False, "error": "...clear message..."}`` instead.

A factory can be injected (``ExcelAdapter(com_factory=...)``) for tests:
the factory is a zero-arg callable returning an object with the COM
application surface (``.Workbooks`` / ``.Documents`` / ``.Presentations``).
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from ..core import IS_WINDOWS, audit, check_path_allowed, err, ok


def _guard(obj: Any, *names: str):
    """Return the first non-None attribute among *names*, else None.

    COM objects raise on missing/prohibited attributes surprisingly often,
    so every read of a COM attribute in this module goes through a guard
    (same helper pattern as the AutoCAD adapter).
    """
    for name in names:
        try:
            value = getattr(obj, name, None)
        except Exception:
            continue
        if value is not None:
            return value
    return None


def _json_safe(value: Any) -> Any:
    """Convert a COM cell/slide value into something JSON-serialisable."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    # datetime/date and friends -> ISO string; anything else -> str().
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        try:
            return isoformat()
        except Exception:
            pass
    return str(value)


def _normalize_range_value(value: Any) -> list:
    """Normalize a COM ``Range.Value`` into a list of row lists.

    Excel returns a scalar for a single cell, a tuple of tuples for a
    multi-cell range (and a flat tuple for some single-row/column reads).
    The normalized shape is always ``[[cell, ...], ...]`` with JSON-safe
    cell values, so the agent sees one predictable structure.
    """
    if isinstance(value, (list, tuple)):
        if len(value) == 0:
            return []
        if all(isinstance(row, (list, tuple)) for row in value):
            return [[_json_safe(cell) for cell in row] for row in value]
        return [[_json_safe(cell) for cell in value]]
    return [[_json_safe(value)]]


class OfficeAdapter:
    """Base class: lazy COM connection to one Office application."""

    app_label = "Office"

    def __init__(self, prog_id: str, com_factory: Optional[Callable[[], Any]] = None):
        self.prog_id = prog_id
        self._com_factory = com_factory
        self._app_instance: Any = None
        self._doc: Any = None  # the opened workbook / document / presentation

    # -- connection -----------------------------------------------------

    def _app(self) -> Any:
        """Return the (cached) COM application, dispatching on first use.

        Raises RuntimeError with a clear, user-readable message when the
        application cannot be reached; callers convert that to
        ``core.err``. An injected factory bypasses the Windows/pywin32
        requirements entirely (that is what makes the adapter testable
        anywhere).
        """
        if self._app_instance is not None:
            return self._app_instance
        if self._com_factory is not None:
            self._app_instance = self._com_factory()
            return self._app_instance
        if not IS_WINDOWS:
            raise RuntimeError(
                f"{self.app_label} adapter requires Windows (COM automation "
                "is Windows-only); this machine is not running Windows."
            )
        try:
            import win32com.client
        except ImportError as exc:
            raise RuntimeError(
                "pywin32 is not installed. On the Windows machine run: "
                "pip install \"desktop-mcp[windows]\" (installs pywin32)."
            ) from exc
        try:
            self._app_instance = win32com.client.Dispatch(self.prog_id)
        except Exception as exc:  # COM errors surface as various types
            raise RuntimeError(
                f"Could not connect to {self.app_label} via COM "
                f"({self.prog_id}): {exc}"
            ) from exc
        return self._app_instance

    def _require_doc(self, what: str) -> Any:
        """Return the opened document object or raise a clear RuntimeError."""
        if self._doc is None:
            raise RuntimeError(
                f"No {what} is open — open one first with the matching "
                f"{self.app_label.lower()}_open_* tool."
            )
        return self._doc

    def _save_doc(self, tool_name: str) -> dict:
        """Shared save() implementation for the three applications."""
        try:
            doc = self._require_doc("document")
            doc.Save()
            name = _guard(doc, "Name", "name")
            audit(tool_name, {"document": name})
            return ok(document=name)
        except Exception as exc:
            return err(exc)


class ExcelAdapter(OfficeAdapter):
    """Excel via COM: workbooks, worksheets and cell ranges."""

    app_label = "Excel"

    def __init__(self, com_factory: Optional[Callable[[], Any]] = None):
        super().__init__("Excel.Application", com_factory=com_factory)

    @property
    def _wb(self) -> Any:
        return self._doc

    @_wb.setter
    def _wb(self, value: Any) -> None:
        self._doc = value

    def _sheet(self, sheet: str):
        """Return the named worksheet of the open workbook."""
        wb = self._require_doc("workbook")
        worksheets = getattr(wb, "Worksheets", None)
        if worksheets is None:
            raise RuntimeError("The open workbook exposes no Worksheets collection.")
        if callable(worksheets):
            return worksheets(sheet)
        for ws in worksheets:
            if _guard(ws, "Name", "name") == sheet:
                return ws
        raise RuntimeError(f"Worksheet not found: {sheet!r}")

    # -- tools ----------------------------------------------------------

    def open_workbook(self, path: str) -> dict:
        """Open an existing workbook at *path* (must be allow-listed)."""
        try:
            resolved = check_path_allowed(path)
            app = self._app()
            wb = app.Workbooks.Open(str(resolved))
            self._wb = wb
            audit("excel_open_workbook", {"path": str(resolved)})
            return ok(path=str(resolved), workbook=_guard(wb, "Name", "name"))
        except Exception as exc:
            return err(exc)

    def list_sheets(self) -> dict:
        """List worksheet names in the open workbook."""
        try:
            wb = self._require_doc("workbook")
            worksheets = getattr(wb, "Worksheets", None)
            if worksheets is None:
                raise RuntimeError("The open workbook exposes no Worksheets collection.")
            names = []
            for ws in worksheets:
                name = _guard(ws, "Name", "name")
                if name is not None:
                    names.append(name)
            count = _guard(worksheets, "Count", "count")
            if not isinstance(count, int):
                count = len(names)
            return ok(sheets=names, count=count)
        except Exception as exc:
            return err(exc)

    def read_range(self, sheet: str, address: str) -> dict:
        """Read a cell range (e.g. "A1" or "A1:C10") from a worksheet.

        Returns ``values`` as a list of row lists (a single cell becomes
        ``[[value]]``), with cell values converted to JSON-safe types.
        """
        try:
            ws = self._sheet(sheet)
            value = ws.Range(address).Value
            return ok(
                sheet=sheet,
                address=address,
                values=_normalize_range_value(value),
            )
        except Exception as exc:
            return err(exc)

    def write_cell(self, sheet: str, cell: str, value: Any) -> dict:
        """Write *value* into a single cell (e.g. "B3") of a worksheet."""
        try:
            ws = self._sheet(sheet)
            ws.Range(cell).Value = value
            audit("excel_write_cell", {"sheet": sheet, "cell": cell, "value": _json_safe(value)})
            return ok(sheet=sheet, cell=cell, value=_json_safe(value))
        except Exception as exc:
            return err(exc)

    def save(self) -> dict:
        """Save the open workbook to its current file."""
        return self._save_doc("excel_save")


class WordAdapter(OfficeAdapter):
    """Word via COM: documents and their text content."""

    app_label = "Word"

    def __init__(self, com_factory: Optional[Callable[[], Any]] = None):
        super().__init__("Word.Application", com_factory=com_factory)

    # -- tools ----------------------------------------------------------

    def open_document(self, path: str) -> dict:
        """Open an existing document at *path* (must be allow-listed)."""
        try:
            resolved = check_path_allowed(path)
            app = self._app()
            doc = app.Documents.Open(str(resolved))
            self._doc = doc
            audit("word_open_document", {"path": str(resolved)})
            return ok(path=str(resolved), document=_guard(doc, "Name", "name"))
        except Exception as exc:
            return err(exc)

    def read_text(self, max_chars: int = 4000) -> dict:
        """Read the document's text, truncated to *max_chars* characters."""
        try:
            doc = self._require_doc("document")
            content = getattr(doc, "Content", None)
            text = _guard(content, "Text", "text") if content is not None else None
            if text is None:
                raise RuntimeError("The open document exposes no readable Content.Text.")
            text = str(text)
            limit = int(max_chars)
            truncated = len(text) > limit
            return ok(text=text[:limit], chars=len(text), truncated=truncated)
        except Exception as exc:
            return err(exc)

    def append_text(self, text: str) -> dict:
        """Append *text* at the very end of the document."""
        try:
            doc = self._require_doc("document")
            text = str(text)
            appended = False
            # Preferred: insert at a collapsed range at the document end.
            try:
                content = getattr(doc, "Content", None)
                end = _guard(content, "End", "end") if content is not None else None
                range_factory = getattr(doc, "Range", None)
                if end is not None and callable(range_factory):
                    rng = range_factory(int(end) - 1, int(end) - 1)
                    insert_after = getattr(rng, "InsertAfter", None)
                    if callable(insert_after):
                        insert_after(text)
                        appended = True
            except Exception:
                appended = False
            if not appended:
                # Fallback: rewrite Content.Text with the text appended.
                content = getattr(doc, "Content", None)
                if content is None:
                    raise RuntimeError("The open document exposes no Content to append to.")
                current = _guard(content, "Text", "text") or ""
                content.Text = str(current) + text
            audit("word_append_text", {"chars": len(text)})
            return ok(appended_chars=len(text))
        except Exception as exc:
            return err(exc)

    def save(self) -> dict:
        """Save the open document to its current file."""
        return self._save_doc("word_save")


class PowerPointAdapter(OfficeAdapter):
    """PowerPoint via COM: presentations and their slides."""

    app_label = "PowerPoint"

    def __init__(self, com_factory: Optional[Callable[[], Any]] = None):
        super().__init__("PowerPoint.Application", com_factory=com_factory)

    # -- tools ----------------------------------------------------------

    def open_presentation(self, path: str) -> dict:
        """Open an existing presentation at *path* (must be allow-listed)."""
        try:
            resolved = check_path_allowed(path)
            app = self._app()
            pres = app.Presentations.Open(str(resolved))
            self._doc = pres
            audit("ppt_open_presentation", {"path": str(resolved)})
            return ok(path=str(resolved), presentation=_guard(pres, "Name", "name"))
        except Exception as exc:
            return err(exc)

    def list_slides(self) -> dict:
        """List slides in the open presentation (index, name, layout)."""
        try:
            pres = self._require_doc("presentation")
            slides_col = getattr(pres, "Slides", None)
            if slides_col is None:
                raise RuntimeError("The open presentation exposes no Slides collection.")
            slides = []
            for position, slide in enumerate(slides_col, start=1):
                index = _guard(slide, "SlideIndex", "slide_index")
                slides.append(
                    {
                        "index": index if isinstance(index, int) else position,
                        "name": _guard(slide, "Name", "name"),
                        "layout": _json_safe(_guard(slide, "Layout", "layout")),
                    }
                )
            count = _guard(slides_col, "Count", "count")
            if not isinstance(count, int):
                count = len(slides)
            return ok(slides=slides, count=count)
        except Exception as exc:
            return err(exc)

    def save(self) -> dict:
        """Save the open presentation to its current file."""
        return self._save_doc("ppt_save")


def register(mcp) -> dict:
    """Register all ``excel_*`` / ``word_*`` / ``ppt_*`` tools on the server.

    One shared adapter per application backs its tools (so each COM
    connection is opened once and reused). Returns the adapters keyed by
    application for testability.
    """
    excel = ExcelAdapter()
    word = WordAdapter()
    ppt = PowerPointAdapter()

    @mcp.tool()
    def excel_open_workbook(path: str) -> dict:
        """Open an Excel workbook (.xlsx/.xlsm/.xls) from an allow-listed path."""
        return excel.open_workbook(path)

    @mcp.tool()
    def excel_list_sheets() -> dict:
        """List the worksheet names in the currently open Excel workbook."""
        return excel.list_sheets()

    @mcp.tool()
    def excel_read_range(sheet: str, address: str) -> dict:
        """Read cells from a worksheet, e.g. sheet="Sheet1", address="A1:C10".

        Returns values as a list of row lists; a single cell address like
        "B2" returns [[value]].
        """
        return excel.read_range(sheet, address)

    @mcp.tool()
    def excel_write_cell(sheet: str, cell: str, value) -> dict:
        """Write a value into one cell of a worksheet, e.g. cell="B3". Audited."""
        return excel.write_cell(sheet, cell, value)

    @mcp.tool()
    def excel_save() -> dict:
        """Save the currently open Excel workbook to its file."""
        return excel.save()

    @mcp.tool()
    def word_open_document(path: str) -> dict:
        """Open a Word document (.docx/.doc) from an allow-listed path."""
        return word.open_document(path)

    @mcp.tool()
    def word_read_text(max_chars: int = 4000) -> dict:
        """Read the open Word document's text (truncated to max_chars)."""
        return word.read_text(max_chars=max_chars)

    @mcp.tool()
    def word_append_text(text: str) -> dict:
        """Append text at the end of the open Word document. Audited."""
        return word.append_text(text)

    @mcp.tool()
    def word_save() -> dict:
        """Save the currently open Word document to its file."""
        return word.save()

    @mcp.tool()
    def ppt_open_presentation(path: str) -> dict:
        """Open a PowerPoint presentation (.pptx/.ppt) from an allow-listed path."""
        return ppt.open_presentation(path)

    @mcp.tool()
    def ppt_list_slides() -> dict:
        """List slides in the open presentation (index, name, layout)."""
        return ppt.list_slides()

    @mcp.tool()
    def ppt_save() -> dict:
        """Save the currently open PowerPoint presentation to its file."""
        return ppt.save()

    return {"excel": excel, "word": word, "ppt": ppt}
