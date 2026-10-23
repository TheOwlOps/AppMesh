"""Everything (voidtools) adapter — instant file search via the es.exe CLI.

Strategy (catalog id ``"everything"``, strategy ``"cli"``): Everything
itself is a GUI indexer, but voidtools ships a small command-line client,
``es.exe``, that talks to the running Everything service and prints one
matching path per line. This adapter wraps that CLI:

- ``everything_status`` — catalog detection (is Everything installed?).
- ``everything_launch`` — launch Everything via the catalog.
- ``everything_search`` — run ``es -n <max> <query>`` through
  :func:`desktop_mcp.adapters.apps._common.run_cli` and return the
  printed paths as a list.

Honest failure mode: ``es.exe`` is a *separate* download / component
from voidtools — it is not always installed alongside Everything.exe, and
the catalog only probes for ``Everything.exe``. So a machine can have
Everything installed yet have no ``es`` on PATH. When that happens the
search tool returns a clear error explaining that ``es.exe`` ships with
Everything from voidtools and must be on PATH (or in the Everything
install folder) instead of crashing or inventing results.

All tools return ``core.ok`` / ``core.err`` plain dicts and never raise.
"""
from __future__ import annotations

from . import _common
from ._common import catalog_launch, catalog_status, err, ok, run_cli

APP_ID = "everything"

MAX_RESULTS_LIMIT = 500

# Original references, used by _resolve() so that monkeypatching *either*
# this module's imported names (everything.run_cli) *or* the _common
# module (_common.run_cli) is honoured at call time.
_ORIGINALS = {
    "catalog_status": catalog_status,
    "catalog_launch": catalog_launch,
    "run_cli": run_cli,
}


def _resolve(name: str):
    """Return the current implementation for *name*.

    Normally this is the function imported from ``_common``. If a test
    (or another caller) monkeypatched either ``everything.<name>`` or
    ``_common.<name>``, the patched version wins.
    """
    original = _ORIGINALS[name]
    local = globals().get(name)
    if local is not None and local is not original:
        return local
    shared = getattr(_common, name, None)
    if shared is not None and shared is not original:
        return shared
    return original


_ES_MISSING_HINT = (
    "es.exe is the Everything search command-line client from voidtools: "
    "it ships with Everything (voidtools) but is a separate small program "
    "from Everything.exe. To use everything_search, put es.exe on PATH "
    "or copy it into the Everything install folder and add that folder "
    "to PATH, and make sure the Everything app/service is running."
)


# ---------------------------------------------------------------------------
# Pure argv builder
# ---------------------------------------------------------------------------

def _clamp_max_results(max_results) -> int:
    """Coerce *max_results* to an int clamped to 1..500 (default 20)."""
    try:
        n = int(max_results)
    except (TypeError, ValueError):
        n = 20
    return max(1, min(MAX_RESULTS_LIMIT, n))


def build_es_argv(query, max_results=20) -> list:
    """Build the es.exe argv for a search. Pure function.

    Returns ``["es", "-n", str(max_results), query]`` — ``argv[0]`` is
    the bare name ``"es"`` so it is resolved via PATH by
    ``_common.run_cli`` (matching how ``run_cli`` checks
    ``shutil.which(argv[0])``). ``max_results`` is clamped to 1..500.

    Raises ValueError when *query* is not a non-empty string; the tool
    wrapper converts that into an ``err`` dict.
    """
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    return ["es", "-n", str(_clamp_max_results(max_results)), query]


# ---------------------------------------------------------------------------
# Tool implementations (module-level, testable without an MCP server)
# ---------------------------------------------------------------------------

def everything_status() -> dict:
    """Detection + strategy info for Everything, straight from the catalog."""
    try:
        return _resolve("catalog_status")(APP_ID)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def everything_launch() -> dict:
    """Launch Everything via the catalog."""
    try:
        return _resolve("catalog_launch")(APP_ID)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def _looks_like_missing_es(message: str) -> bool:
    low = (message or "").lower()
    return (
        "not found" in low
        or "no such file" in low
        or "filenotfound" in low
        or "cannot find" in low
        or "not recognized" in low
    )


def everything_search(query, max_results=20, runner=None) -> dict:
    """Search for files/folders with es.exe and return matching paths.

    ``runner`` is an optional test hook forwarded to
    ``_common.run_cli`` (a callable(argv, timeout) returning
    (returncode, stdout, stderr)); it is not part of the MCP schema.
    """
    try:
        argv = build_es_argv(query, max_results)
    except ValueError as exc:
        return err(exc)
    run = _resolve("run_cli")
    try:
        if runner is not None:
            res = run(argv, runner=runner)
        else:
            res = run(argv)
    except Exception as exc:  # noqa: BLE001 — a (fake) run_cli may raise
        return err(f"Failed to run es.exe: {exc}. {_ES_MISSING_HINT}")
    if not isinstance(res, dict):
        return err(f"Unexpected result from run_cli for es.exe: {res!r}")
    if not res.get("ok"):
        message = str(res.get("error") or "es.exe failed")
        if _looks_like_missing_es(message):
            return err(f"{message}. {_ES_MISSING_HINT}")
        return err(message)
    stdout = res.get("stdout") or ""
    results = [line.strip() for line in str(stdout).splitlines() if line.strip()]
    return ok(query=query, results=results, count=len(results), argv=argv)


# ---------------------------------------------------------------------------
# MCP registration
# ---------------------------------------------------------------------------

def register(mcp) -> list:
    """Register the everything_* tools on a FastMCP server.

    Returns the list of tool names bound.
    """
    _g = globals()
    _status = _g["everything_status"]
    _launch = _g["everything_launch"]
    _search = _g["everything_search"]

    @mcp.tool()
    def everything_status() -> dict:
        """Check whether Everything (voidtools) is installed on this
        machine: returns its detected install path and the automation
        strategy (cli, via the es.exe command-line client)."""
        return _status()

    @mcp.tool()
    def everything_launch() -> dict:
        """Launch the Everything (voidtools) desktop app. Fails with a
        clear error when Everything is not installed."""
        return _launch()

    @mcp.tool()
    def everything_search(query: str, max_results: int = 20) -> dict:
        """Instantly search the whole disk for files and folders by name
        using Everything (voidtools) — e.g. query="report.pdf" or a
        partial name / Everything search syntax. Returns up to
        max_results matching full paths (clamped to 1..500). Requires
        Everything to be running and its es.exe command-line client to
        be on PATH."""
        return _search(query, max_results)

    return ["everything_status", "everything_launch", "everything_search"]
