"""Hermetic tests for the Zoom adapter (adapters/apps/zoom.py).

No real app, OS opener or network: URI opening goes through a
monkeypatched ``zoom._open_uri`` and status/launch delegation through
monkeypatched ``_common`` functions.
"""
from __future__ import annotations

import pytest

from desktop_mcp.adapters.apps import _common, zoom
from desktop_mcp.adapters.apps.zoom import build_join_uri, zoom_join, zoom_launch, zoom_status


# ------------------------------------------------------------------ builder

def test_builder_https_join_url_passthrough():
    url = "https://zoom.us/j/123456789"
    assert build_join_uri(url) == url


def test_builder_https_subdomain_and_pwd_query_passthrough():
    url = "https://us02web.zoom.us/j/1234567890?pwd=abc123%3D&uname=Bob-1_x"
    assert build_join_uri(url) == url


def test_builder_bare_id_with_spaces():
    assert build_join_uri("123 456 7890") == \
        "zoommtg://zoom.us/join?confno=1234567890"


def test_builder_bare_id_plain_and_dashed():
    assert build_join_uri("123456789") == \
        "zoommtg://zoom.us/join?confno=123456789"
    assert build_join_uri("123-456-78901") == \
        "zoommtg://zoom.us/join?confno=12345678901"


@pytest.mark.parametrize("bad", [
    "https://evil.com/j/123456789",          # other host
    "https://zoom.us.evil.com/j/123456789",  # zoom.us only as a prefix label
    "https://evilzoom.us/j/123456789",       # look-alike host
    "http://zoom.us/j/123456789",            # not https
    "javascript:alert(1)",                   # script scheme
    "javascript://zoom.us/j/123456789",      # scheme smuggling
    "zoommtg://zoom.us/join?confno=123456789",  # raw deep links not accepted as input
    "https://zoom.us/j/abc456789",           # letters in URL meeting ID
    "https://zoom.us/meeting/123456789",     # wrong path
    "https://zoom.us/j/123456789?pwd=a b",   # space / bad query charset
    "abc-def",                               # letters in bare ID
    "",                                      # empty
    "   ",                                   # whitespace only
    "12345",                                 # too short
    "123456789012",                          # too long (12 digits)
])
def test_builder_rejects(bad):
    result = build_join_uri(bad)
    assert isinstance(result, dict), bad
    assert result["ok"] is False and result["error"], bad


def test_builder_rejects_non_string():
    result = build_join_uri(None)
    assert isinstance(result, dict) and result["ok"] is False


# ------------------------------------------------------------------ zoom_join

def test_join_bare_id_opens_exact_zoommtg_uri(monkeypatch, tmp_path):
    monkeypatch.setenv("DESKTOP_MCP_HOME", str(tmp_path))
    opened = []
    monkeypatch.setattr(zoom, "_open_uri", lambda uri: opened.append(uri) or True)
    result = zoom_join("123 456 7890")
    assert result["ok"] is True and result["opened"] is True
    assert result["uri"] == "zoommtg://zoom.us/join?confno=1234567890"
    assert opened == ["zoommtg://zoom.us/join?confno=1234567890"]


def test_join_https_url_opens_it_unchanged(monkeypatch, tmp_path):
    monkeypatch.setenv("DESKTOP_MCP_HOME", str(tmp_path))
    opened = []
    monkeypatch.setattr(zoom, "_open_uri", lambda uri: opened.append(uri) or True)
    url = "https://zoom.us/j/123456789?pwd=abc123"
    result = zoom_join(url)
    assert result["ok"] is True and result["uri"] == url
    assert opened == [url]


def test_join_rejected_input_opens_nothing(monkeypatch):
    opened = []
    monkeypatch.setattr(zoom, "_open_uri", lambda uri: opened.append(uri) or True)
    result = zoom_join("https://evil.com/j/123456789")
    assert result["ok"] is False
    assert opened == []


def test_join_opener_failure_is_err_dict_not_raise(monkeypatch, tmp_path):
    monkeypatch.setenv("DESKTOP_MCP_HOME", str(tmp_path))

    def boom(uri):
        raise RuntimeError("no handler")

    monkeypatch.setattr(zoom, "_open_uri", boom)
    result = zoom_join("123456789")
    assert result["ok"] is False and "no handler" in result["error"]


# ---------------------------------------------------------------- delegation

def test_status_delegates_to_common(monkeypatch):
    calls = []
    sentinel = {"ok": True, "app_id": "zoom", "installed": True}

    def fake_status(app_id):
        calls.append(app_id)
        return sentinel

    monkeypatch.setattr(_common, "catalog_status", fake_status)
    assert zoom_status() == sentinel
    assert calls == ["zoom"]


def test_launch_delegates_to_common(monkeypatch):
    calls = []
    sentinel = {"ok": True, "app_id": "zoom", "launched": True}

    def fake_launch(app_id):
        calls.append(app_id)
        return sentinel

    monkeypatch.setattr(_common, "catalog_launch", fake_launch)
    assert zoom_launch() == sentinel
    assert calls == ["zoom"]


# ------------------------------------------------------------------ register

class _FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


def test_register_binds_three_tools(monkeypatch, tmp_path):
    monkeypatch.setenv("DESKTOP_MCP_HOME", str(tmp_path))
    mcp = _FakeMCP()
    names = zoom.register(mcp)
    assert set(names) == {"zoom_status", "zoom_launch", "zoom_join"}
    assert set(mcp.tools) == {"zoom_status", "zoom_launch", "zoom_join"}

    # The registered wrappers really call through.
    monkeypatch.setattr(_common, "catalog_status",
                        lambda app_id: {"ok": True, "app_id": app_id})
    assert mcp.tools["zoom_status"]() == {"ok": True, "app_id": "zoom"}

    opened = []
    monkeypatch.setattr(zoom, "_open_uri", lambda uri: opened.append(uri) or True)
    result = mcp.tools["zoom_join"]("123-456-7890")
    assert result["ok"] is True
    assert opened == ["zoommtg://zoom.us/join?confno=1234567890"]
