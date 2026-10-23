"""Revit adapter — catalog id ``revit``, tool prefix ``revit``.

Revit's catalog strategy is ``plugin``: it has **no COM Automation
interface** (``prog_id`` is ``None``). Real automation happens inside
Revit through the .NET RevitAPI, a Dynamo graph, or journal-file
playback. From outside the app this adapter can therefore honestly do
three things:

- ``revit_status`` / ``revit_launch`` — straight from the catalog.
- ``revit_open_file`` — open a model/family/template by passing the file
  as a command-line argument to the detected ``Revit.exe``. Route used:
  ``detected_exe_with_file_argument`` (reported as ``route`` in the
  result dict — there is no COM route to try first).
- ``revit_run_script`` — journal-file playback, Revit's well-known
  script entry point: launching ``Revit.exe <journal.txt>`` replays the
  journal recorded from an interactive session. The argv is built here
  and executed through :func:`_common.run_cli` (which blocks until
  Revit exits or the timeout hits — journal playback is not a true
  headless mode). Dynamo graphs (``.dyn``) cannot be started this way;
  they run through Dynamo/Revit in-app, so they are rejected with a
  clear error instead of a fake success.

All tools return ``core.ok`` / ``core.err`` dicts and never raise.
"""
from __future__ import annotations

import subprocess

from ... import apps_catalog
from ...core import audit, check_path_allowed, err, ok
from . import _common

APP_ID = "revit"

# Revit journal files are plain-text recordings of an interactive session.
JOURNAL_SUFFIX = ".txt"
# How long journal playback may run before run_cli gives up.
JOURNAL_TIMEOUT = 600


def _detected_exe() -> tuple[str | None, dict | None]:
    """Return ``(exe_path, None)`` or ``(None, err_dict)`` for Revit."""
    det = apps_catalog.detect_app(APP_ID)
    if not det.get("ok"):
        return None, det
    if not det.get("installed") or not det.get("path"):
        return None, err(
            "Revit is not installed (none of its executables were found "
            "on this machine).")
    return str(det["path"]), None


def open_file(path: str, popen=None) -> dict:
    """Open *path* in Revit via the detected exe (file as argument).

    Route used: ``detected_exe_with_file_argument`` — Revit has no COM
    interface, so ``Revit.exe <file>`` is the only outside route. The
    file must pass ``core.check_path_allowed`` first. ``popen`` is
    injectable for tests (defaults to ``subprocess.Popen``).
    """
    try:
        checked = check_path_allowed(path)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)
    exe, failure = _detected_exe()
    if failure is not None:
        return failure
    try:
        spawn = popen if popen is not None else subprocess.Popen
        proc = spawn([exe, str(checked)])
        pid = getattr(proc, "pid", None)
        audit("revit_open_file", {"route": "detected_exe_with_file_argument",
                                  "exe": exe, "path": str(checked), "pid": pid})
        return ok(opened=True, route="detected_exe_with_file_argument",
                  app_id=APP_ID, exe=exe, path=str(checked), pid=pid)
    except Exception as exc:  # noqa: BLE001
        return err(exc)


def run_script(script_path: str, runner=None) -> dict:
    """Replay a Revit journal file (``.txt``) with the detected exe.

    Builds ``[Revit.exe, <journal.txt>]`` and runs it through
    :func:`_common.run_cli`; ``runner`` is injectable for tests (a
    callable ``(argv, timeout) -> (returncode, stdout, stderr)``).
    Returns a graceful err dict when Revit is not installed. Only
    journal files are accepted: Dynamo ``.dyn`` graphs have no
    command-line entry point and must be run inside Revit.
    """
    try:
        checked = check_path_allowed(script_path)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)
    if checked.suffix.lower() == ".dyn":
        return err(
            "Dynamo graphs (.dyn) cannot be run from the command line: "
            "open Revit and run the graph in Dynamo / Dynamo Player. "
            "revit_run_script only replays Revit journal files (.txt).")
    if checked.suffix.lower() != JOURNAL_SUFFIX:
        return err(
            f"Unsupported script type {checked.suffix!r}: Revit's only "
            "command-line script entry is journal-file playback, and "
            "journal files are .txt files.")
    exe, failure = _detected_exe()
    if failure is not None:
        return failure
    argv = [exe, str(checked)]
    result = _common.run_cli(argv, timeout=JOURNAL_TIMEOUT, runner=runner)
    if not result.get("ok"):
        return result
    audit("revit_run_script", {"route": "journal_playback", "argv": argv})
    return ok(**{k: v for k, v in result.items() if k != "ok"},
              route="journal_playback", app_id=APP_ID,
              script=str(checked), exe=exe)


def register(mcp):
    """Register the revit_* tools on a FastMCP server."""

    @mcp.tool()
    def revit_status() -> dict:
        """Report whether Autodesk Revit is installed on this machine,
        where its executable was found, and its automation strategy
        (plugin — no COM; RevitAPI add-ins, Dynamo or journal files)."""
        return _common.catalog_status(APP_ID)

    @mcp.tool()
    def revit_launch() -> dict:
        """Launch Autodesk Revit. Fails with a clear error when Revit
        is not installed on this machine."""
        return _common.catalog_launch(APP_ID)

    @mcp.tool()
    def revit_open_file(path: str) -> dict:
        """Open a Revit file (.rvt project, .rfa family, .rte template…)
        in Revit. Route used: the detected Revit.exe is started with the
        file as its argument (reported as ``route`` in the result) —
        Revit has no COM interface, so there is no COM route. The path
        must be under an allowed root."""
        return open_file(path)

    @mcp.tool()
    def revit_run_script(script_path: str) -> dict:
        """Replay a Revit journal file (.txt) by launching Revit.exe
        with the journal as its argument (journal-file playback, Revit's
        script entry point). Blocks until Revit exits, up to 600s.
        Dynamo .dyn graphs are NOT runnable this way and are rejected."""
        return run_script(script_path)

    return ["revit_status", "revit_launch", "revit_open_file", "revit_run_script"]
