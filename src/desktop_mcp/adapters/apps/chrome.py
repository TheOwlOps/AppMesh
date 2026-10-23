"""Google Chrome adapter — catalog status/launch plus URL opening.

Strategy from the catalog is ``cli``: Chrome is driven by launching the
executable with arguments (a URL to open, ``--version`` to probe). Deeper
control via the Chrome DevTools Protocol is possible later by launching
with ``--remote-debugging-port``; this adapter covers the basics:

- ``chrome_status``  — is Chrome installed, and where?
- ``chrome_launch``  — start Chrome (no arguments).
- ``chrome_open_url`` — open one http(s) URL in Chrome.
- ``chrome_version`` — report the installed Chrome version string.

All tools return plain ``core.ok`` / ``core.err`` dicts and never raise.
"""
from __future__ import annotations

import subprocess
from urllib.parse import urlparse

from ... import apps_catalog
from ...core import audit, err, ok
from ._common import catalog_launch, catalog_status, run_cli

APP_ID = "chrome"


# --------------------------------------------------------------- helpers
# Factored out (and resolved through module globals at call time) so
# tests can monkeypatch detection and spawning without a real browser.

def _exe_path() -> str | None:
    """Absolute path of the Chrome executable, or None if not installed."""
    det = apps_catalog.detect_app(APP_ID)
    if not det.get("ok") or not det.get("installed"):
        return None
    return det.get("path")


def _spawn(argv: list[str]):
    """Start *argv* as a detached process. Monkeypatched in tests."""
    return subprocess.Popen(argv, close_fds=True)


def _validate_url(url) -> str | None:
    """Return an error message when *url* is not a safe http(s) URL."""
    if not isinstance(url, str) or not url.strip():
        return "URL must be a non-empty string."
    parsed = urlparse(url.strip())
    if parsed.scheme.lower() not in ("http", "https"):
        return (f"Only http:// and https:// URLs may be opened in Chrome; "
                f"got scheme {parsed.scheme!r} in {url!r}.")
    if not parsed.netloc:
        return f"URL has no host: {url!r}."
    return None


# ------------------------------------------------------------ operations

def open_url(url: str) -> dict:
    """Open *url* in Chrome. Only http/https URLs are accepted."""
    try:
        problem = _validate_url(url)
        if problem:
            return err(problem)
        url = url.strip()
        exe = _exe_path()
        if not exe:
            return err("Google Chrome is not installed (chrome.exe was not "
                       "found on this machine).")
        argv = [exe, url]
        proc = _spawn(argv)
        audit("chrome_open_url", {"url": url, "path": exe,
                                  "pid": getattr(proc, "pid", None)})
        return ok(app_id=APP_ID, opened=True, url=url, path=exe,
                  pid=getattr(proc, "pid", None), argv=argv)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def get_version() -> dict:
    """Run ``chrome --version`` and report the version string."""
    try:
        exe = _exe_path()
        if not exe:
            return err("Google Chrome is not installed (chrome.exe was not "
                       "found on this machine).")
        res = run_cli([exe, "--version"])
        if not res.get("ok"):
            return res
        text = (res.get("stdout") or "").strip()
        version = text.splitlines()[0].strip() if text else ""
        return ok(app_id=APP_ID, version=version, raw=text, path=exe)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


# -------------------------------------------------------------- register

def register(mcp):
    """Register the chrome_* tools on a FastMCP server."""

    @mcp.tool()
    def chrome_status() -> dict:
        """Check whether Google Chrome is installed on this machine:
        returns installed flag, executable path, how it was found and
        the catalog automation strategy."""
        return catalog_status(APP_ID)

    @mcp.tool()
    def chrome_launch() -> dict:
        """Launch Google Chrome (no page). Fails with a clear error
        when Chrome is not installed. The launch is audit-logged."""
        return catalog_launch(APP_ID)

    @mcp.tool()
    def chrome_open_url(url: str) -> dict:
        """Open a web page in Google Chrome. Only http:// and https://
        URLs are accepted — file:, javascript: and other schemes are
        rejected. Starts Chrome if it is not already running."""
        return open_url(url)

    @mcp.tool()
    def chrome_version() -> dict:
        """Report the installed Google Chrome version by running
        ``chrome --version``. Returns a graceful error when Chrome is
        not installed or the probe fails."""
        return get_version()

    return ["chrome_status", "chrome_launch", "chrome_open_url",
            "chrome_version"]
