"""Visual Studio Code adapter — CLI strategy.

VS Code ships a capable command-line launcher, ``code``, so this adapter
needs no GUI automation:

- ``vscode_status``  — is VS Code installed? (catalog detection)
- ``vscode_launch``  — start VS Code (catalog launch)
- ``vscode_open``    — open a file or folder in VS Code, reusing the
  current window (``code --reuse-window <path>``)
- ``vscode_version`` — report the installed version (``code --version``)

Safety: ``vscode_open`` validates its path with
``core.check_path_allowed`` *before* anything is spawned — a path outside
the allowed roots returns an ``err`` dict and the CLI is never invoked.

All tools return plain ``core.ok`` / ``core.err`` dicts and never raise
across the MCP boundary. Command execution goes through
``_common.run_cli``; its ``runner`` parameter is injectable so tests can
run hermetically without a real ``code`` binary.
"""
from __future__ import annotations

from ... import core
from . import _common

APP_ID = "vscode"


# ---------------------------------------------------------------------------
# Argv builders (pure functions — asserted argv-for-argv in tests)
# ---------------------------------------------------------------------------

def build_vscode_open(path: str) -> list[str]:
    """Build the argv to open *path* (file or folder) in VS Code.

    ``--reuse-window`` forces the path into the most recently used VS Code
    window instead of opening a new one.
    """
    return ["code", "--reuse-window", str(path)]


def build_vscode_version() -> list[str]:
    """Build the argv to print the VS Code version."""
    return ["code", "--version"]


# Convenient short aliases (same builders, alternative names).
build_open = build_vscode_open
build_version = build_vscode_version


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def vscode_status() -> dict:
    """Report whether Visual Studio Code is installed on this machine.

    Returns the catalog detection result for ``vscode``: whether it was
    found, the path it was found at, how it was detected, and the
    automation strategy recorded in the catalog (``cli``).
    """
    try:
        return _common.catalog_status(APP_ID)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return core.err(exc)


def vscode_launch() -> dict:
    """Launch Visual Studio Code.

    Uses the catalog's launch logic for ``vscode``. Returns an ``err``
    dict if VS Code is not installed or cannot be started.
    """
    try:
        return _common.catalog_launch(APP_ID)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return core.err(exc)


def vscode_open(path: str, runner=None) -> dict:
    """Open a file or folder in Visual Studio Code.

    The path is opened in the current VS Code window
    (``code --reuse-window <path>``). The path must live under one of the
    allowed roots (see ``core.check_path_allowed``); a disallowed path is
    rejected with an error and VS Code is not invoked at all.

    Args:
        path: File or folder to open, e.g. a project directory or a
            source file inside an allowed root.
        runner: Test-only injectable runner forwarded to
            ``_common.run_cli`` — a callable ``(argv, timeout)``
            returning ``(returncode, stdout, stderr)``.
    """
    try:
        checked = core.check_path_allowed(path)
    except Exception as exc:  # noqa: BLE001 — disallowed / invalid path
        return core.err(exc)
    argv = build_vscode_open(str(checked))
    result = _common.run_cli(argv, runner=runner)
    if not result.get("ok"):
        return result
    core.audit("vscode_open", {"argv": argv, "path": str(checked)})
    return {**result, "path": str(checked), "opened": True}


def vscode_version(runner=None) -> dict:
    """Report the installed Visual Studio Code version.

    Runs ``code --version``. On success the result includes ``stdout``
    (the full multi-line output: version, commit, architecture) and a
    convenience ``version`` field holding its first line. Returns an
    ``err`` dict when the ``code`` CLI is not on PATH or exits non-zero.

    Args:
        runner: Test-only injectable runner forwarded to
            ``_common.run_cli`` — a callable ``(argv, timeout)``
            returning ``(returncode, stdout, stderr)``.
    """
    argv = build_vscode_version()
    result = _common.run_cli(argv, runner=runner)
    if not result.get("ok"):
        return result
    stdout = result.get("stdout") or ""
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    return {**result, "version": lines[0] if lines else ""}


# ---------------------------------------------------------------------------
# MCP registration
# ---------------------------------------------------------------------------

def register(mcp) -> list[str]:
    """Register the vscode_* tools on a FastMCP server.

    Returns the list of registered tool names. The wrappers expose only
    user-facing parameters (the test-only ``runner`` argument stays out
    of the MCP schema); module-level implementations are captured via
    ``globals()`` first so the same-named wrappers do not recurse.
    """
    _g = globals()
    _status = _g["vscode_status"]
    _launch = _g["vscode_launch"]
    _open = _g["vscode_open"]
    _version = _g["vscode_version"]

    @mcp.tool()
    def vscode_status() -> dict:
        """Report whether Visual Studio Code is installed, where it was
        found, and its catalog automation strategy (cli)."""
        return _status()

    @mcp.tool()
    def vscode_launch() -> dict:
        """Launch Visual Studio Code on this machine."""
        return _launch()

    @mcp.tool()
    def vscode_open(path: str) -> dict:
        """Open a file or folder in Visual Studio Code, reusing the
        current window. The path must be under an allowed root."""
        return _open(path)

    @mcp.tool()
    def vscode_version() -> dict:
        """Report the installed Visual Studio Code version
        (code --version)."""
        return _version()

    return ["vscode_status", "vscode_launch", "vscode_open", "vscode_version"]
