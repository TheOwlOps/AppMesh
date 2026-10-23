"""Telegram Desktop adapter — UI tier plus the ``tg://`` protocol.

Telegram Desktop (catalog id ``telegram``, strategy ``ui``) has no local
automation API: the desktop GUI is driven with Windows UI Automation, and
real programmatic messaging goes through the Bot API / MTProto cloud-side.
What this adapter adds on top of the shared catalog helpers is Telegram's
own protocol link: ``tg://resolve?domain=<username>`` opens a chat with a
user, group or channel by username in the installed desktop app.

Tools (all return plain ``core.ok`` / ``core.err`` dicts, never raise):

- ``telegram_status``    catalog detection + strategy info,
- ``telegram_launch``    launch the installed app from the catalog,
- ``telegram_focus``     bring the Telegram window to the front (Windows),
- ``telegram_open_chat`` open a chat by username via a ``tg://`` URI.

The URI launch itself is factored behind the module-level :func:`_open_uri`
so tests can monkeypatch it — no test ever opens a real chat.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import webbrowser

from ...core import IS_WINDOWS, audit, err, ok
from . import _common

APP_ID = "telegram"
APP_NAME = "Telegram Desktop"  # catalog display name; used for window focus

_MAX_USERNAME_LEN = 64
# Conservative username shape: letters, digits, underscore, dot, dash only.
# This deliberately rejects spaces, slashes, "?", "&", "@", ":" and any
# URL-ish input such as "https://t.me/<name>" — the tool takes a bare
# username, never a link.
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")


def build_resolve_uri(username: str) -> str:
    """Build the ``tg://`` URI that opens a chat with *username*.

    Pure builder (no validation, no side effects): callers are expected
    to validate with :func:`_username_error` first, as
    ``telegram_open_chat`` does.
    """
    return f"tg://resolve?domain={username}"


def _username_error(username) -> str | None:
    """Return a human-readable validation error for *username*, else None."""
    if not isinstance(username, str) or not username:
        return "username must be a non-empty string (the bare Telegram " \
               "username, without a leading '@')."
    if len(username) > _MAX_USERNAME_LEN:
        return f"username is too long: {len(username)} characters " \
               f"(maximum {_MAX_USERNAME_LEN})."
    if not _USERNAME_RE.match(username):
        return f"Invalid username {username!r}: only letters, digits, " \
               "underscore, dot and dash are allowed — pass the bare " \
               "username, not a t.me link or a URL."
    return None


def _open_uri(uri: str) -> None:
    """Hand *uri* to the OS protocol handler. Factored out for tests.

    Windows uses ``os.startfile`` (the same mechanism the system tools
    use for documents); macOS uses ``open``; everywhere else falls back
    to :mod:`webbrowser`, which dispatches to the registered handler.
    Raises RuntimeError when no handler could be invoked.
    """
    if IS_WINDOWS and hasattr(os, "startfile"):
        os.startfile(uri)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", uri],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif not webbrowser.open(uri):
        raise RuntimeError(f"No protocol handler could open {uri!r}.")


def open_chat(username: str) -> dict:
    """Validate *username* and open its chat via the ``tg://`` URI."""
    try:
        problem = _username_error(username)
        if problem is not None:
            return err(problem)
        uri = build_resolve_uri(username)
        _open_uri(uri)
        audit("telegram_open_chat", {"app_id": APP_ID, "username": username,
                                     "uri": uri})
        return ok(app_id=APP_ID, username=username, uri=uri, opened=True)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def register(mcp):
    """Bind the telegram_* tools on *mcp*. Returns the catalog app id."""

    @mcp.tool()
    def telegram_status() -> dict:
        """Report whether Telegram Desktop is installed on this machine,
        where its executable lives, and its catalog automation strategy
        ("ui": the desktop GUI has no local API; messaging automation
        goes through the Bot API / MTProto cloud-side)."""
        return _common.catalog_status(APP_ID)

    @mcp.tool()
    def telegram_launch() -> dict:
        """Launch the installed Telegram Desktop app. Fails with a clear
        error when Telegram is not installed on this machine."""
        return _common.catalog_launch(APP_ID)

    @mcp.tool()
    def telegram_focus() -> dict:
        """Bring the Telegram Desktop window to the front (Windows UI
        Automation). Fails gracefully on non-Windows machines or when no
        Telegram window is currently open."""
        return _common.focus_window(APP_NAME)

    @mcp.tool()
    def telegram_open_chat(username: str) -> dict:
        """Open a Telegram chat by username in the desktop app.

        ``username`` is the bare public username (letters, digits,
        underscore, dot, dash; 1-64 characters) — no leading "@", no
        t.me link, no URL. Works for users, groups and channels that
        have a public username. Requires Telegram Desktop to be
        installed (it registers the tg:// protocol handler)."""
        return open_chat(username)

    return APP_ID
