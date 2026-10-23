"""Mozilla Firefox adapter — catalog status/launch plus URL opening.

Strategy from the catalog is ``cli``: Firefox is driven by launching the
executable with arguments (a URL to open, ``--version`` to probe).
Deeper control via Marionette / the Remote Agent
(``--remote-debugging-port`` gives a CDP subset) is possible later;
this adapter covers the basics:

- ``firefox_status``  — is Firefox installed, and where?
- ``firefox_launch``  — start Firefox (no arguments).
- ``firefox_open_url`` — open one http(s) URL in Firefox.
- ``firefox_version`` — report the installed Firefox version string.

All tools return plain ``core.ok`` / ``core.err`` dicts and never raise.
"""
from __future__ import annotations

import subprocess
from urllib.parse import urlparse

from ... import apps_catalog
from ...core import audit, err, ok
from ._common import catalog_launch, catalog_status, run_cli

APP_ID = "firefox"


# --------------------------------------------------------------- helpers
# Factored out (and resolved through module globals at call time) so
# tests can monkeypatch detection and spawning without a real browser.

def _exe_path() -> str | None:
    """Absolute path of the Firefox executable, or None if not installed."""
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
        return (f"Only http:// and https:// URLs may be opened in Firefox; "
                f"got scheme {parsed.scheme!r} in {url!r}.")
    if not parsed.netloc:
        return f"URL has no host: {url!r}."
    return None


# ------------------------------------------------------------ operations

def open_url(url: str) -> dict:
    """Open *url* in Firefox. Only http/https URLs are accepted."""
    try:
        problem = _validate_url(url)
        if problem:
            return err(problem)
        url = url.strip()
        exe = _exe_path()
        if not exe:
            return err("Mozilla Firefox is not installed (firefox.exe was "
                       "not found on this machine).")
        argv = [exe, url]
        proc = _spawn(argv)
        audit("firefox_open_url", {"url": url, "path": exe,
                                   "pid": getattr(proc, "pid", None)})
        return ok(app_id=APP_ID, opened=True, url=url, path=exe,
                  pid=getattr(proc, "pid", None), argv=argv)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def get_version(runner=None) -> dict:
    """Run ``firefox --version`` and report the version string.

    ``runner`` is injectable for tests and forwarded to
    :func:`_common.run_cli` (a callable(argv, timeout) returning
    (returncode, stdout, stderr)); when omitted, the real run_cli is
    used and the module-level ``run_cli`` name can be monkeypatched.
    """
    try:
        exe = _exe_path()
        if not exe:
            return err("Mozilla Firefox is not installed (firefox.exe was "
                       "not found on this machine).")
        if runner is not None:
            res = run_cli([exe, "--version"], runner=runner)
        else:
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
    """Register the firefox_* tools on a FastMCP server."""

    @mcp.tool()
    def firefox_status() -> dict:
        """Check whether Mozilla Firefox is installed on this machine:
        returns installed flag, executable path, how it was found and
        the catalog automation strategy."""
        return catalog_status(APP_ID)

    @mcp.tool()
    def firefox_launch() -> dict:
        """Launch Mozilla Firefox (no page). Fails with a clear error
        when Firefox is not installed. The launch is audit-logged."""
        return catalog_launch(APP_ID)

    @mcp.tool()
    def firefox_open_url(url: str) -> dict:
        """Open a web page in Mozilla Firefox. Only http:// and
        https:// URLs are accepted — file:, javascript: and other
        schemes are rejected. Starts Firefox if it is not already
        running."""
        return open_url(url)

    @mcp.tool()
    def firefox_version() -> dict:
        """Report the installed Mozilla Firefox version by running
        ``firefox --version``. Returns a graceful error when Firefox
        is not installed or the probe fails."""
        return get_version()

    return ["firefox_status", "firefox_launch", "firefox_open_url",
            "firefox_version"]
