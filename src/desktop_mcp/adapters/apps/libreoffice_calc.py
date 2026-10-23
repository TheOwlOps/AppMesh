"""LibreOffice Calc adapter — CLI strategy via ``soffice`` headless mode.

LibreOffice Calc (catalog id ``libreoffice-calc``, strategy ``cli``,
binary ``soffice``) has no COM interface. Its reliable automation
surface is the LibreOffice command line:

- conversions run headless, e.g.
  ``soffice --headless --convert-to xlsx --outdir <dir> <input.ods>``
  (xlsx / csv / pdf / ods and many other target formats);
- a spreadsheet is opened in the GUI by launching ``soffice`` with
  the file as its single argument: ``soffice <file>``.

This adapter follows the project's pure-builder + injectable-runner
pattern (see the Inkscape adapter):

- :func:`build_convert_argv` is pure — it builds the exact conversion
  argv and does no I/O, so it can be asserted argv-for-argv in tests;
- the conversion itself runs through :func:`_common.run_cli`, whose
  ``runner`` parameter — a callable ``(argv, timeout) ->
  (returncode, stdout, stderr)`` — is injectable at every level
  (function argument, :func:`register` argument, or the module-level
  :data:`RUNNER` / :data:`runner` variables) so tests never invoke a
  real ``soffice`` binary;
- :func:`libreoffice_calc_open_file` spawns ``soffice`` through the
  module-level :func:`_spawn` helper so tests can monkeypatch it.

Safety: the input path and the output directory must both pass
``core.check_path_allowed`` before anything runs, the input file must
exist, and successful conversions / opens are audit-logged. All
tools return ``core.ok`` / ``core.err`` dicts and never raise.
"""
from __future__ import annotations

import shutil
import subprocess
from typing import Callable, Optional

from ... import apps_catalog
from ...core import audit, check_path_allowed, err, ok
from . import _common

APP_ID = "libreoffice-calc"

#: Binary name used for conversions and for opening files.
BINARY = "soffice"

#: Default conversion target format for spreadsheets.
DEFAULT_FORMAT = "xlsx"

# A runner takes (argv, timeout) and returns (returncode, stdout, stderr)
# — the exact contract of _common.run_cli's injectable runner.
Runner = Callable[[list, int], tuple]

# Module-level injectable runner: when no runner is passed explicitly,
# this one is used. Tests may monkeypatch it (either spelling); production
# leaves it None, which makes _common.run_cli spawn the real executable.
RUNNER: Optional[Runner] = None
runner: Optional[Runner] = None


def _effective_runner(explicit_runner: Optional[Runner]) -> Optional[Runner]:
    """Resolve the runner to use: explicit argument first, then the
    module-level injectable variables (:data:`RUNNER`, :data:`runner`)."""
    if explicit_runner is not None:
        return explicit_runner
    if RUNNER is not None:
        return RUNNER
    return globals().get("runner")


# ---------------------------------------------------------------------------
# Pure argv builder
# ---------------------------------------------------------------------------

def build_convert_argv(input_path, output_dir, target_format: str = DEFAULT_FORMAT) -> list[str]:
    """Build the ``soffice`` headless conversion argv. Pure: no I/O.

    >>> build_convert_argv("in.ods", "outdir")
    ['soffice', '--headless', '--convert-to', 'xlsx', '--outdir', 'outdir', 'in.ods']
    """
    return ["soffice", "--headless", "--convert-to", str(target_format),
            "--outdir", str(output_dir), str(input_path)]


# ---------------------------------------------------------------------------
# Injectable helpers
# ---------------------------------------------------------------------------

def _spawn(argv: list[str]):
    """Start *argv* as a detached process via ``subprocess.Popen``.

    Factored out as a module-level function so tests can monkeypatch
    it and never launch a real application. For this adapter *argv*
    is ``["soffice", <file path>]``.
    """
    return subprocess.Popen([str(a) for a in argv], close_fds=True)


def _resolve_soffice() -> str | None:
    """Resolve the ``soffice`` executable, or ``None`` when not found.

    Resolution order: ``shutil.which`` for ``soffice`` (then
    ``soffice.exe``, the Windows name), then catalog detection for
    ``libreoffice-calc`` (PATH lookup plus common install folders).
    """
    for name in ("soffice", "soffice.exe"):
        try:
            found = shutil.which(name)
        except Exception:  # noqa: BLE001 — a broken probe means "not found"
            found = None
        if found:
            return str(found)
    try:
        det = apps_catalog.detect_app(APP_ID)
        if det.get("ok") and det.get("installed") and det.get("path"):
            return str(det["path"])
    except Exception:  # noqa: BLE001 — detection must never crash a tool
        pass
    return None


# ---------------------------------------------------------------------------
# Tool implementations (module-level, directly testable)
# ---------------------------------------------------------------------------

def libreoffice_calc_status() -> dict:
    """Return LibreOffice Calc detection + strategy info from the catalog."""
    return _common.catalog_status(APP_ID)


def libreoffice_calc_launch() -> dict:
    """Launch the installed LibreOffice Calc application (no file)."""
    return _common.catalog_launch(APP_ID)


def libreoffice_calc_convert(input_path: str, output_dir: str,
                             target_format: str = DEFAULT_FORMAT,
                             runner: Optional[Runner] = None) -> dict:
    """Convert a spreadsheet with ``soffice --headless --convert-to``.

    *input_path* is the spreadsheet to convert (e.g. .ods, .xls, .csv)
    and *output_dir* is the directory the converted file is written to;
    the output file keeps the input's base name with the
    *target_format* extension (default "xlsx"; "csv", "pdf" and "ods"
    are also common). Both paths must live under an allowed root (see
    ``core.check_path_allowed``) and the input file must exist — a
    rejected path returns an err dict and nothing is run. *runner* is
    injectable for tests and is forwarded to :func:`_common.run_cli`;
    when it is None the module-level :data:`RUNNER` is used. A
    successful conversion is written to the audit log.
    """
    try:
        inp = check_path_allowed(input_path)
        outdir = check_path_allowed(output_dir)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)
    if not inp.exists():
        return err(f"Input file does not exist: {inp}")
    argv = build_convert_argv(str(inp), str(outdir), str(target_format))
    try:
        outdir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return err(exc)
    result = _common.run_cli(argv, runner=_effective_runner(runner))
    if not result.get("ok"):
        return result
    audit("libreoffice_calc_convert",
          {"argv": argv, "input": str(inp), "output_dir": str(outdir),
           "target_format": str(target_format)})
    result["input"] = str(inp)
    result["output_dir"] = str(outdir)
    result["target_format"] = str(target_format)
    return result


def libreoffice_calc_open_file(path: str) -> dict:
    """Open a spreadsheet file in LibreOffice Calc.

    The path must pass ``core.check_path_allowed`` first; a disallowed
    path returns an err dict and nothing is launched. ``soffice`` is
    resolved via ``shutil.which`` / catalog detection — when it is not
    found an err dict is returned and nothing is spawned either. On
    success the file is opened by spawning ``["soffice", <path>]``
    through :func:`_spawn` and the open is audit-logged.
    """
    try:
        checked = check_path_allowed(path)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)
    try:
        soffice = _resolve_soffice()
        if not soffice:
            return err("LibreOffice (soffice) was not found on this machine "
                       "(not on PATH and not in a common install folder), "
                       f"so {checked} cannot be opened.")
        argv = [soffice, str(checked)]
        proc = _spawn(argv)
        pid = getattr(proc, "pid", None)
        audit("libreoffice_calc_open_file",
              {"app_id": APP_ID, "path": str(checked), "exe": soffice,
               "argv": argv, "pid": pid})
        return ok(app_id=APP_ID, opened=True, path=str(checked),
                  exe=soffice, argv=argv, pid=pid)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


# ---------------------------------------------------------------------------
# MCP registration
# ---------------------------------------------------------------------------

def register(mcp, runner: Optional[Runner] = None):
    """Register the libreoffice_calc_* tools on a FastMCP server.

    ``runner`` is the injectable CLI runner forwarded to the convert
    tool (tests pass a fake; production leaves it None, in which case
    the module-level :data:`RUNNER` / real ``soffice`` is used).
    Returns the list of registered tool names.
    """
    # Capture the module-level implementations through globals(): the
    # wrappers below reuse their names, which would otherwise recurse.
    _g = globals()
    _status = _g["libreoffice_calc_status"]
    _launch = _g["libreoffice_calc_launch"]
    _convert = _g["libreoffice_calc_convert"]
    _open_file = _g["libreoffice_calc_open_file"]

    @mcp.tool()
    def libreoffice_calc_status() -> dict:
        """Check whether LibreOffice Calc is installed on this machine.

        Returns the catalog detection result for LibreOffice Calc:
        installed flag, the detected soffice path (and how it was
        found) and its automation strategy ("cli"). Use this before
        converting or opening a file.
        """
        return _status()

    @mcp.tool()
    def libreoffice_calc_launch() -> dict:
        """Launch LibreOffice Calc (without opening any file).

        Starts the installed soffice found by the catalog and returns
        its path and pid. Fails with a clear error when LibreOffice is
        not installed on this machine. The launch is audit-logged.
        """
        return _launch()

    @mcp.tool()
    def libreoffice_calc_convert(input_path: str, output_dir: str,
                                 target_format: str = DEFAULT_FORMAT) -> dict:
        """Convert a spreadsheet file with LibreOffice headless mode.

        Runs ``soffice --headless --convert-to <target_format>
        --outdir <output_dir> <input_path>`` — for example an .ods or
        .xls spreadsheet to "xlsx" (the default), "csv", "pdf" or
        "ods". The converted file lands in output_dir with the input's
        base name and the new extension. Both input_path and
        output_dir must be under an allowed root and the input file
        must exist. Requires LibreOffice installed.
        """
        return _convert(input_path, output_dir, target_format, runner=runner)

    @mcp.tool()
    def libreoffice_calc_open_file(path: str) -> dict:
        """Open a spreadsheet file (.ods, .xlsx, .xls, .csv, …) in LibreOffice Calc.

        The file is opened by launching soffice with the file as its
        argument. The path must live under an allowed root — a
        disallowed path is rejected and nothing is opened. Fails with
        a clear error when soffice is not found on this machine.
        """
        return _open_file(path)

    return ["libreoffice_calc_status", "libreoffice_calc_launch",
            "libreoffice_calc_convert", "libreoffice_calc_open_file"]
