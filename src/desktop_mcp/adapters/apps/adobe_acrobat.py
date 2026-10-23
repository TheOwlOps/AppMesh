"""Adobe Acrobat adapter — catalog id ``adobe-acrobat``, COM strategy.

Acrobat's Interapplication Communication (IAC) API is COM-based with
ProgID ``AcroExch.App`` — but unlike Word/Excel it has **no**
``Documents`` collection on the application object. The real way to
open a file through IAC is to create an ``AcroExch.AVDoc`` object and
call ``AVDoc.Open(path, "")`` on it, then drive the viewer through
``AcroExch.AVPageView`` / ``PDDoc`` objects.

This adapter first attempts the generic collection route,
``ComApp("AcroExch.App").open_via("Documents", path)`` (which succeeds
against COM bridges/shims that do expose such a collection, and in
tests with a fake factory). When that route errors — as it will
against a stock Acrobat install — it falls back to the reliable
route: launching the detected ``Acrobat.exe`` with the file path as
its argument, which is exactly how double-clicking the PDF opens it
in Acrobat.

The path must pass ``core.check_path_allowed`` *before* either route
is attempted; a disallowed path is rejected immediately and neither
COM nor the launcher is touched.
"""
from __future__ import annotations

import subprocess

from ... import apps_catalog
from ...core import audit, check_path_allowed, err, ok
from ._common import ComApp, catalog_launch, catalog_status

APP_ID = "adobe-acrobat"
PROG_ID = "AcroExch.App"

# Module-level COM client (lazy: nothing is dispatched until a tool
# actually uses it). Tests inject a fake factory either by replacing
# this instance with ``ComApp(PROG_ID, factory=...)`` or by setting
# the module-level ``_com_factory`` below.
_com_app = ComApp(PROG_ID)

# Injectable COM factory for tests; ``None`` means "use ``_com_app``".
_com_factory = None


def _com_client() -> ComApp:
    """Return the COM client to use, honouring an injected factory."""
    if _com_factory is not None:
        return ComApp(PROG_ID, factory=_com_factory)
    return _com_app


def _launch_with_file(exe_path: str, file_path: str):
    """Spawn *exe_path* with *file_path* as its only argument.

    Factored out at module level so tests can monkeypatch it instead
    of really starting Acrobat.
    """
    return subprocess.Popen([str(exe_path), str(file_path)], close_fds=True)


def _open_file(path: str) -> dict:
    """Open *path* in Acrobat: COM ``Documents`` route, else exe launch."""
    # Allowlist first — before any COM dispatch or process spawn.
    try:
        checked = check_path_allowed(path)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)

    # Route 1: COM via a Documents collection (see module docstring —
    # stock AcroExch.App has none; the real IAC API is AVDoc.Open).
    com_result = _com_client().open_via("Documents", str(checked))
    if com_result.get("ok"):
        result = dict(com_result)
        result.update(app_id=APP_ID, via="com")
        return result

    # Route 2 (fallback): launch the detected Acrobat.exe with the file.
    try:
        det = apps_catalog.detect_app(APP_ID)
        if not det.get("ok"):
            return err(
                f"COM open failed ({com_result.get('error')}); "
                f"fallback detection failed: {det.get('error')}")
        if not det.get("installed") or not det.get("path"):
            return err(
                f"COM open failed ({com_result.get('error')}) and the "
                "Acrobat executable was not found on this machine, so "
                "the file could not be opened.")
        proc = _launch_with_file(det["path"], str(checked))
        pid = getattr(proc, "pid", None)
        audit("adobe_acrobat_open_file",
              {"app_id": APP_ID, "path": str(checked),
               "exe": det["path"], "pid": pid, "via": "exe_launch"})
        return ok(app_id=APP_ID, opened=True, via="exe_launch",
                  path=str(checked), exe=det["path"], pid=pid)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def register(mcp):
    """Register the adobe_acrobat_* tools on a FastMCP server."""

    @mcp.tool()
    def adobe_acrobat_status() -> dict:
        """Report whether Adobe Acrobat is installed on this machine,
        where its executable lives, and how it is automated (catalog
        strategy "com", ProgID "AcroExch.App")."""
        return catalog_status(APP_ID)

    @mcp.tool()
    def adobe_acrobat_launch() -> dict:
        """Launch Adobe Acrobat (no document). Fails with a clear
        error when Acrobat is not installed."""
        return catalog_launch(APP_ID)

    @mcp.tool()
    def adobe_acrobat_open_file(path: str) -> dict:
        """Open a PDF (or other Acrobat-supported file) in Adobe Acrobat.

        Route: the path must first pass the desktop-mcp path allowlist.
        The adapter then tries COM — ``AcroExch.App`` via a ``Documents``
        collection ``Open(path)``. Note that a stock ``AcroExch.App``
        object has no ``Documents`` collection (the real IAC API is an
        ``AcroExch.AVDoc`` object's ``Open(path, "")``), so when the COM
        route errors the adapter falls back to launching the detected
        ``Acrobat.exe`` with the file as its argument. Returns
        ``via="com"`` or ``via="exe_launch"`` so the caller knows which
        route opened the file.
        """
        return _open_file(path)

    return mcp
