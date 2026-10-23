"""Slack adapter — UI + protocol strategy.

Slack's desktop GUI exposes no local automation API (the catalog lists
it as strategy ``ui``; real programmatic work goes through the Slack
Web API cloud-side). What this adapter can honestly do locally:

- ``slack_status`` / ``slack_launch`` — catalog detection and launch.
- ``slack_focus`` — bring an already-open Slack window to the front
  via Windows UI Automation (``_common.focus_window``).
- ``slack_open_via_protocol`` — hand a ``slack://`` URI to the OS,
  which routes it to the installed Slack client. With no team this is
  the plain ``slack://`` URI (just opens Slack). With a team id it uses
  ``slack://open?team=<team>`` — Slack's documented deep-link form for
  opening a specific workspace — where ``<team>`` is the workspace's
  team id (e.g. ``T0123ABCD``), *not* a workspace name or URL.

All tools return plain ``core.ok`` / ``core.err`` dicts and never raise.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

from ...core import audit, err, ok
from . import _common

APP_ID = "slack"

# Display name from the catalog record; Slack's main window title
# contains it, which is what focus_window matches on.
WINDOW_TITLE = "Slack"

# Conservative team-id charset: letters, digits, dash, underscore, dot.
# Anything else (spaces, slashes, "?", "&", ":", full URLs, …) is
# rejected, so the value can be embedded in the URI verbatim with no
# quoting/encoding surprises.
_TEAM_RE = re.compile(r"[A-Za-z0-9._-]+")


def build_protocol_uri(team: str = "") -> str:
    """Build the ``slack://`` URI for *team*. Pure — no side effects.

    - Empty team -> ``"slack://"`` (just opens Slack).
    - A team id -> ``"slack://open?team=<team>"``, Slack's documented
      open-a-specific-workspace deep-link form.

    Raises ValueError for a team containing anything outside
    ``[A-Za-z0-9._-]`` (spaces, slashes, ``?``, ``&``, full URLs, …).
    """
    team = (team or "").strip()
    if not team:
        return "slack://"
    if not _TEAM_RE.fullmatch(team):
        raise ValueError(
            f"Invalid Slack team id: {team!r}. Use the workspace's team id "
            "(letters, digits, '-', '_' or '.' only — not a name or URL).")
    return f"slack://open?team={team}"


def _open_uri(uri: str) -> dict:
    """Hand *uri* to the OS protocol handler. Factored out so tests can
    monkeypatch it — opening a URI must never happen in tests."""
    try:
        audit("slack_open_via_protocol", {"uri": uri})
        if hasattr(os, "startfile"):  # Windows
            os.startfile(uri)  # type: ignore[attr-defined]
        else:
            opener = "open" if sys.platform == "darwin" else "xdg-open"
            subprocess.Popen([opener, uri], close_fds=True)
        return ok(opened=True, uri=uri)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def register(mcp):
    """Register the ``slack_*`` tools on a FastMCP server."""

    @mcp.tool()
    def slack_status() -> dict:
        """Report whether Slack is installed on this machine, where its
        executable lives, and which automation strategy applies (UI /
        protocol — the desktop GUI has no local API)."""
        return _common.catalog_status(APP_ID)

    @mcp.tool()
    def slack_launch() -> dict:
        """Launch the installed Slack desktop app. Fails with a clear
        error when Slack is not installed."""
        return _common.catalog_launch(APP_ID)

    @mcp.tool()
    def slack_focus() -> dict:
        """Bring an already-open Slack window to the front (Windows UI
        Automation). Fails when no Slack window is open or on
        non-Windows machines."""
        return _common.focus_window(WINDOW_TITLE)

    @mcp.tool()
    def slack_open_via_protocol(team: str = "") -> dict:
        """Open Slack through its ``slack://`` protocol URI.

        With no argument this opens Slack itself (``slack://``). Pass a
        workspace *team id* (e.g. "T0123ABCD" — letters, digits, '-',
        '_' or '.' only; not a workspace name or a URL) to open that
        specific workspace via ``slack://open?team=<team>``, Slack's
        documented open-team deep link. Invalid team ids are rejected
        with an error instead of building a malformed URI.
        """
        try:
            uri = build_protocol_uri(team)
        except Exception as exc:  # noqa: BLE001 — tools never raise
            return err(exc)
        return _open_uri(uri)

    return ["slack_status", "slack_launch", "slack_focus",
            "slack_open_via_protocol"]
