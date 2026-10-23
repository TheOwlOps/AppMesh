"""Obsidian adapter — catalog status/launch plus obsidian:// URI opening.

Strategy from the catalog is ``cli``: Obsidian exposes the
``obsidian://`` URI scheme for opening a vault note, and vaults are
plain Markdown files on disk. This adapter covers:

- ``obsidian_status``          — is Obsidian installed, and where?
- ``obsidian_launch``          — start Obsidian (no note).
- ``obsidian_open_vault_note`` — open one note in a named vault via
  ``obsidian://open?vault=<vault>&file=<note>``.
- ``obsidian_focus``           — bring the Obsidian window to the front.

All tools return plain ``core.ok`` / ``core.err`` dicts and never raise.
"""
from __future__ import annotations

import os
import sys
import urllib.parse

from ._common import audit, catalog_launch, catalog_status, err, focus_window, ok

APP_ID = "obsidian"


# --------------------------------------------------------------- helpers

def build_obsidian_uri(vault: str, note: str) -> str:
    """Build the obsidian:// URI that opens *note* inside *vault*.

    Pure builder — exact format::

        obsidian://open?vault=<quote(vault, safe="")>&file=<quote(note, safe="")>

    Both parts are percent-encoded with ``safe=""`` so spaces, slashes,
    ``#`` and non-ASCII characters in vault or note names cannot break
    out of their query parameter. The ``.md`` extension is NOT forced:
    *note* is used verbatim (Obsidian resolves it with or without it).
    """
    return (
        "obsidian://open?vault="
        + urllib.parse.quote(vault, safe="")
        + "&file="
        + urllib.parse.quote(note, safe="")
    )


def _open_uri(uri: str) -> None:
    """Open *uri* with the operating system's default handler.

    Factored out at module level so tests can monkeypatch it instead of
    launching anything. On Windows ``os.startfile`` dispatches the
    ``obsidian://`` scheme to the registered app; elsewhere
    ``webbrowser.open`` does the equivalent.
    """
    if sys.platform.startswith("win"):
        os.startfile(uri)  # type: ignore[attr-defined]  # Windows-only API
    else:
        import webbrowser

        webbrowser.open(uri)


# ------------------------------------------------------------ operations

def open_vault_note(vault: str, note: str) -> dict:
    """Open *note* in the Obsidian vault named *vault* via its URI."""
    try:
        if not isinstance(vault, str) or not vault.strip():
            return err("Vault name must be a non-empty string.")
        if not isinstance(note, str) or not note.strip():
            return err("Note name/path must be a non-empty string.")
        uri = build_obsidian_uri(vault, note)
        _open_uri(uri)
        audit("obsidian_open_vault_note",
              {"vault": vault, "note": note, "uri": uri})
        return ok(opened=True, uri=uri)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


# -------------------------------------------------------------- register

def register(mcp):
    """Register the obsidian_* tools on a FastMCP server."""

    @mcp.tool()
    def obsidian_status() -> dict:
        """Check whether Obsidian is installed on this machine: returns
        the installed flag, executable path, how it was found and the
        catalog automation strategy (cli — obsidian:// URI scheme)."""
        return catalog_status(APP_ID)

    @mcp.tool()
    def obsidian_launch() -> dict:
        """Launch Obsidian. Fails with a clear error when Obsidian is
        not installed. The launch is written to the audit log."""
        return catalog_launch(APP_ID)

    @mcp.tool()
    def obsidian_open_vault_note(vault: str, note: str) -> dict:
        """Open a note in an Obsidian vault by name.

        Builds ``obsidian://open?vault=<vault>&file=<note>`` (both
        parts URL-encoded) and hands it to the OS, which routes it to
        Obsidian. ``vault`` is the vault's name as shown in Obsidian;
        ``note`` is the note's vault-relative path or title, with or
        without the ``.md`` extension (it is not forced). Both must be
        non-empty. The successful open is written to the audit log.
        """
        return open_vault_note(vault, note)

    @mcp.tool()
    def obsidian_focus() -> dict:
        """Bring the Obsidian window to the front (Windows UI
        Automation). Returns a graceful error when no Obsidian window
        is open or this machine is not running Windows."""
        return focus_window("Obsidian")

    return ["obsidian_status", "obsidian_launch",
            "obsidian_open_vault_note", "obsidian_focus"]
