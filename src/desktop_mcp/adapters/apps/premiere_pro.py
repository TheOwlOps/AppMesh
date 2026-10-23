"""Adobe Premiere Pro adapter — plugin strategy, exe project-open only.

Catalog entry: ``premiere-pro``, strategy ``plugin``, executable
``Adobe Premiere Pro.exe``, no COM ProgID. The catalog notes say:
"ExtendScript / UXP panels; project is XML-ish .prproj (gzip) —
scripting preferred."

Honest scope: Premiere Pro has **no real CLI for scripting** and no
external automation API an outside process can drive — ExtendScript
(and UXP) scripts only run *inside* the app. So this adapter does not
pretend to script Premiere. What it can do reliably is:

- ``premiere_pro_status``       — is Premiere Pro installed? (catalog
  detection, via ``_common.catalog_status``),
- ``premiere_pro_launch``       — launch the installed app (catalog
  launch, via ``_common.catalog_launch``),
- ``premiere_pro_open_project`` — open a ``.prproj`` project in the
  app by launching the detected executable with the project path as
  its argument.

Safety: the project path must pass ``core.check_path_allowed`` (it has
to live under an allowed root) *before* anything is launched, and every
tool returns a plain ``core.ok`` / ``core.err`` dict — tools never
raise across the MCP boundary.
"""
from __future__ import annotations

import contextlib
import subprocess
from pathlib import Path

from ... import apps_catalog
from ...apps_catalog import detect_app  # noqa: F401 — re-exported in this
# module's namespace so tests (and callers) can monkeypatch
# ``premiere_pro.detect_app`` and have status / launch / open honour it.
from ...core import audit, check_path_allowed, err, ok
from . import _common

APP_ID = "premiere-pro"


# --------------------------------------------------------------- detection

@contextlib.contextmanager
def _using_module_detect():
    """Run a block with ``apps_catalog.detect_app`` swapped for this
    module's ``detect_app`` global when the two differ.

    ``_common.catalog_status`` / ``_common.catalog_launch`` resolve
    detection through the ``apps_catalog`` module at call time, so
    swapping that attribute for the duration of the call makes a
    ``detect_app`` patched into *this* module's namespace (the test
    seam) authoritative, exactly as it already is for
    :func:`premiere_pro_open_project`, which calls the module-level
    name directly.
    """
    original = apps_catalog.detect_app
    module_detect = globals().get("detect_app")
    if module_detect is not None and module_detect is not original:
        apps_catalog.detect_app = module_detect  # type: ignore[misc]
        try:
            yield
        finally:
            apps_catalog.detect_app = original  # type: ignore[misc]
    else:
        yield


# --------------------------------------------------------------------- argv

def build_open_command(exe, project_path) -> list[str]:
    """Build the argv used to open *project_path* in Premiere Pro. Pure.

    Premiere Pro takes the project to open as a plain positional
    argument, so the command is exactly ``[exe, project_path]`` — no
    flags, no shell quoting (the list is passed straight to
    ``subprocess.Popen``). Tests assert this argv exactly.
    """
    return [str(exe), str(project_path)]


# ------------------------------------------------------------------ process

def _spawn(argv: list[str]):
    """Start *argv* as a detached process. Factored out for testability:
    tests monkeypatch this instead of really launching Premiere Pro.
    Returns the ``Popen`` object (only its ``pid`` is used)."""
    return subprocess.Popen([str(a) for a in argv], close_fds=True)


# -------------------------------------------------------------------- tools

def premiere_pro_status() -> dict:
    """Check whether Adobe Premiere Pro is installed on this machine.

    Returns the catalog detection result for Premiere Pro: whether it
    was found, the executable path and how it was detected, plus its
    catalog automation strategy ("plugin" — ExtendScript / UXP, which
    only runs inside the app).
    """
    try:
        with _using_module_detect():
            return _common.catalog_status(APP_ID)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def premiere_pro_launch() -> dict:
    """Launch Adobe Premiere Pro (without opening any project).

    Starts the installed Premiere Pro executable found by the catalog.
    Fails with a clear error dict when Premiere Pro is not installed.
    The launch is written to the audit log.
    """
    try:
        with _using_module_detect():
            return _common.catalog_launch(APP_ID)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def premiere_pro_open_project(path: str) -> dict:
    """Open a Premiere Pro project (.prproj) in Adobe Premiere Pro.

    `path` is the .prproj project file to open. It must live under an
    allowed root (see DESKTOP_MCP_ALLOWED_PATHS); a disallowed path is
    rejected with an error dict before anything is launched. The
    project is opened by launching the detected Premiere Pro
    executable with the project path as its argument, and the launch
    is written to the audit log. Fails with a clear error dict when
    Premiere Pro is not installed.

    Note: Premiere Pro has no external scripting CLI — scripting
    inside Premiere is ExtendScript-only (ExtendScript / UXP panels
    run inside the app). This tool therefore only *opens the project
    in the app*; it cannot run scripts, edit the timeline, or export
    anything.
    """
    try:
        if not path or not str(path).strip():
            return err("`path` must be a non-empty .prproj project path.")
        checked = check_path_allowed(path)
        if Path(checked).suffix.lower() != ".prproj":
            return err(
                f"Premiere Pro project files must end with .prproj: {checked}")
        det = detect_app(APP_ID)
        if not det.get("ok"):
            return det
        exe = det.get("path")
        if not det.get("installed") or not exe:
            return err(
                "Adobe Premiere Pro is not installed (its executable "
                "'Adobe Premiere Pro.exe' was not found on this machine), "
                "so the project cannot be opened.")
        argv = build_open_command(str(exe), str(checked))
        proc = _spawn(argv)
        pid = getattr(proc, "pid", None)
        audit("premiere_pro_open_project",
              {"app_id": APP_ID, "path": str(checked),
               "exe": str(exe), "argv": argv, "pid": pid})
        return ok(app_id=APP_ID, opened=True, launched=True,
                  path=str(checked), project=str(checked),
                  exe=str(exe), argv=argv, pid=pid)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


# ----------------------------------------------------------------- register

def register(mcp):
    """Register the premiere_pro_* tools on a FastMCP server.

    Binds ``premiere_pro_status``, ``premiere_pro_launch`` and
    ``premiere_pro_open_project``. Returns the tool names.
    """
    mcp.tool()(premiere_pro_status)
    mcp.tool()(premiere_pro_launch)
    mcp.tool()(premiere_pro_open_project)
    return [
        "premiere_pro_status",
        "premiere_pro_launch",
        "premiere_pro_open_project",
    ]
