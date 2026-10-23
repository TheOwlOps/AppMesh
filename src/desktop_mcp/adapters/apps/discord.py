"""Discord adapter — UI automation plus the ``discord://`` protocol.

Discord desktop has no COM interface, no plugin API and no local RPC an
outside process may drive: the catalog strategy is ``ui``. What an agent
can honestly do from outside the app is:

- ``discord_status``            — is Discord installed, and where?
- ``discord_launch``            — start it via the catalog launcher,
- ``discord_focus``             — bring its window to the front (Windows
  UI Automation, via ``_common.focus_window``),
- ``discord_open_via_protocol`` — hand a ``discord://`` URI to the OS
  protocol handler Discord registers on install.

Real in-app automation (reading/sending messages) belongs to bots on
Discord's cloud API, not to this desktop adapter.

About ``discord://`` targets — the honest version: the plain URI
``discord://`` reliably just opens/focuses the client. Discord does NOT
publish a documented path grammar for the scheme (the shareable routes
it documents are ``https://discord.com/...`` web URLs, which this
adapter deliberately refuses to smuggle in). This adapter therefore
forwards at most ONE opaque path segment, ``discord://<target>``, and
makes no promise that Discord will navigate anywhere specific for it;
the empty target is the reliable, recommended form.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import webbrowser

from . import _common
from ._common import IS_WINDOWS, audit, err, ok

APP_ID = "discord"
APP_NAME = "Discord"  # catalog display name; also the window-title needle

# One conservative path segment: letters, digits, dash, underscore, dot.
# No spaces, slashes, "?", "&", ":", "#" — so no multi-segment paths, no
# query strings and no full URLs can be smuggled into the URI.
_TARGET_RE = re.compile(r"[A-Za-z0-9._-]+")


def build_protocol_uri(target: str = "") -> str:
    """Build the ``discord://`` URI for *target*. Pure; raises ValueError.

    ``build_protocol_uri("")`` is ``"discord://"`` (just open Discord).
    A non-empty *target* must be a single segment of alphanumerics plus
    ``-``, ``_`` and ``.`` and becomes ``"discord://<target>"`` — an
    opaque segment forwarded untouched (see the module docstring: no
    documented navigation semantics are claimed for it). Anything else
    — spaces, slashes, ``?``, ``&``, ``:``, ``#``, dot-segments, full
    URLs such as ``"https://discord.com/app"`` — raises ValueError.
    """
    if target is None:
        target = ""
    if not isinstance(target, str):
        raise ValueError(f"target must be a string, got {type(target).__name__}.")
    segment = target.strip()
    if not segment:
        return "discord://"
    if segment in (".", "..") or _TARGET_RE.fullmatch(segment) is None:
        raise ValueError(
            f"Invalid discord:// target {target!r}: use a single segment of "
            "letters, digits, '-', '_' and '.' only (no spaces, slashes, "
            "'?', '&' or full URLs). Leave it empty to just open Discord.")
    return f"discord://{segment}"


def _open_uri(uri: str) -> None:
    """Hand *uri* to the OS protocol handler. Raises on failure.

    Factored out as the monkeypatch point for tests: ``os.startfile`` on
    Windows, ``open`` on macOS, ``webbrowser`` elsewhere with an
    ``xdg-open`` subprocess fallback.
    """
    if IS_WINDOWS:
        os.startfile(uri)  # type: ignore[attr-defined]  # Windows only
        return
    if sys.platform == "darwin":
        subprocess.Popen(["open", uri])
        return
    if webbrowser.open(uri):
        return
    subprocess.Popen(["xdg-open", uri])


def register(mcp):
    """Register the discord_* tools on a FastMCP server. Returns APP_ID."""

    @mcp.tool()
    def discord_status() -> dict:
        """Report whether Discord is installed on this machine.

        Returns the catalog detection result: installed flag, executable
        path and how it was found, plus Discord's automation strategy
        ("ui") and display name. Never raises.
        """
        try:
            return _common.catalog_status(APP_ID)
        except Exception as exc:  # noqa: BLE001 — tools never raise
            return err(exc)

    @mcp.tool()
    def discord_launch() -> dict:
        """Launch the Discord desktop app if it is installed.

        Fails with a clear error dict when Discord is not installed.
        The launch is written to the audit log. Never raises.
        """
        try:
            return _common.catalog_launch(APP_ID)
        except Exception as exc:  # noqa: BLE001 — tools never raise
            return err(exc)

    @mcp.tool()
    def discord_focus() -> dict:
        """Bring the Discord window to the front.

        Uses Windows UI Automation to find a window whose title contains
        "Discord" and focus it. Returns an error dict on non-Windows
        machines, when pywinauto is missing, or when no Discord window
        is open. Never raises.
        """
        try:
            return _common.focus_window(APP_NAME)
        except Exception as exc:  # noqa: BLE001 — tools never raise
            return err(exc)

    @mcp.tool()
    def discord_open_via_protocol(target: str = "") -> dict:
        """Open Discord through its registered ``discord://`` protocol.

        With no target this hands the OS ``discord://``, which opens or
        focuses the Discord client — the reliable form. An optional
        *target* may be ONE opaque path segment (letters, digits, '-',
        '_' and '.' only), forwarded as ``discord://<target>``; Discord
        publishes no documented navigation grammar for the scheme, so
        no specific in-app destination is promised for it. Spaces,
        slashes, query strings and full URLs are rejected with an error
        dict and nothing is opened. Never raises.
        """
        try:
            uri = build_protocol_uri(target)
            _open_uri(uri)
            audit("discord_open_via_protocol", {"app_id": APP_ID, "uri": uri})
            return ok(app_id=APP_ID, name=APP_NAME, opened=True,
                      uri=uri, via="protocol")
        except Exception as exc:  # noqa: BLE001 — tools never raise
            return err(exc)

    return APP_ID
