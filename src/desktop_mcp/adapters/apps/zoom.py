"""Zoom adapter — CLI/protocol join strategy.

Zoom has no local automation API worth driving: the supported, stable
route is its protocol handlers. Joining a meeting is done by opening
either the meeting's ``https://*.zoom.us/j/<id>`` web link (which hands
off to the desktop client) or a ``zoommtg://zoom.us/join?confno=<id>``
deep link built from a bare meeting ID.

Because both forms hand a URI to the OS opener, the builder is strict:
only genuine Zoom HTTPS join links and bare 9-11 digit meeting IDs are
accepted. Any other host/scheme/input is rejected with an err dict, so
a meeting string can never smuggle an arbitrary URL to the opener.

Tools (all return plain ``core.ok``/``core.err`` dicts, never raise):
- ``zoom_status`` — catalog detection/strategy info,
- ``zoom_launch`` — launch the installed Zoom app from the catalog,
- ``zoom_join``   — validate/build a join URI and open it.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import webbrowser
from urllib.parse import urlparse

from ...core import audit, err, ok
from . import _common

APP_ID = "zoom"

# Meeting IDs are 9-11 digits; spaces/dashes are the usual separators
# when people paste them ("123 456 7890", "123-456-7890").
_ID_DIGITS_RE = re.compile(r"\d{9,11}")
_BARE_ID_RE = re.compile(r"[0-9 \-]+")
_JOIN_PATH_RE = re.compile(r"/j/(\d{9,11})/?")
# Conservative query charset for links like ?pwd=<token>: alphanumerics
# plus percent-encoding and the few separators Zoom actually uses.
_QUERY_RE = re.compile(r"[A-Za-z0-9%=&\-_]*")
_SCHEME_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*:")
_FORBIDDEN_URL_CHARS = set('\\<>"\'`')


def build_join_uri(meeting) -> str | dict:
    """Build (or validate) the URI to open for *meeting*. Pure.

    Returns the URI string on success, or a ``core.err`` dict:

    - ``https`` URL: accepted **unchanged** only when the host is
      ``zoom.us`` or a ``*.zoom.us`` subdomain, the path is exactly
      ``/j/<9-11 digits>`` (optional trailing slash) and the query, if
      any, uses only alphanumerics, ``%``, ``=``, ``&``, ``-``, ``_``
      (e.g. ``?pwd=...``). No userinfo, no non-443 port, no fragment.
    - Bare meeting ID: digits, spaces and dashes only; the separators
      are stripped and the result must be 9-11 digits, yielding
      ``zoommtg://zoom.us/join?confno=<digits>``.
    - Everything else (other hosts, ``http:``, ``javascript:``,
      non-HTTPS schemes, empty input, letters in an ID, too short/long
      IDs) is rejected.
    """
    try:
        if not isinstance(meeting, str):
            return err(f"Meeting must be a string, got {type(meeting).__name__}.")
        text = meeting.strip()
        if not text:
            return err("Meeting is empty: pass a Zoom join URL or a meeting ID.")

        if text.lower().startswith("https://"):
            return _validate_https_join_url(text)
        if "://" in text or _SCHEME_RE.match(text):
            return err(
                f"Unsupported meeting link: {text!r}. Only https://*.zoom.us/j/<id> "
                "links or a bare numeric meeting ID are accepted.")

        # Bare meeting ID.
        if not _BARE_ID_RE.fullmatch(text):
            return err(
                f"Invalid meeting ID: {text!r}. A meeting ID is 9-11 digits "
                "(spaces and dashes are allowed as separators).")
        digits = text.replace(" ", "").replace("-", "")
        if not _ID_DIGITS_RE.fullmatch(digits):
            return err(
                f"Invalid meeting ID: {text!r}. After removing spaces/dashes it "
                f"must be 9-11 digits, got {len(digits)}.")
        return f"zoommtg://zoom.us/join?confno={digits}"
    except Exception as exc:  # noqa: BLE001 — builder never raises
        return err(exc)


def _validate_https_join_url(text: str) -> str | dict:
    """Return *text* unchanged iff it is a genuine Zoom join link."""
    if any(ch.isspace() for ch in text) or _FORBIDDEN_URL_CHARS.intersection(text):
        return err(f"Invalid Zoom join URL: {text!r}.")
    try:
        parsed = urlparse(text)
        host = parsed.hostname  # lower-cased by urlparse; None if absent
        port = parsed.port  # raises ValueError on a malformed port
    except Exception as exc:  # noqa: BLE001
        return err(f"Invalid Zoom join URL: {text!r} ({exc}).")
    if parsed.scheme.lower() != "https":
        return err(f"Only https Zoom join URLs are accepted: {text!r}.")
    if host is None or not (host == "zoom.us" or host.endswith(".zoom.us")):
        return err(f"Not a Zoom URL: host {host!r} is not zoom.us or a *.zoom.us subdomain.")
    if parsed.username or parsed.password:
        return err("Zoom join URLs with embedded credentials are not accepted.")
    if port is not None and port != 443:
        return err(f"Unexpected port in Zoom join URL: {port}.")
    if parsed.fragment:
        return err("Zoom join URLs with a #fragment are not accepted.")
    if not _JOIN_PATH_RE.fullmatch(parsed.path or ""):
        return err(
            f"Not a Zoom join URL: path {parsed.path!r} is not /j/<9-11 digit meeting ID>.")
    if not _QUERY_RE.fullmatch(parsed.query or ""):
        return err("Zoom join URL has characters outside the allowed query charset "
                   "(alphanumerics, %, =, &, -, _).")
    return text


def _open_uri(uri: str) -> bool:
    """Open *uri* with the OS handler. Factored out for tests.

    Windows uses ``os.startfile`` (the ``zoommtg://`` / ``https``
    protocol handler); elsewhere ``webbrowser.open`` with an
    ``xdg-open``/``open`` subprocess fallback. Raises on failure —
    callers convert that to an err dict.
    """
    if sys.platform.startswith("win") and hasattr(os, "startfile"):
        os.startfile(uri)  # type: ignore[attr-defined]  # Windows-only
        return True
    if webbrowser.open(uri):
        return True
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    subprocess.run([opener, uri], check=True, timeout=15)
    return True


# ------------------------------------------------------------------- tools

def zoom_status() -> dict:
    """Catalog status for Zoom (delegates to _common.catalog_status)."""
    try:
        return _common.catalog_status(APP_ID)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def zoom_launch() -> dict:
    """Launch Zoom (delegates to _common.catalog_launch)."""
    try:
        return _common.catalog_launch(APP_ID)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def zoom_join(meeting: str) -> dict:
    """Validate/build the join URI for *meeting* and open it."""
    try:
        built = build_join_uri(meeting)
        if isinstance(built, dict):  # err dict from the builder
            return built
        _open_uri(built)
        audit("zoom_join", {"app_id": APP_ID, "uri": built})
        return ok(app_id=APP_ID, joined=True, opened=True, uri=built)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def register(mcp) -> list[str]:
    """Register the zoom_* tools on a FastMCP server. Returns tool names."""
    _g = globals()
    _status, _launch, _join = _g["zoom_status"], _g["zoom_launch"], _g["zoom_join"]

    @mcp.tool()
    def zoom_status() -> dict:
        """Report whether Zoom is installed on this machine, where its
        executable was found, and its catalog automation strategy
        (CLI/protocol join via zoommtg:// and https join links)."""
        return _status()

    @mcp.tool()
    def zoom_launch() -> dict:
        """Launch the Zoom desktop app. Fails with a clear error when
        Zoom is not installed on this machine."""
        return _launch()

    @mcp.tool()
    def zoom_join(meeting: str) -> dict:
        """Join a Zoom meeting. *meeting* is either a full Zoom join
        link (https://zoom.us/j/<id> or a *.zoom.us subdomain link,
        optionally with ?pwd=...) or a bare meeting ID of 9-11 digits
        (spaces/dashes allowed, e.g. "123 456 7890"). Anything else —
        other hosts, http/javascript links, malformed IDs — is
        rejected without opening anything."""
        return _join(meeting)

    return ["zoom_status", "zoom_launch", "zoom_join"]
