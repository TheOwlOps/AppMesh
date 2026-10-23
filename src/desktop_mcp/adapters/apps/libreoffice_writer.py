"""LibreOffice Writer adapter — catalog id ``libreoffice-writer``.

Strategy: ``cli`` (binary ``soffice``). Writer documents are converted
headlessly with ``soffice --headless --convert-to <format>`` and opened
in the GUI by launching ``soffice`` with the file path.

All tools return ``core.ok`` / ``core.err`` dicts and never raise.
"""
from __future__ import annotations

import shutil
import subprocess

from ... import apps_catalog
from ...core import audit, check_path_allowed, err, ok
from . import _common

APP_ID = "libreoffice-writer"
BINARY = "soffice"

# Injectable runner for tests: a callable(argv, timeout) returning
# (returncode, stdout, stderr), passed straight to ``_common.run_cli``.
# ``None`` means run_cli uses its real subprocess implementation.
runner = None


def _find_soffice() -> str | None:
    """Resolve the ``soffice`` binary, or return None when not found.

    Tries ``shutil.which`` for ``soffice`` / ``soffice.exe`` first, then
    falls back to the catalog detection for ``libreoffice-writer``.
    """
    for name in ("soffice", "soffice.exe"):
        found = shutil.which(name)
        if found:
            return str(found)
    try:
        det = apps_catalog.detect_app(APP_ID)
    except Exception:  # noqa: BLE001 — detection must never break a tool
        return None
    if det.get("ok") and det.get("installed") and det.get("path"):
        return str(det["path"])
    return None


def _spawn(argv: list[str]):
    """Launch *argv* as a detached process. Factored out for tests."""
    return subprocess.Popen(argv, close_fds=True)


def build_convert_argv(input_path, output_dir, target_format: str = "pdf") -> list[str]:
    """Build the headless conversion argv. Pure: no I/O, no validation."""
    return ["soffice", "--headless", "--convert-to", target_format,
            "--outdir", output_dir, input_path]


def libreoffice_writer_status() -> dict:
    """Report whether LibreOffice Writer (soffice) is installed."""
    return _common.catalog_status(APP_ID)


def libreoffice_writer_launch() -> dict:
    """Launch LibreOffice Writer via the app catalog."""
    return _common.catalog_launch(APP_ID)


def libreoffice_writer_convert(input_path: str, output_dir: str,
                               target_format: str = "pdf", runner=None) -> dict:
    """Convert a document headlessly with LibreOffice (soffice).

    Converts *input_path* (e.g. a .docx/.odt file) to *target_format*
    (default "pdf"; also "docx", "odt", "txt", "html", ...) and writes
    the result into *output_dir*. Both paths must be under an allowed
    root and the input file must exist. Returns the soffice output on
    success; the conversion is written to the audit log.
    """
    try:
        fmt = (target_format or "").strip() or "pdf"
        try:
            checked_input = check_path_allowed(input_path)
        except Exception as exc:  # noqa: BLE001
            return err(exc)
        if not checked_input.exists():
            return err(f"Input file does not exist: {checked_input}")
        try:
            checked_outdir = check_path_allowed(output_dir)
        except Exception as exc:  # noqa: BLE001
            return err(exc)
        argv = build_convert_argv(str(checked_input), str(checked_outdir), fmt)
        effective_runner = runner if runner is not None else globals().get("runner")
        result = _common.run_cli(argv, runner=effective_runner)
        if not result.get("ok"):
            return result
        audit("libreoffice_writer_convert",
              {"input_path": str(checked_input), "output_dir": str(checked_outdir),
               "target_format": fmt, "argv": argv})
        return ok(input_path=str(checked_input), output_dir=str(checked_outdir),
                  target_format=fmt, argv=argv,
                  returncode=result.get("returncode"),
                  stdout=result.get("stdout"), stderr=result.get("stderr"))
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def libreoffice_writer_open_file(path: str) -> dict:
    """Open a document file in LibreOffice Writer (soffice).

    The path must be under an allowed root. Fails with a clear error
    when the ``soffice`` binary cannot be found on this machine.
    """
    try:
        try:
            checked = check_path_allowed(path)
        except Exception as exc:  # noqa: BLE001
            return err(exc)
        if _find_soffice() is None:
            return err("LibreOffice (soffice) was not found on this machine; "
                       "install LibreOffice to open files with Writer.")
        argv = ["soffice", str(checked)]
        proc = _spawn(argv)
        pid = getattr(proc, "pid", None)
        audit("libreoffice_writer_open_file", {"path": str(checked), "pid": pid})
        return ok(opened=True, path=str(checked), argv=argv, pid=pid)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def register(mcp) -> list[str]:
    """Register the libreoffice_writer_* tools on a FastMCP server.

    Returns the list of registered tool names. The wrappers expose only
    user-facing parameters (the test-only ``runner`` injection stays out
    of the MCP schema); module-level implementations are captured first
    so the same-named wrappers do not recurse.
    """
    _g = globals()
    _status = _g["libreoffice_writer_status"]
    _launch = _g["libreoffice_writer_launch"]
    _convert = _g["libreoffice_writer_convert"]
    _open_file = _g["libreoffice_writer_open_file"]

    @mcp.tool()
    def libreoffice_writer_status() -> dict:
        """Check whether LibreOffice Writer is installed on this machine
        (looks for the soffice executable) and report its catalog
        automation strategy and detected path."""
        return _status()

    @mcp.tool()
    def libreoffice_writer_launch() -> dict:
        """Launch LibreOffice Writer on this machine. Fails with a clear
        error when LibreOffice is not installed."""
        return _launch()

    @mcp.tool()
    def libreoffice_writer_convert(input_path: str, output_dir: str,
                                   target_format: str = "pdf") -> dict:
        """Convert a document with LibreOffice headlessly: convert
        input_path (docx/odt/...) to target_format (default "pdf") and
        write the converted file into output_dir. Both paths must be
        under an allowed root and the input file must exist."""
        return _convert(input_path, output_dir, target_format)

    @mcp.tool()
    def libreoffice_writer_open_file(path: str) -> dict:
        """Open a document file in LibreOffice Writer. The path must be
        under an allowed root and LibreOffice must be installed."""
        return _open_file(path)

    return ["libreoffice_writer_status", "libreoffice_writer_launch",
            "libreoffice_writer_convert", "libreoffice_writer_open_file"]
