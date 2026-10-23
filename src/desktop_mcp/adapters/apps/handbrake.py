"""HandBrake adapter — transcode video via HandBrakeCLI (strategy: cli).

HandBrake ships two executables and they are NOT interchangeable:

- ``HandBrake.exe`` — the GUI. It cannot be driven from the command line
  to transcode; launching it just opens the window (that is what
  ``handbrake_launch`` does, via the catalog).
- ``HandBrakeCLI.exe`` — the standalone command-line transcoder. All
  real conversion work goes through it, e.g.
  ``HandBrakeCLI -i in.mkv -o out.mp4 --preset "Fast 1080p30"``.

So :func:`handbrake_convert` never uses the catalog's GUI path: it
resolves the CLI explicitly — ``shutil.which`` for ``HandBrakeCLI``
first, then a catalog-detection fallback that only accepts a detected
path whose file name is actually the CLI binary, then a scan of the
catalog's guessed install folders for ``HandBrakeCLI.exe`` (the usual
Windows layout, where the CLI sits next to the GUI but is not on PATH).
If no CLI binary is found the tool returns a clear ``err`` dict instead
of failing mysteriously by "transcoding" with the GUI exe.

All tools return ``core.ok`` / ``core.err`` dicts and never raise.
Paths are validated with ``core.check_path_allowed`` only (allowlist);
the conversion itself runs through :func:`_common.run_cli`, whose
``runner`` parameter is injectable for tests.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from ... import apps_catalog
from ...core import audit, check_path_allowed, err, ok
from ._common import catalog_launch, catalog_status, run_cli

APP_ID = "handbrake"

#: Names probed (in order) when looking for the CLI transcoder.
CLI_EXE_NAMES = ("HandBrakeCLI", "HandBrakeCLI.exe", "handbrakecli")

DEFAULT_PRESET = "Fast 1080p30"

#: Transcodes can run for a long time; give HandBrakeCLI an hour.
CONVERT_TIMEOUT = 3600


def build_convert_argv(cli_exe, input_path, output_path, preset=DEFAULT_PRESET) -> list:
    """Pure argv builder for one HandBrakeCLI transcode.

    >>> build_convert_argv("HandBrakeCLI", "in.mkv", "out.mp4")
    ['HandBrakeCLI', '-i', 'in.mkv', '-o', 'out.mp4', '--preset', 'Fast 1080p30']
    """
    return [str(cli_exe), "-i", str(input_path), "-o", str(output_path),
            "--preset", str(preset)]


def _is_cli_path(path) -> bool:
    """True when *path*'s file name is the HandBrakeCLI binary itself."""
    return Path(str(path)).stem.lower() == "handbrakecli"


def resolve_cli_exe(which_fn=None) -> str | None:
    """Resolve the HandBrakeCLI executable, or ``None`` when absent.

    Resolution order: ``shutil.which`` (or the injected *which_fn*) for
    the CLI names, then catalog detection — accepted only when the
    detected path is the CLI binary rather than the GUI exe — then the
    catalog's guessed install folders scanned for ``HandBrakeCLI.exe``.
    Injectable/monkeypatchable: tests patch this function or pass a
    fake *which_fn*.
    """
    which = which_fn if which_fn is not None else shutil.which
    for name in CLI_EXE_NAMES:
        try:
            found = which(name)
        except Exception:  # noqa: BLE001 — a broken probe means "not found"
            found = None
        if found:
            return str(found)
    # Catalog detection fallback (PATH via which_fn, common dirs on Windows).
    try:
        det = apps_catalog.detect_app(APP_ID, which_fn=which)
        if det.get("ok") and det.get("installed") and _is_cli_path(det.get("path")):
            return str(det["path"])
    except Exception:  # noqa: BLE001
        pass
    # Last resort: the catalog's guessed install folders, CLI file only.
    try:
        app = apps_catalog.get_app(APP_ID)
        if app.get("ok"):
            for cand in apps_catalog._common_paths(app["app"]):
                if _is_cli_path(cand) and cand.exists():
                    return str(cand)
    except Exception:  # noqa: BLE001
        pass
    return None


def handbrake_status() -> dict:
    """Report whether HandBrake is installed, where, and its strategy."""
    return catalog_status(APP_ID)


def handbrake_launch() -> dict:
    """Launch the HandBrake GUI via the catalog."""
    return catalog_launch(APP_ID)


def handbrake_convert(input_path: str, output_path: str,
                      preset: str = DEFAULT_PRESET, runner=None) -> dict:
    """Transcode *input_path* to *output_path* with HandBrakeCLI.

    *preset* is any HandBrake preset name (default "Fast 1080p30").
    Both paths must live under an allowed root (see
    ``core.check_path_allowed``). Note this uses HandBrakeCLI, the
    dedicated command-line transcoder — the HandBrake GUI executable
    cannot transcode from the command line. *runner* is injectable for
    tests and is forwarded to :func:`_common.run_cli` (a callable
    ``(argv, timeout) -> (returncode, stdout, stderr)``).
    """
    try:
        inp = check_path_allowed(input_path)
        out = check_path_allowed(output_path)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)
    cli_exe = resolve_cli_exe()
    if not cli_exe:
        return err(
            "HandBrakeCLI was not found on this machine. Transcoding "
            "requires HandBrakeCLI (the command-line transcoder), not "
            "just the HandBrake GUI: install HandBrake from "
            "handbrake.fr and make sure HandBrakeCLI.exe is on PATH or "
            "in the HandBrake install folder.")
    argv = build_convert_argv(cli_exe, str(inp), str(out), preset)
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
    except Exception as exc:  # noqa: BLE001
        return err(exc)
    res = run_cli(argv, timeout=CONVERT_TIMEOUT, runner=runner)
    if not res.get("ok"):
        return res
    audit("handbrake_convert", {"argv": argv, "preset": str(preset)})
    return ok(input=str(inp), output=str(out), preset=str(preset),
              argv=argv, returncode=res.get("returncode"),
              stdout=res.get("stdout", ""), stderr=res.get("stderr", ""))


def register(mcp):
    """Register the handbrake_* tools on a FastMCP server.

    Returns the list of tool names bound.
    """
    # Capture the module-level impls first: the wrappers below reuse the
    # same names, which would otherwise make them recurse.
    _g = globals()
    _status = _g["handbrake_status"]
    _launch = _g["handbrake_launch"]
    _convert = _g["handbrake_convert"]

    @mcp.tool()
    def handbrake_status() -> dict:
        """Check whether HandBrake is installed on this machine, where
        its executable lives, and which automation strategy it uses
        (cli — via HandBrakeCLI)."""
        return _status()

    @mcp.tool()
    def handbrake_launch() -> dict:
        """Launch the HandBrake GUI application. Fails with a clear
        error when HandBrake is not installed."""
        return _launch()

    @mcp.tool()
    def handbrake_convert(input_path: str, output_path: str,
                          preset: str = DEFAULT_PRESET) -> dict:
        """Transcode a video file with HandBrakeCLI using a named
        preset (default "Fast 1080p30"; others include "Fast 720p30",
        "HQ 1080p30 Surround", "Very Fast 480p30"). Both paths must be
        under an allowed root. Runs HandBrakeCLI — the GUI executable
        cannot transcode — and can take a long time for large files."""
        return _convert(input_path, output_path, preset)

    return ["handbrake_status", "handbrake_launch", "handbrake_convert"]
