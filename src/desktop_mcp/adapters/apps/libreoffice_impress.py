"""LibreOffice Impress adapter — CLI strategy via ``soffice``.

LibreOffice Impress (presentations) has no COM interface on the
LibreOffice side; its scriptable surface is the shared ``soffice``
binary:

- ``libreoffice_impress_status``  — is LibreOffice installed? (catalog)
- ``libreoffice_impress_launch``  — start LibreOffice (catalog launch)
- ``libreoffice_impress_convert`` — headless format conversion, e.g.
  ``soffice --headless --convert-to pptx --outdir <dir> <file.odp>``
  (also pdf, odp, ppt, html, … — anything LibreOffice can export)
- ``libreoffice_impress_open_file`` — open a presentation in the GUI

Safety: both file tools validate their paths with
``core.check_path_allowed`` *before* anything runs — a path outside
the allowed roots returns an ``err`` dict and ``soffice`` is never
invoked. Conversions are written to the audit log on success.

Testability: the conversion runs through :func:`_common.run_cli` with
``runner`` taken from the module-level :data:`runner` variable
(default ``None`` = real subprocess), so tests can inject a fake
runner by monkeypatching ``libreoffice_impress.runner`` (or by passing
``runner=`` to the implementation function directly).
``libreoffice_impress_open_file`` spawns through the module-level
:func:`_spawn` helper so tests can monkeypatch it. All tools return
``core.ok`` / ``core.err`` dicts and never raise.
"""
from __future__ import annotations

import shutil
import subprocess

from ... import apps_catalog
from ...core import audit, check_path_allowed, err, ok
from . import _common

APP_ID = "libreoffice-impress"

#: Binary name used for every soffice invocation (conversions + GUI open).
SOFFICE = "soffice"

#: Default export format for conversions (PowerPoint, the common ask).
DEFAULT_FORMAT = "pptx"

#: Conversions of large decks can take a while; give soffice 5 minutes.
CONVERT_TIMEOUT = 300

#: Module-level injectable CLI runner for tests: a callable
#: ``(argv, timeout) -> (returncode, stdout, stderr)`` forwarded to
#: :func:`_common.run_cli`. ``None`` (the default) means "really run it".
runner = None

_UNSET = object()


# ---------------------------------------------------------------------------
# Pure builder
# ---------------------------------------------------------------------------

def build_convert_argv(input_path, output_dir, target_format: str = DEFAULT_FORMAT) -> list[str]:
    """Build the headless ``soffice`` conversion argv. Pure: no I/O.

    >>> build_convert_argv("deck.odp", "out")
    ['soffice', '--headless', '--convert-to', 'pptx', '--outdir', 'out', 'deck.odp']
    """
    return [SOFFICE, "--headless", "--convert-to", str(target_format),
            "--outdir", str(output_dir), str(input_path)]


# ---------------------------------------------------------------------------
# Injectable helpers
# ---------------------------------------------------------------------------

def _spawn(argv: list[str]):
    """Start *argv* as a detached process. Factored out for monkeypatching."""
    return subprocess.Popen([str(a) for a in argv], close_fds=True)


def _soffice_found() -> bool:
    """True when the ``soffice`` binary is resolvable on this machine.

    Resolution: ``shutil.which`` for ``soffice`` / ``soffice.exe``,
    then catalog detection for ``libreoffice-impress`` (PATH lookup
    plus, on Windows, the usual install folders).
    """
    for name in (SOFFICE, "soffice.exe"):
        try:
            if shutil.which(name):
                return True
        except Exception:  # noqa: BLE001 — a broken probe means "not found"
            pass
    try:
        det = apps_catalog.detect_app(APP_ID)
    except Exception:  # noqa: BLE001 — detection must never crash a tool
        return False
    return bool(det.get("ok") and det.get("installed"))


# ---------------------------------------------------------------------------
# Tool implementations (module-level, directly testable)
# ---------------------------------------------------------------------------

def libreoffice_impress_status() -> dict:
    """Return LibreOffice Impress detection + strategy info from the catalog."""
    return _common.catalog_status(APP_ID)


def libreoffice_impress_launch() -> dict:
    """Launch the installed LibreOffice application (no file)."""
    return _common.catalog_launch(APP_ID)


def libreoffice_impress_convert(input_path: str, output_dir: str,
                                target_format: str = DEFAULT_FORMAT,
                                runner=_UNSET) -> dict:
    """Convert a presentation to another format with headless ``soffice``.

    Runs ``soffice --headless --convert-to <target_format> --outdir
    <output_dir> <input_path>`` — e.g. an .odp deck to .pptx (the
    default), or a .pptx deck to ``pdf``. The input file must exist
    and both the input file and *output_dir* must live under an
    allowed root (see ``core.check_path_allowed``); a disallowed path
    or a missing input returns an ``err`` dict and ``soffice`` is
    never invoked. A successful conversion is written to the audit
    log. *runner* is injectable for tests (a callable ``(argv,
    timeout) -> (returncode, stdout, stderr)``); when not passed, the
    module-level :data:`runner` variable is used (default ``None`` =
    real subprocess via :func:`_common.run_cli`).
    """
    try:
        checked_input = check_path_allowed(input_path)
        if not checked_input.exists():
            return err(f"Input file does not exist: {checked_input}")
        checked_outdir = check_path_allowed(output_dir)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)
    argv = build_convert_argv(str(checked_input), str(checked_outdir),
                              target_format)
    effective_runner = globals()["runner"] if runner is _UNSET else runner
    result = _common.run_cli(argv, timeout=CONVERT_TIMEOUT,
                             runner=effective_runner)
    if not result.get("ok"):
        return result
    audit("libreoffice_impress_convert",
          {"argv": argv, "input_path": str(checked_input),
           "output_dir": str(checked_outdir),
           "target_format": str(target_format)})
    return {**result, "input_path": str(checked_input),
            "output_dir": str(checked_outdir),
            "target_format": str(target_format)}


def libreoffice_impress_open_file(path: str) -> dict:
    """Open a presentation file in the LibreOffice Impress GUI.

    The path must pass ``core.check_path_allowed`` first; a disallowed
    path returns an ``err`` dict and nothing is launched. The
    ``soffice`` binary is resolved via ``shutil.which`` / catalog
    detection — when LibreOffice is not found on this machine an
    ``err`` dict is returned and nothing is spawned either.
    """
    try:
        checked = check_path_allowed(path)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)
    try:
        if not _soffice_found():
            return err(
                "LibreOffice (soffice) was not found on this machine; "
                f"cannot open {checked}. Install LibreOffice from "
                "libreoffice.org and make sure soffice is on PATH.")
        argv = [SOFFICE, str(checked)]
        proc = _spawn(argv)
        pid = getattr(proc, "pid", None)
        audit("libreoffice_impress_open_file",
              {"path": str(checked), "argv": argv, "pid": pid})
        return ok(app_id=APP_ID, opened=True, path=str(checked),
                  argv=argv, pid=pid)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


# ---------------------------------------------------------------------------
# MCP registration
# ---------------------------------------------------------------------------

def register(mcp):
    """Register the libreoffice_impress_* tools on a FastMCP server.

    Returns the list of tool names bound.
    """
    # Capture the module-level impls first: the wrappers below reuse
    # the same names, which would otherwise make them recurse.
    _g = globals()
    _status = _g["libreoffice_impress_status"]
    _launch = _g["libreoffice_impress_launch"]
    _convert = _g["libreoffice_impress_convert"]
    _open_file = _g["libreoffice_impress_open_file"]

    @mcp.tool()
    def libreoffice_impress_status() -> dict:
        """Check whether LibreOffice Impress is installed on this
        machine, where the soffice executable lives, and which
        automation strategy applies (cli — headless soffice)."""
        return _status()

    @mcp.tool()
    def libreoffice_impress_launch() -> dict:
        """Launch LibreOffice (no file). Fails with a clear error when
        LibreOffice is not installed. The launch is written to the
        audit log."""
        return _launch()

    @mcp.tool()
    def libreoffice_impress_convert(input_path: str, output_dir: str,
                                    target_format: str = DEFAULT_FORMAT) -> dict:
        """Convert a presentation file to another format, headlessly.

        Runs soffice --headless --convert-to <target_format> into
        output_dir: .odp → .pptx (default), .pptx → pdf, and any other
        format LibreOffice Impress can export. The input file must
        exist and both paths must be under an allowed root. Returns
        soffice's stdout/stderr and return code; the converted file
        lands in output_dir with the same base name.
        """
        return _convert(input_path, output_dir, target_format)

    @mcp.tool()
    def libreoffice_impress_open_file(path: str) -> dict:
        """Open a presentation file (e.g. .odp, .pptx, .ppt) in the
        LibreOffice Impress GUI. The path must be under an allowed
        root; LibreOffice must be installed."""
        return _open_file(path)

    return ["libreoffice_impress_status", "libreoffice_impress_launch",
            "libreoffice_impress_convert", "libreoffice_impress_open_file"]
