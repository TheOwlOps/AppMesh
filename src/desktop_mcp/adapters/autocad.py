"""AutoCAD adapter — controls a running/new AutoCAD instance via COM (pyautocad).

AutoCAD exposes a full COM/ActiveX automation API on Windows, so this
adapter is the "deep" tier of desktop-mcp: no clicking, everything goes
through ``pyautocad`` (a thin wrapper over ``win32com``).

Connection is LAZY: constructing :class:`AutoCADAdapter` never touches
AutoCAD; the first tool call connects (starting AutoCAD if needed via
``Autocad(create_if_not_exists=True)``).

Every public method returns a plain dict via :func:`desktop_mcp.core.ok` /
:func:`desktop_mcp.core.err` and NEVER raises across the MCP boundary —
on non-Windows machines or when ``pyautocad`` is not installed, methods
return ``{"ok": False, "error": "...clear message..."}`` instead.

A factory can be injected (``AutoCADAdapter(acad_factory=...)``) for
tests: the factory is a zero-arg callable returning an object with the
pyautocad surface (``.app``, ``.doc``, ``.model``, ``.iter_objects()``).
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from ..core import IS_WINDOWS, audit, check_path_allowed, err, ok


def _point(x: float, y: float, z: float = 0.0):
    """Build a pyautocad ``APoint`` when pyautocad is available, else a tuple.

    Real pyautocad/COM requires an APoint (a VARIANT array of doubles);
    fakes and tests accept a plain ``(x, y, z)`` tuple, which is what we
    fall back to whenever the import is unavailable.
    """
    try:
        from pyautocad import APoint

        return APoint(x, y, z)
    except Exception:
        return (float(x), float(y), float(z))


def _guard(obj: Any, *names: str):
    """Return the first non-None attribute among *names*, else None.

    COM objects raise on missing/prohibited attributes surprisingly often
    (e.g. reading the active document when none is open), so every read
    of a COM attribute in this module goes through a guard.
    """
    for name in names:
        try:
            value = getattr(obj, name, None)
        except Exception:
            continue
        if value is not None:
            return value
    return None


class AutoCADAdapter:
    """Lazy COM connection to AutoCAD with dict-returning tool methods."""

    def __init__(self, acad_factory: Optional[Callable[[], Any]] = None):
        self._acad_factory = acad_factory
        self._acad_instance: Any = None

    # -- connection -----------------------------------------------------

    def _acad(self) -> Any:
        """Return the (cached) pyautocad object, connecting on first use.

        Raises RuntimeError with a clear, user-readable message when
        AutoCAD cannot be reached; callers convert that to ``core.err``.
        An injected factory bypasses the Windows/pyautocad requirements
        entirely (that is what makes the adapter testable anywhere).
        """
        if self._acad_instance is not None:
            return self._acad_instance
        if self._acad_factory is not None:
            self._acad_instance = self._acad_factory()
            return self._acad_instance
        if not IS_WINDOWS:
            raise RuntimeError(
                "AutoCAD adapter requires Windows (COM automation is "
                "Windows-only); this machine is not running Windows."
            )
        try:
            from pyautocad import Autocad
        except ImportError as exc:
            raise RuntimeError(
                "pyautocad is not installed. On the Windows machine run: "
                "pip install \"desktop-mcp[windows]\" (installs pywin32 + pyautocad)."
            ) from exc
        try:
            self._acad_instance = Autocad(create_if_not_exists=True)
        except Exception as exc:  # COM errors surface as various types
            raise RuntimeError(f"Could not connect to AutoCAD via COM: {exc}") from exc
        return self._acad_instance

    def _doc(self, acad: Any) -> Any:
        """Return the active document or raise a clear RuntimeError."""
        try:
            doc = getattr(acad, "doc", None)
        except Exception as exc:
            raise RuntimeError(f"AutoCAD has no active document: {exc}") from exc
        if doc is None:
            raise RuntimeError(
                "AutoCAD has no active document — open or create a drawing first."
            )
        return doc

    # -- tools ----------------------------------------------------------

    def status(self) -> dict:
        """Report whether AutoCAD is reachable and the active document name."""
        try:
            acad = self._acad()
        except Exception as exc:
            return err(exc)
        doc_name = None
        try:
            doc = getattr(acad, "doc", None)
            if doc is not None:
                doc_name = _guard(doc, "Name", "name")
        except Exception:
            doc_name = None
        if doc_name is None:
            # pyautocad also exposes the active document name directly.
            try:
                doc_name = getattr(acad, "docname", None) or None
            except Exception:
                doc_name = None
        return ok(connected=True, document=doc_name)

    def open_drawing(self, path: str) -> dict:
        """Open an existing .dwg drawing at *path* (must be allow-listed)."""
        try:
            resolved = check_path_allowed(path)
            acad = self._acad()
            documents = acad.app.Documents
            doc = documents.Open(str(resolved))
            audit("autocad_open_drawing", {"path": str(resolved)})
            return ok(path=str(resolved), document=_guard(doc, "Name", "name"))
        except Exception as exc:
            return err(exc)

    def new_drawing(self) -> dict:
        """Create a new blank drawing and make it the active document."""
        try:
            acad = self._acad()
            documents = acad.app.Documents
            add = getattr(documents, "Add", None)
            doc = add() if callable(add) else None
            if doc is None:
                doc = self._doc(acad)
            audit("autocad_new_drawing", {"document": _guard(doc, "Name", "name")})
            return ok(document=_guard(doc, "Name", "name"))
        except Exception as exc:
            return err(exc)

    def list_entities(self, limit: int = 200, type_filter: str = "") -> dict:
        """List entities in the active drawing's model space.

        ``type_filter`` is an AutoCAD object name such as ``"Line"``,
        ``"Circle"`` or ``"Text"``; empty means all types. At most
        ``limit`` entities are returned.
        """
        try:
            acad = self._acad()
            entities = []
            iterator = acad.iter_objects(type_filter or None)
            for obj in iterator:
                if len(entities) >= int(limit):
                    break
                object_name = _guard(obj, "ObjectName", "object_name")
                entities.append(
                    {
                        "handle": _guard(obj, "Handle", "handle"),
                        "object_name": object_name,
                        "type": object_name,
                        "layer": _guard(obj, "Layer", "layer"),
                    }
                )
            return ok(entities=entities, count=len(entities))
        except Exception as exc:
            return err(exc)

    def draw_line(self, x1: float, y1: float, x2: float, y2: float, z: float = 0.0) -> dict:
        """Draw a straight line segment from (x1, y1, z) to (x2, y2, z)."""
        try:
            acad = self._acad()
            start, end = _point(x1, y1, z), _point(x2, y2, z)
            entity = acad.model.AddLine(start, end)
            audit(
                "autocad_draw_line",
                {"start": [x1, y1, z], "end": [x2, y2, z]},
            )
            return ok(handle=_guard(entity, "Handle", "handle"))
        except Exception as exc:
            return err(exc)

    def draw_circle(self, cx: float, cy: float, radius: float, z: float = 0.0) -> dict:
        """Draw a circle centred at (cx, cy, z) with the given radius."""
        try:
            acad = self._acad()
            entity = acad.model.AddCircle(_point(cx, cy, z), float(radius))
            audit(
                "autocad_draw_circle",
                {"center": [cx, cy, z], "radius": float(radius)},
            )
            return ok(handle=_guard(entity, "Handle", "handle"))
        except Exception as exc:
            return err(exc)

    def add_text(self, x: float, y: float, text: str, height: float = 2.5) -> dict:
        """Add a single-line text entity at (x, y) with the given height."""
        try:
            acad = self._acad()
            entity = acad.model.AddText(str(text), _point(x, y), float(height))
            audit(
                "autocad_add_text",
                {"at": [x, y], "text": str(text), "height": float(height)},
            )
            return ok(handle=_guard(entity, "Handle", "handle"))
        except Exception as exc:
            return err(exc)

    def list_layers(self) -> dict:
        """List the layer names defined in the active drawing."""
        try:
            acad = self._acad()
            doc = self._doc(acad)
            names = []
            for layer in doc.Layers:
                name = _guard(layer, "Name", "name")
                if name is not None:
                    names.append(name)
            return ok(layers=names, count=len(names))
        except Exception as exc:
            return err(exc)

    def create_layer(self, name: str) -> dict:
        """Create a new layer named *name* in the active drawing."""
        try:
            acad = self._acad()
            doc = self._doc(acad)
            layer = doc.Layers.Add(str(name))
            audit("autocad_create_layer", {"layer": str(name)})
            return ok(layer=_guard(layer, "Name", "name") or str(name))
        except Exception as exc:
            return err(exc)

    def save(self) -> dict:
        """Save the active drawing to its current file (Ctrl+S equivalent)."""
        try:
            acad = self._acad()
            doc = self._doc(acad)
            doc.Save()
            audit("autocad_save", {"document": _guard(doc, "Name", "name")})
            return ok(document=_guard(doc, "Name", "name"))
        except Exception as exc:
            return err(exc)

    def run_command(self, command: str) -> dict:
        """Send a raw AutoCAD command line to the active document.

        POWERFUL / DANGEROUS: this is the equivalent of typing at the
        AutoCAD command prompt — a command string can run AutoLISP,
        load scripts, overwrite or erase the drawing, or shell out via
        other commands. It exists as an escape hatch for operations the
        typed tools do not cover. Every invocation is written to the
        audit log. Prefer the typed tools (draw_line, add_text, ...)
        whenever one fits.
        """
        try:
            acad = self._acad()
            doc = self._doc(acad)
            audit("autocad_run_command", {"command": str(command)})
            doc.SendCommand(str(command) + "\n")
            return ok(command=str(command))
        except Exception as exc:
            return err(exc)


def register(mcp) -> AutoCADAdapter:
    """Register all ``autocad_*`` tools on the FastMCP server.

    One shared adapter instance backs every tool (so the COM connection
    is opened once and reused). Returns the adapter for testability.
    """
    adapter = AutoCADAdapter()

    @mcp.tool()
    def autocad_status() -> dict:
        """Check whether AutoCAD is reachable via COM and which drawing is active."""
        return adapter.status()

    @mcp.tool()
    def autocad_open_drawing(path: str) -> dict:
        """Open an existing AutoCAD drawing (.dwg) from an allow-listed path."""
        return adapter.open_drawing(path)

    @mcp.tool()
    def autocad_new_drawing() -> dict:
        """Create a new blank AutoCAD drawing and make it active."""
        return adapter.new_drawing()

    @mcp.tool()
    def autocad_list_entities(limit: int = 200, type_filter: str = "") -> dict:
        """List entities in the active drawing (handle, type, layer).

        type_filter is an AutoCAD object name like "Line", "Circle" or
        "Text"; leave empty to list all types. At most `limit` are returned.
        """
        return adapter.list_entities(limit=limit, type_filter=type_filter)

    @mcp.tool()
    def autocad_draw_line(x1: float, y1: float, x2: float, y2: float, z: float = 0.0) -> dict:
        """Draw a straight line in model space from (x1, y1, z) to (x2, y2, z)."""
        return adapter.draw_line(x1, y1, x2, y2, z=z)

    @mcp.tool()
    def autocad_draw_circle(cx: float, cy: float, radius: float, z: float = 0.0) -> dict:
        """Draw a circle in model space centred at (cx, cy, z) with `radius`."""
        return adapter.draw_circle(cx, cy, radius, z=z)

    @mcp.tool()
    def autocad_add_text(x: float, y: float, text: str, height: float = 2.5) -> dict:
        """Add a single-line text label at (x, y) with the given text height."""
        return adapter.add_text(x, y, text, height=height)

    @mcp.tool()
    def autocad_list_layers() -> dict:
        """List all layer names defined in the active drawing."""
        return adapter.list_layers()

    @mcp.tool()
    def autocad_create_layer(name: str) -> dict:
        """Create a new layer with the given name in the active drawing."""
        return adapter.create_layer(name)

    @mcp.tool()
    def autocad_save() -> dict:
        """Save the active drawing to its current file."""
        return adapter.save()

    @mcp.tool()
    def autocad_run_command(command: str) -> dict:
        """DANGEROUS escape hatch: send a raw command to AutoCAD's command line.

        Equivalent to typing at the AutoCAD prompt — can modify or erase
        the drawing and run AutoLISP/scripts. Audited. Prefer the typed
        autocad_* tools whenever one can do the job.
        """
        return adapter.run_command(command)

    return adapter
