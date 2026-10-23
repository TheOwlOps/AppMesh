"""Microsoft Teams adapter — UI strategy + ``msteams://`` protocol.

Catalog id: ``teams`` (display name "Microsoft Teams"). Teams has no
local COM/plugin API for the desktop GUI — real content automation goes
through Microsoft Graph cloud-side — so this adapter covers what is
honestly possible locally:

- ``teams_status``  — is Teams installed? (catalog detection)
- ``teams_launch``  — start the installed Teams executable (catalog)
- ``teams_focus``   — bring an open Teams window to the front (UIA)
- ``teams_open_via_protocol`` — open Teams via its ``msteams://`` URI

All tools return plain ``core.ok`` / ``core.err`` dicts and never raise.
"""
from __future__ import annotations

import os
import re

from . import _common
from ._common import audit, err, ok

APP_ID = "teams"
APP_NAME = "Microsoft Teams"

# Conservative charset for a would-be protocol target token: letters,
# digits, dash, underscore and dot only. Spaces, slashes, "?", "&",
# ":" (and therefore full URLs) are all rejected.
_TARGET_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def build_protocol_uri(target: str = "") -> str:
    """Build the ``msteams://`` URI for :func:`teams_open_via_protocol`.

    Pure builder (no I/O, no launching):

    - An empty/blank *target* returns exactly ``"msteams://"``, the
      documented URI that simply opens Microsoft Teams.
    - A non-empty *target* is validated conservatively first: only
      alphanumerics, dash, underscore and dot are allowed, so values
      like ``"a b"``, ``"a/b"``, ``"a?b"``, ``"a&b"`` or a full URL
      such as ``"https://teams.microsoft.com"`` raise ``ValueError``.
    - Even a charset-valid bare token is rejected with ``ValueError``:
      Microsoft documents structured deep links (team/channel/chat ids
      in a path plus query parameters), but *no* documented form takes
      a single bare token after ``msteams://``. Rather than inventing
      one — or silently dropping the target — this builder refuses it.
      Call with no target to just open Teams.

    Raises:
        ValueError: for any non-empty target, with a message saying why.
    """
    text = "" if target is None else str(target).strip()
    if not text:
        return "msteams://"
    if not _TARGET_RE.match(text):
        raise ValueError(
            f"Invalid Teams protocol target {target!r}: only alphanumerics, "
            "dash, underscore and dot are allowed (no spaces, slashes, "
            "'?', '&' or full URLs).")
    raise ValueError(
        f"Unsupported Teams protocol target {target!r}: no documented "
        "msteams:// form takes a bare token — deep links need structured "
        "team/channel/chat ids. Call with an empty target to open Teams.")


def _open_uri(uri: str) -> None:
    """Open *uri* with the OS default handler (Windows: ``os.startfile``).

    Factored out as the monkeypatch point for tests. Raises on failure
    or on non-Windows platforms; callers convert that to an err dict.
    """
    if not _common.IS_WINDOWS:
        raise RuntimeError(
            "Opening msteams:// URIs uses the Windows shell; "
            "this machine is not running Windows.")
    os.startfile(uri)  # type: ignore[attr-defined]  # Windows-only API


def register(mcp):
    """Register the teams_* tools on a FastMCP server."""

    @mcp.tool()
    def teams_status() -> dict:
        """Check whether Microsoft Teams is installed on this machine.

        Returns the catalog detection result for Teams: installed flag,
        executable path (when found), how it was found, and its
        automation strategy ("ui" — the GUI has no local API; content
        automation goes through Microsoft Graph cloud-side).
        """
        try:
            return _common.catalog_status(APP_ID)
        except Exception as exc:  # noqa: BLE001 — tools never raise
            return err(exc)

    @mcp.tool()
    def teams_launch() -> dict:
        """Launch the installed Microsoft Teams desktop app.

        Fails with a clear error when Teams is not installed on this
        machine. The launch is written to the audit log.
        """
        try:
            return _common.catalog_launch(APP_ID)
        except Exception as exc:  # noqa: BLE001 — tools never raise
            return err(exc)

    @mcp.tool()
    def teams_focus() -> dict:
        """Bring an already-open Microsoft Teams window to the front.

        Uses Windows UI Automation to find a window whose title
        contains "Microsoft Teams" and focus it. Fails with a clear
        error when no such window is open or on non-Windows machines.
        """
        try:
            return _common.focus_window(APP_NAME)
        except Exception as exc:  # noqa: BLE001 — tools never raise
            return err(exc)

    @mcp.tool()
    def teams_open_via_protocol(target: str = "") -> dict:
        """Open Microsoft Teams via its ``msteams://`` protocol URI.

        With no target this opens Teams to its home view using exactly
        the URI ``msteams://``. A non-empty target is NOT accepted:
        no documented msteams:// form takes a bare token (deep links
        need structured team/channel/chat ids), so instead of inventing
        a URI this returns an error explaining that — and targets with
        spaces, slashes, "?", "&" or a full URL are rejected outright.
        Windows only (the URI is handed to the Windows shell).
        """
        try:
            uri = build_protocol_uri(target)
            _open_uri(uri)
            audit("teams_open_via_protocol", {"app_id": APP_ID, "uri": uri})
            return ok(app_id=APP_ID, name=APP_NAME, opened=True, uri=uri)
        except Exception as exc:  # noqa: BLE001 — tools never raise
            return err(exc)

    return mcp
