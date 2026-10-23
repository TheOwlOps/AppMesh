"""Sublime Text adapter — plugin/CLI strategy.

Sublime Text ships a small command-line launcher, ``subl``, alongside the
editor (on Windows it lives in the install folder; the catalog probes for
``sublime_text.exe`` while ``subl`` is the documented CLI for opening
files). This adapter wraps that CLI:

- ``sublime_text_status`` — catalog detection/strategy info for
  ``sublime-text`` (via :mod:`._common`).
- ``sublime_text_launch`` — launch the editor via the catalog.
- ``sublime_text_open`` — open one file in Sublime Text with
  ``subl <path>``. The path must pass ``core.check_path_allowed`` first;
  a disallowed path returns an ``err`` dict and nothing is spawned.

The argv construction is a pure builder (:func:`build_open_argv`) and the
actual process start is factored into :func:`spawn`, so tests can assert
the exact argv and monkeypatch the spawn without touching a real editor.

All tools return ``core.ok`` / ``core.err`` dicts and never raise.
"""
from __future__ import annotations

import shutil
import subprocess

from ... import core
from ...core import err, ok
from . import _common

APP_ID = "sublime-text"

#: The Sublime Text command-line launcher.
CLI_EXE = "subl"


# ---------------------------------------------------------------------------
# Pure builder + factored spawn
# ---------------------------------------------------------------------------

def build_open_argv(path) -> list[str]:
    """Build the exact argv for opening *path* in Sublime Text.

    Pure function (no I/O, no allowlist check): returns
    ``["subl", str(path)]``.
    """
    return [CLI_EXE, str(path)]


# Alias matching the tool name, for discoverability.
build_sublime_text_open_argv = build_open_argv


def spawn(argv: list[str]):
    """Start *argv* as a detached editor process. Factored out so tests
    can monkeypatch it.

    Raises ``FileNotFoundError`` with a clear message when the ``subl``
    launcher is not on PATH; callers convert that into an ``err`` dict.
    Returns the ``subprocess.Popen`` handle.
    """
    if shutil.which(argv[0]) is None:
        raise FileNotFoundError(
            f"Executable not found on PATH: {argv[0]} "
            "(install Sublime Text and make sure its 'subl' CLI is on PATH)")
    return subprocess.Popen(argv, close_fds=True)


# Backwards/alternate name for the same factored spawn point.
_spawn = spawn


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def sublime_text_status() -> dict:
    """Report whether Sublime Text is installed and how it is automated.

    Delegates to the shared catalog helper for the ``sublime-text``
    catalog entry (detection result, install path and strategy).
    """
    try:
        return _common.catalog_status(APP_ID)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def sublime_text_launch() -> dict:
    """Launch Sublime Text via the shared catalog launcher."""
    try:
        return _common.catalog_launch(APP_ID)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def sublime_text_open(path: str) -> dict:
    """Open *path* in Sublime Text using the ``subl`` CLI.

    The path must live under an allow-listed root
    (``core.check_path_allowed``); a disallowed path returns an ``err``
    dict and nothing is spawned. On success the file is opened with the
    exact argv ``["subl", <resolved path>]`` via :func:`spawn`.
    """
    try:
        checked = core.check_path_allowed(path)
    except Exception as exc:  # noqa: BLE001 — disallowed path: err, no spawn
        return err(exc)
    argv = build_open_argv(checked)
    try:
        proc = spawn(argv)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)
    pid = getattr(proc, "pid", None)
    core.audit("sublime_text_open", {"path": str(checked), "argv": argv, "pid": pid})
    return ok(opened=True, path=str(checked), argv=argv, pid=pid)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(mcp) -> list[str]:
    """Register the ``sublime_text_*`` tools on a FastMCP server.

    Returns the list of registered tool names. The wrappers reuse the
    tool names, so the module-level implementations are captured through
    ``globals()`` first (same pattern as the CLI-tools adapter) to avoid
    the wrappers recursing into themselves.
    """
    _g = globals()
    _status = _g["sublime_text_status"]
    _launch = _g["sublime_text_launch"]
    _open = _g["sublime_text_open"]

    @mcp.tool()
    def sublime_text_status() -> dict:
        """Report whether Sublime Text is installed, where its executable
        was found, and which automation strategy the catalog assigns it."""
        return _status()

    @mcp.tool()
    def sublime_text_launch() -> dict:
        """Launch Sublime Text (no file). Fails with a clear error when
        the editor is not installed on this machine."""
        return _launch()

    @mcp.tool()
    def sublime_text_open(path: str) -> dict:
        """Open a file in Sublime Text via the `subl` CLI.

        `path` must be under an allow-listed root; disallowed paths are
        rejected with an error and nothing is launched.
        """
        return _open(path)

    return ["sublime_text_status", "sublime_text_launch", "sublime_text_open"]
