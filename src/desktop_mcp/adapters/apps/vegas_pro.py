"""VEGAS Pro adapter — plugin strategy (catalog id ``vegas-pro``).

VEGAS Pro has no COM ProgID, but it does expose a real scripting route
from the command line (its C#/VB/JScript ScriptHost API, normally used
via Tools > Scripting inside the app):

- run a script at launch:  ``vegas.exe -SCRIPT:<path to .cs/.vb/.js>``
  (the ``-SCRIPT:`` launch argument is documented by MAGIX/VEGAS), and
- open a project at launch: ``vegas.exe <path to a .veg project file>``
  (a project file passed as a plain argument).

This adapter exposes exactly those two routes as pure argv builders
(:func:`build_script_command` / :func:`build_open_command`) plus tool
wrappers that validate the target path against the core allowlist,
locate the installed executable through the catalog, and start VEGAS
detached via the factored :func:`_spawn` helper (monkeypatchable in
tests — VEGAS is a GUI app, so blocking on it with a CLI runner would
be wrong). All tools return ``core.ok`` / ``core.err`` dicts and never
raise.
"""
from __future__ import annotations

import subprocess

from ... import apps_catalog
from ...core import audit, check_path_allowed, err, ok
from . import _common

APP_ID = "vegas-pro"

# Imported into this module's namespace on purpose: tools below call the
# bare name ``detect_app`` so tests can monkeypatch
# ``vegas_pro.detect_app`` without touching the catalog module.
from ...apps_catalog import detect_app  # noqa: E402

_REAL_DETECT_APP = detect_app


# ---------------------------------------------------------------------------
# Pure argv builders
# ---------------------------------------------------------------------------

def build_script_command(exe: str, script_path: str) -> list[str]:
    """Build the VEGAS launch command that runs *script_path* on start.

    Pure: ``[exe, "-SCRIPT:<script_path>"]`` — the script path is glued
    to the ``-SCRIPT:`` prefix with a colon and no space, exactly as
    VEGAS expects its documented scripting launch argument.
    """
    return [str(exe), f"-SCRIPT:{script_path}"]


def build_open_command(exe: str, project_path: str) -> list[str]:
    """Build the VEGAS launch command that opens a project file.

    Pure: ``[exe, project_path]`` — VEGAS opens a ``.veg`` project
    passed as a plain command-line argument.
    """
    return [str(exe), str(project_path)]


# ---------------------------------------------------------------------------
# Spawn helper (factored out for tests)
# ---------------------------------------------------------------------------

def _popen(argv: list[str]):
    """Start *argv* via ``subprocess.Popen``. Factored out for tests."""
    return subprocess.Popen([str(a) for a in argv], close_fds=True)


def _spawn(argv: list[str]):
    """Start *argv* as a detached process. Factored out for testability
    (tests can monkeypatch this, :func:`_popen`, or ``subprocess.Popen``)."""
    return _popen(argv)


def _detect() -> dict:
    """Catalog detection record for VEGAS Pro.

    Uses this module's ``detect_app`` when a test has monkeypatched it
    (``vegas_pro.detect_app``); otherwise defers to
    ``apps_catalog.detect_app`` so patching there works too.
    """
    patched = globals().get("detect_app")
    if patched is not None and patched is not _REAL_DETECT_APP:
        return patched(APP_ID)
    return apps_catalog.detect_app(APP_ID)


def _resolve_exe() -> tuple[str | None, dict | None]:
    """Return ``(exe_path, None)`` or ``(None, err_dict)`` via the catalog."""
    try:
        det = _detect()
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return None, err(exc)
    if not det.get("ok"):
        return None, det
    if not det.get("installed") or not det.get("path"):
        return None, err(
            "VEGAS Pro is not installed (none of its executables — "
            "'VEGAS Pro.exe', 'vegas.exe' — were found on this machine).")
    return str(det["path"]), None


def _launch(argv: list[str], kind: str, target: str, exe: str) -> dict:
    """Spawn *argv* detached, audit it, and return an ok/err dict."""
    try:
        proc = _spawn(argv)
        pid = getattr(proc, "pid", None)
        audit(f"vegas_pro_{kind}", {"app_id": APP_ID, "argv": argv,
                                    "exe": exe, "target": target, "pid": pid})
        return ok(app_id=APP_ID, launched=True, opened=True, pid=pid,
                  argv=argv, exe=exe, path=target, **{kind: target})
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


# ---------------------------------------------------------------------------
# Tool implementations (also bound by register() below)
# ---------------------------------------------------------------------------

def vegas_pro_status() -> dict:
    """Check whether VEGAS Pro is installed on this machine.

    Returns the catalog detection result: installed flag, the
    executable path found (VEGAS Pro.exe / vegas.exe) and how it was
    found, plus the app's automation strategy ("plugin" — its
    C#/VB/JScript ScriptHost scripting API) and catalog name.
    """
    try:
        # If a test patched this module's ``detect_app``, honour it:
        # temporarily point the catalog module at the patched callable
        # so catalog_status (which looks it up there) sees it too.
        if globals().get("detect_app") is not _REAL_DETECT_APP:
            original = apps_catalog.detect_app
            try:
                apps_catalog.detect_app = globals()["detect_app"]
                return _common.catalog_status(APP_ID)
            finally:
                apps_catalog.detect_app = original
        return _common.catalog_status(APP_ID)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def vegas_pro_launch() -> dict:
    """Launch VEGAS Pro on its own (no project or script loaded).

    Fails with a clear error when VEGAS Pro is not installed. Use
    vegas_pro_open_project to start it with a project open, or
    vegas_pro_run_script to start it running a script. The launch is
    written to the audit log.
    """
    try:
        return _common.catalog_launch(APP_ID)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def vegas_pro_run_script(script_path: str) -> dict:
    """Launch VEGAS Pro and run a VEGAS script file on startup.

    *script_path* must point to a VEGAS script (.cs, .vb or .js — the
    ScriptHost scripting API) under an allowed root. VEGAS is started
    detached with its documented scripting launch argument
    ``-SCRIPT:<script_path>``, so the script runs inside the app (it
    can edit the timeline, render, etc.). Returns the exact argv used
    and the new process pid, or a clear error when the path is not
    allowed or VEGAS Pro is not installed.
    """
    if not script_path or not str(script_path).strip():
        return err("`script_path` must be a non-empty script file path.")
    try:
        checked = check_path_allowed(script_path)
    except Exception as exc:  # noqa: BLE001 — ValueError from allowlist
        return err(exc)
    exe, failure = _resolve_exe()
    if failure is not None:
        return failure
    argv = build_script_command(exe, str(checked))
    return _launch(argv, "script", str(checked), exe)


def vegas_pro_open_project(path: str) -> dict:
    """Launch VEGAS Pro with a project file open.

    *path* must point to a VEGAS project file (.veg) under an allowed
    root; it is passed to the executable as a plain argument, which
    VEGAS opens on startup. Returns the exact argv used and the new
    process pid, or a clear error when the path is not allowed or
    VEGAS Pro is not installed.
    """
    if not path or not str(path).strip():
        return err("`path` must be a non-empty project file path.")
    try:
        checked = check_path_allowed(path)
    except Exception as exc:  # noqa: BLE001 — ValueError from allowlist
        return err(exc)
    exe, failure = _resolve_exe()
    if failure is not None:
        return failure
    argv = build_open_command(exe, str(checked))
    return _launch(argv, "project", str(checked), exe)


# ---------------------------------------------------------------------------
# MCP registration
# ---------------------------------------------------------------------------

def register(mcp):
    """Register the vegas_pro_* tools on a FastMCP server.

    Binds the module-level implementations directly, so the registered
    tools and the plain functions are one and the same. Returns the
    list of tool names bound.
    """
    mcp.tool()(vegas_pro_status)
    mcp.tool()(vegas_pro_launch)
    mcp.tool()(vegas_pro_run_script)
    mcp.tool()(vegas_pro_open_project)
    return ["vegas_pro_status", "vegas_pro_launch",
            "vegas_pro_run_script", "vegas_pro_open_project"]
