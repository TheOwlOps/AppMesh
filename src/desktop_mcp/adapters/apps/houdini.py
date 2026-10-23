"""Houdini adapter — plugin strategy (the ``hou`` Python module).

Houdini has no COM ProgID (catalog ``prog_id`` is ``None``); its real
scripting route is its own Python interpreter:

- ``hython.exe`` — "Houdini Python": a Python interpreter with the
  ``hou`` module importable. Headless scripts run as
  ``hython script.py [args...]`` — that is :func:`houdini_run_script`.
- ``hrender.exe`` — the documented command-line renderer: it loads a
  .hip file and renders one ROP/driver node, e.g.
  ``hrender -f 1 24 -d /out/mantra1 scene.hip`` — that is
  :func:`houdini_render`.

Both executables live in the same ``bin`` folder as ``houdini.exe``
(the catalog detects ``houdini.exe`` / ``hython.exe``), so the sibling
tools are derived from the detected install path. The argv builders
(:func:`build_hython_command`, :func:`build_render_command`) are pure:
the executable is passed in as a parameter and nothing is probed or
spawned while building.

Every tool returns a ``core.ok`` / ``core.err`` dict and never raises.
Script and .hip paths must pass ``core.check_path_allowed`` before
anything is executed.
"""
from __future__ import annotations

from pathlib import Path, PureWindowsPath

from ... import apps_catalog
from ...apps_catalog import detect_app
from ...core import audit, check_path_allowed, err, ok
from ._common import catalog_launch, catalog_status, run_cli

APP_ID = "houdini"

#: Default ROP/driver node rendered by ``houdini_render``.
DEFAULT_ROP = "/out/mantra1"

#: hython scripts can do real work; renders can run for hours.
SCRIPT_TIMEOUT = 600
RENDER_TIMEOUT = 3600


# ---------------------------------------------------------------------------
# Pure argv builders
# ---------------------------------------------------------------------------

def build_hython_command(script_path, args=None, exe="hython.exe") -> list[str]:
    """Build the hython argv: ``[exe, script, *args]``. Pure.

    >>> build_hython_command("job.py", ["--shot", "010"], exe="hython")
    ['hython', 'job.py', '--shot', '010']
    """
    return [str(exe), str(script_path), *[str(a) for a in (args or [])]]


def build_render_command(hip_path, rop_node=DEFAULT_ROP, frame_range=None,
                         exe="hrender.exe") -> list[str]:
    """Build the hrender argv. Pure.

    With a frame range the documented ``hrender`` layout is
    ``[exe, "-f", first, last, "-d", rop_node, hip]``; without one the
    ``-f`` pair is omitted and hrender uses the ROP's own frame range.

    >>> build_render_command("shot.hip", frame_range=(1, 24), exe="hrender")
    ['hrender', '-f', '1', '24', '-d', '/out/mantra1', 'shot.hip']
    """
    argv = [str(exe)]
    if frame_range is not None:
        first, last = frame_range
        argv += ["-f", str(int(first)), str(int(last))]
    argv += ["-d", str(rop_node), str(hip_path)]
    return argv


# ---------------------------------------------------------------------------
# Executable resolution (via the catalog)
# ---------------------------------------------------------------------------

def _sibling_exe(detected_path: str, filename: str) -> str:
    """Return *filename* placed next to *detected_path*. Pure.

    Handles both POSIX-style (``C:/SideFX/bin/houdini.exe``) and
    Windows-style (``C:\\SideFX\\bin\\houdini.exe``) detected paths.
    """
    text = str(detected_path)
    if "\\" in text:
        win = PureWindowsPath(text)
        if win.name.lower() == filename.lower():
            return text
        return str(win.with_name(filename))
    posix = Path(text)
    if posix.name.lower() == filename.lower():
        return text
    if str(posix.parent) in (".", ""):
        return filename
    return str(posix.with_name(filename))


def _resolve_tool_exe(filename: str) -> str | None:
    """Resolve a sibling tool exe (``hython.exe`` / ``hrender.exe``).

    Resolution goes through the catalog's :func:`detect_app` for
    Houdini: when Houdini is installed, the detected executable's
    folder is Houdini's ``bin`` folder, which also holds hython and
    hrender. Returns ``None`` when Houdini is not installed.
    """
    try:
        det = detect_app(APP_ID)
    except Exception:  # noqa: BLE001 — detection must never break a tool
        return None
    if not det.get("ok") or not det.get("installed") or not det.get("path"):
        return None
    return _sibling_exe(str(det["path"]), filename)


def _not_installed(tool: str) -> dict:
    return err(
        f"Houdini is not installed ({tool} was not found): none of "
        "Houdini's executables (houdini.exe / hython.exe) were detected "
        "on this machine. Install Houdini from sidefx.com first."
    )


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def houdini_status() -> dict:
    """Report whether Houdini is installed, where, and its strategy."""
    return catalog_status(APP_ID)


def houdini_launch() -> dict:
    """Launch the Houdini GUI via the catalog."""
    return catalog_launch(APP_ID)


def houdini_run_script(script_path: str, args=None, runner=None) -> dict:
    """Run a Python script headlessly with hython (the ``hou`` module).

    The script runs under Houdini's own Python interpreter, so
    ``import hou`` works and the script can load/build/save .hip files
    and trigger renders. *args* are passed to the script verbatim
    (they land in its ``sys.argv[1:]``). The script path must live
    under an allowed root. *runner* is injectable for tests and is
    forwarded to :func:`_common.run_cli` (a callable
    ``(argv, timeout) -> (returncode, stdout, stderr)``).
    """
    try:
        checked = check_path_allowed(script_path)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)
    try:
        hython_exe = _resolve_tool_exe("hython.exe")
        if not hython_exe:
            return _not_installed("hython.exe")
        argv = build_hython_command(str(checked), args or [], exe=hython_exe)
        result = run_cli(argv, timeout=SCRIPT_TIMEOUT, runner=runner)
        if not result.get("ok"):
            return result
        audit("houdini_run_script", {"script": str(checked), "argv": argv})
        return ok(script=str(checked), exe=hython_exe, argv=argv,
                  returncode=result.get("returncode"),
                  stdout=result.get("stdout", ""),
                  stderr=result.get("stderr", ""))
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def houdini_render(hip_path: str, rop_node: str = DEFAULT_ROP,
                   frame_range=None, runner=None) -> dict:
    """Render a .hip file headlessly with hrender.

    *rop_node* is the output driver inside the .hip to render
    (default ``/out/mantra1``). *frame_range* is an optional
    ``(first, last)`` pair; when omitted, hrender uses the ROP's own
    frame range. The .hip path must live under an allowed root.
    *runner* is injectable for tests and is forwarded to
    :func:`_common.run_cli`.
    """
    try:
        checked = check_path_allowed(hip_path)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)
    try:
        hrender_exe = _resolve_tool_exe("hrender.exe")
        if not hrender_exe:
            return _not_installed("hrender.exe")
        argv = build_render_command(str(checked), rop_node=rop_node,
                                    frame_range=frame_range, exe=hrender_exe)
        result = run_cli(argv, timeout=RENDER_TIMEOUT, runner=runner)
        if not result.get("ok"):
            return result
        audit("houdini_render", {"hip": str(checked), "rop_node": str(rop_node),
                                 "argv": argv})
        return ok(hip=str(checked), rop_node=str(rop_node),
                  frame_range=list(frame_range) if frame_range is not None else None,
                  exe=hrender_exe, argv=argv,
                  returncode=result.get("returncode"),
                  stdout=result.get("stdout", ""),
                  stderr=result.get("stderr", ""))
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


# ---------------------------------------------------------------------------
# MCP registration
# ---------------------------------------------------------------------------

def register(mcp):
    """Register the houdini_* tools on a FastMCP server.

    Returns the list of tool names bound.
    """
    # Capture the module-level impls first: the wrappers below reuse
    # the same names, which would otherwise make them recurse.
    _g = globals()
    _status = _g["houdini_status"]
    _launch = _g["houdini_launch"]
    _run_script = _g["houdini_run_script"]
    _render = _g["houdini_render"]

    @mcp.tool()
    def houdini_status() -> dict:
        """Check whether Houdini (SideFX) is installed on this machine,
        where its executable lives, and which automation strategy it
        uses (plugin — the hou Python module via hython)."""
        return _status()

    @mcp.tool()
    def houdini_launch() -> dict:
        """Launch the Houdini GUI application. Fails with a clear
        error when Houdini is not installed."""
        return _launch()

    @mcp.tool()
    def houdini_run_script(script_path: str, args: list = None) -> dict:
        """Run a Python script headlessly with hython, Houdini's own
        Python interpreter (the script can `import hou` to load, edit
        and save .hip files or trigger renders — no GUI opens).
        Optional args are passed to the script verbatim. The script
        path must be under an allowed root. Fails with a clear error
        when Houdini is not installed."""
        return _run_script(script_path, args)

    @mcp.tool()
    def houdini_render(hip_path: str, rop_node: str = DEFAULT_ROP,
                       frame_range: list = None) -> dict:
        """Render a .hip file headlessly with hrender, Houdini's
        command-line renderer. rop_node is the output driver to render
        (default "/out/mantra1"). frame_range is an optional
        [first, last] pair; omit it to use the ROP's own range. The
        .hip path must be under an allowed root. Renders can take a
        long time. Fails with a clear error when Houdini is not
        installed."""
        return _render(hip_path, rop_node, frame_range)

    return ["houdini_status", "houdini_launch", "houdini_run_script",
            "houdini_render"]
