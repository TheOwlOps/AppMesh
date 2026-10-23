"""Inkscape adapter — CLI strategy (catalog id ``inkscape``).

Inkscape has a first-class command line: a document conversion is a
single headless invocation ``inkscape <input> -o <output>`` where the
output format is inferred from the output file extension (SVG -> PNG,
PDF, EPS, …). This adapter wraps that CLI following the project's
pure-builder + injectable-runner pattern:

- :func:`build_convert_command` / :func:`build_version_command` are
  pure argv builders (no I/O), asserted argv-for-argv in tests;
- the actual execution goes through :func:`_common.run_cli`, whose
  ``runner`` parameter — a callable ``(argv, timeout) ->
  (returncode, stdout, stderr)`` — is injectable at every level
  (function argument, :func:`register` argument, or the module-level
  :data:`RUNNER`) so tests never invoke a real inkscape binary;
- both paths of a conversion must pass ``core.check_path_allowed``
  and the input must exist; successful conversions are audit-logged.

All tools return ``core.ok`` / ``core.err`` dicts and never raise.
"""
from __future__ import annotations

from typing import Callable, Optional

from ...core import audit, check_path_allowed, err, ok  # noqa: F401  (ok re-exported per adapter convention)
from . import _common

APP_ID = "inkscape"

# A runner takes (argv, timeout) and returns (returncode, stdout, stderr)
# — the exact contract of _common.run_cli's injectable runner.
Runner = Callable[[list, int], tuple]

# Module-level indirection: when no runner is passed explicitly, this one
# is used. Tests may monkeypatch it; production leaves it None, which
# makes _common.run_cli spawn the real executable.
RUNNER: Optional[Runner] = None


def _effective_runner(runner: Optional[Runner]) -> Optional[Runner]:
    return runner if runner is not None else RUNNER


# --------------------------------------------------------------------------
# Pure argv builders
# --------------------------------------------------------------------------

def build_convert_command(input_path, output_path, exe: str = "inkscape") -> list:
    """Argv converting *input_path* to *output_path* with Inkscape.

    The output format is chosen by Inkscape from the output extension.
    """
    return [exe, str(input_path), "-o", str(output_path)]


def build_version_command(exe: str = "inkscape") -> list:
    """Argv printing the installed Inkscape version."""
    return [exe, "--version"]


# --------------------------------------------------------------------------
# Tool implementations (runner-injectable)
# --------------------------------------------------------------------------

def inkscape_convert(input_path: str, output_path: str,
                     runner: Optional[Runner] = None,
                     exe: str = "inkscape") -> dict:
    """Convert a vector document with Inkscape's CLI.

    Both paths must live under an allowed root and the input must
    exist. On success the runner result (ok/argv/returncode/stdout/
    stderr from ``_common.run_cli``) is returned, enriched with the
    resolved ``input``/``output`` paths, and the run is audit-logged.
    """
    try:
        inp = check_path_allowed(input_path)
        out = check_path_allowed(output_path)
    except ValueError as exc:
        return err(exc)
    if not inp.exists():
        return err(f"Input file does not exist: {inp}")
    argv = build_convert_command(str(inp), str(out), exe=exe)
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return err(exc)
    result = _common.run_cli(argv, runner=_effective_runner(runner))
    if not result.get("ok"):
        return result
    audit("inkscape_convert", {"argv": argv, "input": str(inp),
                               "output": str(out)})
    result["input"] = str(inp)
    result["output"] = str(out)
    return result


def inkscape_version(runner: Optional[Runner] = None,
                     exe: str = "inkscape") -> dict:
    """Report the installed Inkscape version.

    On success the result carries the raw ``stdout`` plus a stripped
    ``version`` field with the version text (e.g. "Inkscape 1.3 …").
    """
    argv = build_version_command(exe=exe)
    result = _common.run_cli(argv, runner=_effective_runner(runner))
    if not result.get("ok"):
        return result
    result["version"] = (result.get("stdout") or "").strip()
    return result


# --------------------------------------------------------------------------
# MCP registration
# --------------------------------------------------------------------------

def register(mcp, runner: Optional[Runner] = None) -> list:
    """Register the inkscape_* tools on a FastMCP server.

    ``runner`` is the injectable CLI runner forwarded to the convert /
    version tools (tests pass a fake; production leaves it None).
    Returns the list of registered tool names.
    """
    # Capture the module-level implementations through globals(): the
    # wrappers below reuse their names, which would otherwise recurse.
    _g = globals()
    _convert = _g["inkscape_convert"]
    _version = _g["inkscape_version"]

    @mcp.tool()
    def inkscape_status() -> dict:
        """Check whether Inkscape is installed on this machine and how
        AppMesh automates it (CLI strategy): returns installed flag,
        executable path and catalog info."""
        return _common.catalog_status(APP_ID)

    @mcp.tool()
    def inkscape_launch() -> dict:
        """Launch the Inkscape GUI application. Fails with a clear
        error when Inkscape is not installed."""
        return _common.catalog_launch(APP_ID)

    @mcp.tool()
    def inkscape_convert(input_path: str, output_path: str) -> dict:
        """Convert a document with Inkscape, e.g. an SVG to PNG or PDF
        (the output format follows the output_path extension:
        drawing.svg -> drawing.png / drawing.pdf / drawing.eps).
        Both paths must be under an allowed root and the input file
        must exist. Requires Inkscape installed."""
        return _convert(input_path, output_path, runner=runner)

    @mcp.tool()
    def inkscape_version() -> dict:
        """Print the installed Inkscape version (runs
        ``inkscape --version``). The result's ``version`` field holds
        the version text."""
        return _version(runner=runner)

    return ["inkscape_status", "inkscape_launch",
            "inkscape_convert", "inkscape_version"]
