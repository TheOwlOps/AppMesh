"""OBS Studio adapter (catalog id ``obs-studio``, strategy ``plugin``).

OBS Studio 28+ ships the obs-websocket plugin built in: it listens on
``127.0.0.1:4455`` and gives full remote control (scenes, sources,
recording/streaming state, version info). This adapter exposes:

- ``obs_studio_status``           catalog detection + websocket TCP probe,
- ``obs_studio_launch``           launch the installed app from the catalog,
- ``obs_studio_websocket_status`` real obs-websocket handshake (GetVersion)
  via the optional ``obs-websocket-py`` package (import name
  ``obswebsocket``). When the package is absent the tool returns a clear
  error dict — it never raises.

All tools return ``core.ok`` / ``core.err`` dicts and never raise.
"""
from __future__ import annotations

import socket

from ...apps_catalog import detect_app, get_app  # noqa: F401  (module-level, patchable in tests)
from ._common import catalog_launch, catalog_status, err, ok

APP_ID = "obs-studio"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 4455  # obs-websocket's default listen port (OBS 28+)

_TCP_TIMEOUT = 0.5


# --------------------------------------------------------------------- probe

def _tcp_reachable(host: str, port: int, connector=None, timeout: float = _TCP_TIMEOUT) -> bool:
    """Pure TCP reachability probe: True iff a connection to
    ``(host, port)`` can be opened. Never raises.

    ``connector`` is injectable for tests: a callable with the
    ``socket.create_connection`` signature ``connector((host, port),
    timeout=...)`` returning a closable socket-like object.
    """
    connect = connector if connector is not None else socket.create_connection
    try:
        sock = connect((host, port), timeout=timeout)
    except Exception:  # noqa: BLE001 — unreachable is a result, not an error
        return False
    try:
        close = getattr(sock, "close", None)
        if callable(close):
            close()
    except Exception:  # noqa: BLE001
        pass
    return True


# ------------------------------------------------------- obs-websocket client

def _import_obswebsocket():
    """Import the optional ``obswebsocket`` package (obs-websocket-py)."""
    import obswebsocket  # type: ignore

    return obswebsocket


def _version_info(response) -> dict:
    """Best-effort extraction of version fields from a GetVersion response.

    ``obs-websocket-py`` responses expose ``getObsVersion()`` /
    ``getObsWebSocketVersion()`` methods and a raw ``datain`` dict;
    plain dicts are also accepted so tests/fakes stay simple.
    """
    info: dict = {}
    if response is None:
        return info
    for key, method in (("obs_version", "getObsVersion"),
                        ("websocket_version", "getObsWebSocketVersion")):
        fn = getattr(response, method, None)
        if callable(fn):
            try:
                value = fn()
            except Exception:  # noqa: BLE001
                value = None
            if value:
                info[key] = value
    datain = response if isinstance(response, dict) else getattr(response, "datain", None)
    if isinstance(datain, dict):
        if "obs_version" not in info and datain.get("obsVersion"):
            info["obs_version"] = datain["obsVersion"]
        if "websocket_version" not in info and datain.get("obsWebSocketVersion"):
            info["websocket_version"] = datain["obsWebSocketVersion"]
    return info


def _connect(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
             password: str = "", import_fn=None) -> dict:
    """Connect to OBS over obs-websocket and run a GetVersion request.

    ``import_fn`` is injectable for tests: a zero-arg callable returning
    the ``obswebsocket`` module. Returns an ok dict with the OBS /
    websocket versions on success, or a clear err dict when the package
    is missing or OBS is not reachable — never raises.
    """
    loader = import_fn if import_fn is not None else _import_obswebsocket
    try:
        module = loader()
    except ImportError:
        return err("obs-websocket-py not installed; OBS websocket control "
                   "needs the optional package — install it with: "
                   "pip install obs-websocket-py")
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)

    client = None
    try:
        client = module.obsws(host, port, password)
        client.connect()
        response = client.call(module.requests.GetVersion())
        return ok(app_id=APP_ID, host=host, port=int(port), connected=True,
                  reachable=True, **_version_info(response))
    except Exception as exc:  # noqa: BLE001
        return err(f"Could not reach OBS websocket at {host}:{port}: {exc}. "
                   "Is OBS Studio running with the obs-websocket server "
                   "enabled (Tools > WebSocket Server Settings)?")
    finally:
        if client is not None:
            try:
                client.disconnect()
            except Exception:  # noqa: BLE001
                pass


# --------------------------------------------------------------------- tools

def _status(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> dict:
    """Catalog status for OBS Studio, enriched with websocket reachability."""
    try:
        result = catalog_status(APP_ID)
        if not result.get("ok"):
            return result
        # Refresh the detection fields through this module's own
        # ``detect_app`` reference (same function the catalog uses;
        # kept module-level so tests can substitute it).
        try:
            det = detect_app(APP_ID)
        except Exception:  # noqa: BLE001 — enrichment must not break status
            det = None
        if isinstance(det, dict) and det.get("ok"):
            for key in ("installed", "path", "via", "name"):
                if key in det:
                    result[key] = det[key]
        # catalog_status reads strategy/prog_id off the get_app wrapper,
        # where they are nested under "app" — fill them from the record.
        try:
            rec = get_app(APP_ID)
        except Exception:  # noqa: BLE001 — enrichment must not break status
            rec = None
        if isinstance(rec, dict) and rec.get("ok"):
            record = rec.get("app") or {}
            for key in ("strategy", "prog_id"):
                if record.get(key) is not None:
                    result[key] = record[key]
        reachable = _tcp_reachable(host, port)
        result["websocket"] = {
            "host": host,
            "port": int(port),
            "reachable": reachable,
            "note": ("obs-websocket (OBS 28+, Tools > WebSocket Server "
                     "Settings) listens here when OBS is running."),
        }
        result["websocket_reachable"] = reachable
        return result
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def register(mcp):
    """Register the obs_studio_* tools on a FastMCP server."""

    @mcp.tool()
    def obs_studio_status(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> dict:
        """Report OBS Studio status: whether obs64.exe is installed
        (from the AppMesh catalog) and whether the obs-websocket server
        is reachable over TCP at host:port (default 127.0.0.1:4455,
        the port OBS 28+ listens on when obs-websocket is enabled).
        An unreachable websocket simply reports reachable=false."""
        return _status(host, port)

    @mcp.tool()
    def obs_studio_launch() -> dict:
        """Launch OBS Studio from the AppMesh catalog. Fails with a
        clear error when OBS Studio is not installed on this machine."""
        try:
            return catalog_launch(APP_ID)
        except Exception as exc:  # noqa: BLE001 — tools never raise
            return err(exc)

    @mcp.tool()
    def obs_studio_websocket_status(host: str = DEFAULT_HOST,
                                    port: int = DEFAULT_PORT,
                                    password: str = "") -> dict:
        """Check OBS Studio's real control route: connect to the
        built-in obs-websocket server (OBS 28+, default 127.0.0.1:4455)
        and run a GetVersion request, returning the OBS and websocket
        versions. Needs the optional obs-websocket-py package; when it
        is missing, or OBS is not running / the websocket server is
        disabled, a clear error dict is returned (never an exception).
        Pass the websocket password if one is set in OBS."""
        return _connect(host, port, password)

    return ["obs_studio_status", "obs_studio_launch", "obs_studio_websocket_status"]
