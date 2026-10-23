"""Cinema 4D adapter — plugin strategy (Python SDK / c4dpy + renderer).

Cinema 4D has no COM ProgID (catalog ``prog_id`` is ``None``); its
automation surface, per the catalog notes ("Python SDK (c4d)
plugins/scripts; render via Commandline.exe"), is:

- Scripting: ``c4dpy.exe`` — the standalone Python interpreter that
  ships with Cinema 4D, with the ``c4d`` module importable:
  ``c4dpy.exe script.py`` runs a Cinema 4D Python script headlessly.
- Rendering: ``Commandline.exe`` — the headless renderer that sits in
  the same install folder as ``Cinema 4D.exe``:
  ``Commandline.exe -render scene.c4d -frame 0 10 -oimage out.png``
  (documented flags: ``-render <file>``, ``-frame <from> <to>``,
  ``-oimage <path>``, ``-oresolution <width> <height>``).

Both executables are derived from the detected GUI install (same
folder) and fall back to a PATH lookup. The argv builders are pure
functions (exe passed in as a parameter) so they are trivially
testable without a real install.

Every tool returns a ``core.ok`` / ``core.err`` dict and never raises.
Scene, output and script paths must pass ``core.check_path_allowed``
before anything is executed.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from ... import apps_catalog
from ...core import audit, check_path_allowed, err, ok
from . import _common
from ._common import catalog_launch, catalog_status, run_cli

APP_ID = "cinema-4d"

# Re-exported so tests / callers can patch detection in this module's
# namespace; the helpers below look it up through ``apps_catalog``
# (whose ``detect_app`` attribute is the same function).
detect_app = apps_catalog.detect_app

# Sibling executable names in a Cinema 4D install folder.
RENDER_EXE_NAME = "Commandline.exe"
C4DPY_EXE_NAME = "c4dpy.exe"

# PATH fallbacks for the sibling executables, most specific first.
_RENDER_NAMES = ("Commandline.exe", "commandline.exe", "Commandline", "commandline")
_C4DPY_NAMES = ("c4dpy.exe", "c4dpy")


# ------------------------------------------------------------- derivation

def _sibling_exe(gui_path: str, sibling_name: str) -> str:
    """Return *sibling_name* placed alongside the GUI exe at *gui_path*.

    Pure string/Path logic: no disk access, so it works for
    Windows-style paths on any OS and is trivially testable with a
    fake detected path.
    """
    p = Path(gui_path)
    parent = str(p.parent)
    if parent in (".", ""):
        return sibling_name
    return str(p.parent / sibling_name)


def _render_exe_from_gui(gui_path: str) -> str:
    """``.../Cinema 4D.exe`` -> ``.../Commandline.exe``."""
    return _sibling_exe(gui_path, RENDER_EXE_NAME)


def _c4dpy_exe_from_gui(gui_path: str) -> str:
    """``.../Cinema 4D.exe`` -> ``.../c4dpy.exe``."""
    return _sibling_exe(gui_path, C4DPY_EXE_NAME)


def _find_sibling_exe(sibling_name: str, path_names: tuple[str, ...]) -> str | None:
    """Locate a sibling exe: alongside the detected GUI exe, else on PATH."""
    try:
        det = apps_catalog.detect_app(APP_ID)
    except Exception:  # noqa: BLE001 — detection must never break the tool
        det = {}
    if det.get("ok") and det.get("installed") and det.get("path"):
        return _sibling_exe(str(det["path"]), sibling_name)
    for name in path_names:
        found = shutil.which(name)
        if found:
            return str(found)
    return None


def _find_render_exe() -> str | None:
    """Locate Commandline.exe (detected install first, then PATH)."""
    return _find_sibling_exe(RENDER_EXE_NAME, _RENDER_NAMES)


def _find_c4dpy_exe() -> str | None:
    """Locate c4dpy.exe (detected install first, then PATH)."""
    return _find_sibling_exe(C4DPY_EXE_NAME, _C4DPY_NAMES)


# --------------------------------------------------------------- builders

def build_render_command(render_exe: str, scene_path: str, output_path: str,
                         frame_range=None, resolution=None) -> list[str]:
    """Build the Commandline.exe render argv. Pure: no I/O, no checks.

    ``build_render_command(exe, scene, out)`` ->
    ``[exe, "-render", scene, "-oimage", out]``; with
    ``frame_range=(0, 10)`` a ``"-frame", "0", "10"`` group goes
    before ``-oimage``, and with ``resolution=(1920, 1080)`` a
    ``"-oresolution", "1920", "1080"`` group is appended.
    """
    argv = [str(render_exe), "-render", str(scene_path)]
    if frame_range is not None:
        frame_from, frame_to = frame_range
        argv += ["-frame", str(frame_from), str(frame_to)]
    argv += ["-oimage", str(output_path)]
    if resolution is not None:
        width, height = resolution
        argv += ["-oresolution", str(width), str(height)]
    return argv


def build_c4dpy_command(c4dpy_exe: str, script_path: str,
                        script_args=None) -> list[str]:
    """Build the c4dpy argv: ``[c4dpy_exe, script, *script_args]``. Pure."""
    argv = [str(c4dpy_exe), str(script_path)]
    if script_args:
        argv += [str(a) for a in script_args]
    return argv


# ---------------------------------------------------------------- actions

def render_scene(scene_path: str, output_path: str, frame_range=None,
                 resolution=None, runner=None) -> dict:
    """Render a .c4d scene headlessly with Commandline.exe.

    Uses the scene's own render settings unless *frame_range*
    (a ``(from, to)`` pair) and/or *resolution* (a ``(width, height)``
    pair) override them. Scene and output paths must live under an
    allow-listed root. The renderer exe is resolved from the detected
    Cinema 4D install (sibling ``Commandline.exe``), falling back to
    PATH; a clear error is returned when it is not available.

    ``runner`` is injectable for tests and forwarded to
    ``_common.run_cli``: a callable ``(argv, timeout)`` returning
    ``(returncode, stdout, stderr)``.
    """
    try:
        checked_scene = check_path_allowed(scene_path)
        checked_output = check_path_allowed(output_path)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)
    try:
        if frame_range is not None:
            frame_from, frame_to = frame_range  # raises if not a pair
            frame_range = (int(frame_from), int(frame_to))
        if resolution is not None:
            width, height = resolution  # raises if not a pair
            resolution = (int(width), int(height))
            if resolution[0] <= 0 or resolution[1] <= 0:
                return err(f"Resolution must be positive, got {resolution}.")
    except Exception as exc:  # noqa: BLE001
        return err(f"Invalid frame_range/resolution: {exc}")
    try:
        render_exe = _find_render_exe()
        if not render_exe:
            return err(
                "Cinema 4D commandline renderer not found: Cinema 4D is "
                "not installed and no Commandline.exe was found on PATH."
            )
        argv = build_render_command(render_exe, str(checked_scene),
                                    str(checked_output),
                                    frame_range=frame_range,
                                    resolution=resolution)
        result = run_cli(argv, runner=runner)
        if not result.get("ok"):
            return result
        audit("cinema_4d_render", {"scene": str(checked_scene),
                                   "output": str(checked_output), "argv": argv})
        return ok(scene=str(checked_scene), output=str(checked_output),
                  render_exe=render_exe, argv=argv, route="commandline",
                  returncode=result.get("returncode"),
                  stdout=result.get("stdout"), stderr=result.get("stderr"))
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def run_script(script_path: str, runner=None) -> dict:
    """Run a Cinema 4D Python script headlessly with c4dpy.exe.

    The script runs with Cinema 4D's Python interpreter (the ``c4d``
    module is importable) and c4dpy exits when it finishes. The exe
    is resolved from the detected Cinema 4D install (sibling
    ``c4dpy.exe``), falling back to PATH; a clear error is returned
    when it is not available. The script path must live under an
    allow-listed root.

    ``runner`` is injectable for tests and forwarded to
    ``_common.run_cli``: a callable ``(argv, timeout)`` returning
    ``(returncode, stdout, stderr)``.
    """
    try:
        checked = check_path_allowed(script_path)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)
    try:
        c4dpy_exe = _find_c4dpy_exe()
        if not c4dpy_exe:
            return err(
                "Cinema 4D Python interpreter not found: Cinema 4D is "
                "not installed and no c4dpy.exe was found on PATH."
            )
        argv = build_c4dpy_command(c4dpy_exe, str(checked))
        result = run_cli(argv, runner=runner)
        if not result.get("ok"):
            return result
        audit("cinema_4d_run_script", {"script": str(checked), "argv": argv})
        return ok(script=str(checked), c4dpy_exe=c4dpy_exe, argv=argv,
                  route="c4dpy", returncode=result.get("returncode"),
                  stdout=result.get("stdout"), stderr=result.get("stderr"))
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


# ---------------------------------------------------------------- register

def register(mcp):
    """Register the cinema_4d_* tools on a FastMCP server."""

    @mcp.tool()
    def cinema_4d_status() -> dict:
        """Check whether Cinema 4D is installed and how it can be automated.

        Returns the catalog detection result: installed flag, executable
        path and detection source, plus Cinema 4D's automation strategy
        ("plugin" — Python SDK scripts via c4dpy, rendering via the
        Commandline renderer).
        """
        return catalog_status(APP_ID)

    @mcp.tool()
    def cinema_4d_launch() -> dict:
        """Launch the Cinema 4D GUI. Fails clearly when not installed."""
        return catalog_launch(APP_ID)

    @mcp.tool()
    def cinema_4d_render(scene_path: str, output_path: str,
                         frame_start: int = -1, frame_end: int = -1,
                         width: int = 0, height: int = 0) -> dict:
        """Render a .c4d scene to an image file, headlessly (no GUI).

        Runs Commandline.exe with -render <scene> -oimage <output>.
        Optionally pass frame_start AND frame_end to render a frame
        range (-frame), and width AND height (both > 0) to override
        the output resolution (-oresolution); leave them at their
        defaults to use the scene's saved render settings. Scene and
        output paths must live under an allow-listed root. Returns the
        renderer's stdout/stderr and exit code on success.
        """
        frame_range = None
        if frame_start >= 0 and frame_end >= 0:
            frame_range = (frame_start, frame_end)
        elif frame_start >= 0 or frame_end >= 0:
            return err("Pass both frame_start and frame_end, or neither.")
        resolution = None
        if width > 0 and height > 0:
            resolution = (width, height)
        elif width > 0 or height > 0:
            return err("Pass both width and height, or neither.")
        return render_scene(scene_path, output_path,
                            frame_range=frame_range, resolution=resolution)

    @mcp.tool()
    def cinema_4d_run_script(script_path: str) -> dict:
        """Run a Cinema 4D Python script headlessly via c4dpy.exe.

        The script has access to the Cinema 4D Python SDK (import c4d).
        The path must live under an allow-listed root. Returns the
        script's stdout/stderr and exit code on success.
        """
        return run_script(script_path)

    return [cinema_4d_status, cinema_4d_launch,
            cinema_4d_render, cinema_4d_run_script]
