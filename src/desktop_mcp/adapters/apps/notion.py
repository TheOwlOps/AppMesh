"""Notion adapter — catalog status/launch, validated page opening, focus.

Strategy from the catalog is ``ui``: the Notion desktop app (Notion.exe)
is an Electron wrapper with no local API — the official Notion API is
cloud-side. This adapter therefore covers the basics an agent can do
locally:

- ``notion_status``    — is Notion installed, and where?
- ``notion_launch``    — start the Notion desktop app.
- ``notion_open_page`` — open one Notion page URL in the default handler
  (the desktop app registers itself for notion.so links on Windows;
  elsewhere the system browser opens it). Only genuine Notion URLs are
  accepted — see ``_is_notion_url``.
- ``notion_focus``     — bring the Notion window to the front (Windows
  UI Automation; graceful error elsewhere).

All tools return plain ``core.ok`` / ``core.err`` dicts and never raise.
"""
from __future__ import annotations

import os
import sys
from urllib.parse import urlparse

from ._common import audit, catalog_launch, catalog_status, err, focus_window, ok

APP_ID = "notion"

# Hosts that may be opened directly: Notion's main web app domains.
_ALLOWED_HOSTS = frozenset({"notion.so", "www.notion.so"})
# Public Notion Sites live on subdomains of notion.site
# (e.g. team.notion.site); the bare domain is Notion-owned too.
_NOTION_SITE_DOMAIN = "notion.site"


# --------------------------------------------------------------- helpers

def _is_notion_url(url) -> bool:
    """Return True only for https URLs on a genuine Notion host.

    Pure validation helper. The scheme must be ``https`` and the host
    (as parsed by :func:`urllib.parse.urlparse`, so userinfo/port tricks
    cannot smuggle a different host past the check) must be:

    - ``notion.so`` or ``www.notion.so``, or
    - ``notion.site`` or a subdomain of it (``*.notion.site``).

    Anything else — ``http://``, lookalikes such as ``evilnotion.so`` or
    ``notion.so.evil.com``, other domains, or unparseable input — is
    rejected.
    """
    try:
        if not isinstance(url, str) or not url.strip():
            return False
        parsed = urlparse(url)
        if parsed.scheme.lower() != "https":
            return False
        host = (parsed.hostname or "").lower()
        if not host:
            return False
        if host in _ALLOWED_HOSTS:
            return True
        return host == _NOTION_SITE_DOMAIN or host.endswith(f".{_NOTION_SITE_DOMAIN}")
    except Exception:  # noqa: BLE001 — a validator must never raise
        return False


def _open_url(url: str) -> None:
    """Open *url* with the operating system's default handler.

    Factored out at module level so tests can monkeypatch it without
    opening anything. On Windows ``os.startfile`` hands the URL to the
    registered handler (the Notion desktop app, when installed);
    elsewhere the standard ``webbrowser`` module is used.
    """
    if sys.platform.startswith("win"):
        os.startfile(url)  # type: ignore[attr-defined]  # Windows-only
    else:
        import webbrowser

        webbrowser.open(url)


def open_page(url: str) -> dict:
    """Open one Notion page URL after strict validation (see
    ``_is_notion_url``). Invalid URLs are refused without opening
    anything; opener failures are returned as errors, never raised."""
    try:
        if not _is_notion_url(url):
            return err(
                "Refusing to open non-Notion URL: "
                f"{url!r}. Only https:// URLs on notion.so, "
                "www.notion.so or a subdomain of notion.site are allowed."
            )
        _open_url(url)
        audit("notion_open_page", {"url": url})
        return ok(opened=True, url=url)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


# -------------------------------------------------------------- register

def register(mcp):
    """Register the notion_* tools on a FastMCP server."""

    @mcp.tool()
    def notion_status() -> dict:
        """Check whether Notion is installed on this machine: returns
        the installed flag, executable path, how it was found and the
        catalog automation strategy ("ui" — the desktop app has no
        local API)."""
        return catalog_status(APP_ID)

    @mcp.tool()
    def notion_launch() -> dict:
        """Launch the Notion desktop app (Notion.exe). Fails with a
        clear error when Notion is not installed. The launch is
        audit-logged."""
        return catalog_launch(APP_ID)

    @mcp.tool()
    def notion_open_page(url: str) -> dict:
        """Open a Notion page in the desktop app / default browser.

        Only https:// URLs on notion.so, www.notion.so or a subdomain
        of notion.site (e.g. team.notion.site — public Notion Sites)
        are accepted; every other URL is rejected without opening
        anything. Successful opens are written to the audit log.
        Returns {"ok": true, "opened": true, "url": url} on success.
        """
        return open_page(url)

    @mcp.tool()
    def notion_focus() -> dict:
        """Bring the Notion window to the front so the user (or UI
        automation) can interact with it. Requires Windows UI
        Automation and a Notion window that is already open; returns a
        graceful error otherwise."""
        return focus_window("Notion")

    return ["notion_status", "notion_launch", "notion_open_page", "notion_focus"]
