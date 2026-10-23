"""Adobe Photoshop adapter — COM automation with an exe-launch fallback.

Photoshop is a ``com`` catalog app (ProgID ``Photoshop.Application``):
on Windows it exposes a full COM / ExtendScript object model, so the
preferred route for opening a file is
``Photoshop.Application.Documents.Open(path)`` — via
:class:`desktop_mcp.adapters.apps._common.ComApp`.

COM is not always reachable (Photoshop not yet running, COM busy, or
pywin32 missing), so :func:`_open_file` falls back to launching the
detected Photoshop executable directly with the file as its argument.
Every result carries ``"route": "com"`` or ``"route": "exe"`` so callers
know which path was taken.

Safety contract (shared by the whole project):
- The file path must pass :func:`desktop_mcp.core.check_path_allowed`
  *before* any COM call or process launch is attempted; a disallowed
  path returns an ``err`` dict and nothing else is touched.
- All tools return ``core.ok`` / ``core.err`` dicts and never raise
  across the MCP boundary.

Testability: COM access goes through the module-level factory
:func:`_make_com_app`, detection through :func:`_detect_exe` (which
wraps ``apps_catalog.detect_app``) and process spawning through
:func:`_spawn_exe_with_file`. Tests monkeypatch those three seams to
inject fakes — no real Photoshop or COM is needed.
"""
from __future__ import annotations

import subprocess

from ... import apps_catalog
from ...core import audit, check_path_allowed, err, ok
from . import _common

APP_ID = "photoshop"
PROG_ID = "Photoshop.Application"


# ---------------------------------------------------------------------------
# Injectable seams (monkeypatched by tests)
# ---------------------------------------------------------------------------

def _make_com_app():
    """Create the COM wrapper for Photoshop.

    Factored into a module-level factory so tests can inject a fake
    ``ComApp`` (an object with ``open_via(collection, path)`` and,
    for export, ``app()``) without touching real COM.
    """
    return _common.ComApp(PROG_ID)


def _detect_exe() -> str | None:
    """Return the detected Photoshop executable path, or ``None``.

    Thin wrapper over ``apps_catalog.detect_app("photoshop")`` so tests
    can monkeypatch either this helper or the catalog function itself.
    """
    det = apps_catalog.detect_app(APP_ID)
    if not det.get("ok") or not det.get("installed"):
        return None
    return det.get("path")


def _spawn_exe_with_file(exe_path: str, file_path: str):
    """Launch *exe_path* with *file_path* as its argument (detached).

    Factored out for testability: tests monkeypatch this to capture the
    launch instead of starting a real process. Returns the ``Popen``
    object (only its ``pid`` is used).
    """
    return subprocess.Popen([str(exe_path), str(file_path)], close_fds=True)


# ---------------------------------------------------------------------------
# Core logic (also directly testable without the MCP layer)
# ---------------------------------------------------------------------------

def _open_file(path: str) -> dict:
    """Open *path* in Photoshop, COM first, exe launch as fallback."""
    # 1. Allowlist gate — before any COM or launch attempt.
    try:
        checked = check_path_allowed(path)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)

    # 2. Preferred route: COM via Documents.Open.
    com_error: str | None = None
    try:
        com_app = _make_com_app()
        result = com_app.open_via("Documents", str(checked))
    except Exception as exc:  # noqa: BLE001
        result = err(exc)
    if isinstance(result, dict) and result.get("ok"):
        result = dict(result)
        result["route"] = "com"
        return result
    if isinstance(result, dict):
        com_error = result.get("error")
    else:  # defensive: a fake/foreign wrapper returned a non-dict
        com_error = f"Unexpected COM result: {result!r}"

    # 3. Fallback route: launch the detected exe with the file.
    try:
        exe = _detect_exe()
        if not exe:
            return err(
                f"Photoshop COM open failed ({com_error}) and the "
                "Photoshop executable was not found on this machine, "
                "so the exe fallback cannot run either."
            )
        proc = _spawn_exe_with_file(exe, str(checked))
        pid = getattr(proc, "pid", None)
        audit("photoshop_open_file_exe",
              {"path": str(checked), "exe": str(exe), "pid": pid,
               "com_error": com_error})
        return ok(opened=True, path=str(checked), route="exe",
                  exe=str(exe), pid=pid, com_error=com_error)
    except Exception as exc:  # noqa: BLE001
        return err(exc)


def _export_active_as_png(path: str) -> dict:
    """Export the active Photoshop document to *path* as PNG via COM.

    Guarded COM call: reaches the application through the same
    injectable :func:`_make_com_app` factory, then uses the active
    document's ``SaveAs`` method. Returns a clear ``err`` dict when
    there is no active document or the document cannot save.
    """
    try:
        checked = check_path_allowed(path)
    except Exception as exc:  # noqa: BLE001
        return err(exc)
    try:
        com_app = _make_com_app()
        # _common.ComApp exposes the raw COM object via .app(); a fake
        # wrapper may instead carry the app surface directly.
        app_obj = com_app.app() if hasattr(com_app, "app") else com_app
        doc = getattr(app_obj, "ActiveDocument", None)
        if doc is None:
            return err("No active Photoshop document to export — "
                       "open a document in Photoshop first.")
        save_as = getattr(doc, "SaveAs", None)
        if not callable(save_as):
            return err("The active Photoshop document exposes no "
                       "SaveAs method, so it cannot be exported.")
        save_as(str(checked))
        audit("photoshop_export_active_as_png", {"path": str(checked)})
        name = getattr(doc, "Name", None)
        return ok(exported=True, path=str(checked), route="com",
                  document=str(name) if name else None)
    except Exception as exc:  # noqa: BLE001
        return err(exc)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(mcp):
    """Register the ``photoshop_*`` tools on a FastMCP server.

    Binds ``photoshop_status``, ``photoshop_launch``,
    ``photoshop_open_file`` and ``photoshop_export_active_as_png``.
    Returns the catalog app id for testability / logging.
    """

    @mcp.tool()
    def photoshop_status() -> dict:
        """Check whether Adobe Photoshop is installed on this machine.

        Returns the catalog detection result for Photoshop: whether it
        was found, the executable path and how it was detected, plus
        its automation strategy ("com") and COM ProgID
        ("Photoshop.Application"). Use this before trying to open a
        file so you know Photoshop is available at all.
        """
        return _common.catalog_status(APP_ID)

    @mcp.tool()
    def photoshop_launch() -> dict:
        """Launch Adobe Photoshop (without opening any file).

        Starts the installed Photoshop executable found by the catalog.
        Fails with a clear error when Photoshop is not installed. The
        launch is written to the audit log.
        """
        return _common.catalog_launch(APP_ID)

    @mcp.tool()
    def photoshop_open_file(path: str) -> dict:
        """Open an image/design file in Adobe Photoshop.

        The path must be inside an allowed root (see the project
        allowlist); a disallowed path is rejected before anything is
        attempted. Opening tries Photoshop's COM interface first
        (Documents.Open) and falls back to launching the Photoshop
        executable with the file as its argument. The result includes
        "route": "com" or "route": "exe" telling you which path was
        taken, plus the opened document name on the COM route.
        """
        return _open_file(path)

    @mcp.tool()
    def photoshop_export_active_as_png(path: str) -> dict:
        """Export the currently active Photoshop document as a PNG file.

        Saves the document currently active in Photoshop to the given
        allow-listed .png path via COM (the document's SaveAs method).
        Fails with a clear error when no document is active or COM is
        unavailable. The export is written to the audit log.
        """
        return _export_active_as_png(path)

    return APP_ID
