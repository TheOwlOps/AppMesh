"""Hermetic tests for the Chrome adapter.

No real browser, no network, no real subprocess: detection is faked via
the adapter's ``_exe_path`` helper, spawning via its ``_spawn`` helper,
and the version probe via the injected ``run_cli`` name.
"""
from __future__ import annotations

import pytest

from desktop_mcp.adapters.apps import chrome

FAKE_EXE = r"C:\Program Files\Google\Chrome\Application\chrome.exe"


class FakeMCP:
    """Minimal FastMCP stand-in capturing @mcp.tool() registrations."""

    def __init__(self):
        self.tools = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


@pytest.fixture()
def tools():
    mcp = FakeMCP()
    chrome.register(mcp)
    return mcp.tools


@pytest.fixture()
def spawned(monkeypatch):
    """Patch _spawn to record argv instead of starting a process."""
    calls = []

    class FakeProc:
        pid = 4242

    def fake_spawn(argv):
        calls.append(list(argv))
        return FakeProc()

    monkeypatch.setattr(chrome, "_spawn", fake_spawn)
    return calls


@pytest.fixture()
def installed(monkeypatch):
    monkeypatch.setattr(chrome, "_exe_path", lambda: FAKE_EXE)


@pytest.fixture()
def not_installed(monkeypatch):
    monkeypatch.setattr(chrome, "_exe_path", lambda: None)


# ------------------------------------------------------------ registration

def test_register_binds_required_tools(tools):
    assert {"chrome_status", "chrome_launch", "chrome_open_url",
            "chrome_version"} <= set(tools)


# ------------------------------------------------------- status / launch

def test_status_delegates_to_catalog(monkeypatch, tools):
    seen = []
    monkeypatch.setattr(chrome, "catalog_status",
                        lambda app_id: seen.append(app_id) or {"ok": True})
    assert tools["chrome_status"]() == {"ok": True}
    assert seen == ["chrome"]


def test_launch_delegates_to_catalog(monkeypatch, tools):
    seen = []
    monkeypatch.setattr(chrome, "catalog_launch",
                        lambda app_id: seen.append(app_id) or {"ok": True})
    assert tools["chrome_launch"]() == {"ok": True}
    assert seen == ["chrome"]


# -------------------------------------------------------------- open_url

@pytest.mark.parametrize("url", ["https://example.com",
                                 "https://example.com/a?b=1#frag",
                                 "http://example.com/page"])
def test_open_url_accepts_http_and_https(tools, installed, spawned, url):
    res = tools["chrome_open_url"](url)
    assert res["ok"] is True
    assert res["opened"] is True
    assert res["url"] == url


@pytest.mark.parametrize("bad", ["file:///etc/passwd",
                                 "javascript:alert(1)",
                                 "ftp://example.com/x",
                                 "chrome://settings",
                                 "notaurl",
                                 "",
                                 "   ",
                                 "https://"])
def test_open_url_rejects_non_http_schemes(tools, installed, spawned, bad):
    res = tools["chrome_open_url"](bad)
    assert res["ok"] is False
    assert res["error"]
    assert spawned == []  # nothing was launched for a rejected URL


def test_open_url_spawn_argv_is_exe_then_url(tools, installed, spawned):
    res = tools["chrome_open_url"]("https://example.com")
    assert spawned == [[FAKE_EXE, "https://example.com"]]
    assert res["argv"] == [FAKE_EXE, "https://example.com"]
    assert res["pid"] == 4242
    assert res["path"] == FAKE_EXE


def test_open_url_not_installed(tools, not_installed, spawned):
    res = tools["chrome_open_url"]("https://example.com")
    assert res["ok"] is False
    assert "not installed" in res["error"]
    assert spawned == []


def test_open_url_spawn_failure_is_graceful(tools, installed, monkeypatch):
    def boom(argv):
        raise OSError("cannot start")
    monkeypatch.setattr(chrome, "_spawn", boom)
    res = tools["chrome_open_url"]("https://example.com")
    assert res["ok"] is False
    assert "cannot start" in res["error"]


def test_exe_path_helper_uses_detect_app(monkeypatch):
    monkeypatch.setattr(
        chrome.apps_catalog, "detect_app",
        lambda app_id: {"ok": True, "installed": True, "path": FAKE_EXE})
    assert chrome._exe_path() == FAKE_EXE
    monkeypatch.setattr(
        chrome.apps_catalog, "detect_app",
        lambda app_id: {"ok": True, "installed": False, "path": None})
    assert chrome._exe_path() is None


# --------------------------------------------------------------- version

def test_version_installed(tools, installed, monkeypatch):
    calls = []

    def fake_run_cli(argv, timeout=120, runner=None):
        calls.append(list(argv))
        return {"ok": True, "argv": argv, "returncode": 0,
                "stdout": "Google Chrome 131.0.6778.86 \n", "stderr": ""}

    monkeypatch.setattr(chrome, "run_cli", fake_run_cli)
    res = tools["chrome_version"]()
    assert calls == [[FAKE_EXE, "--version"]]
    assert res["ok"] is True
    assert res["version"] == "Google Chrome 131.0.6778.86"
    assert res["path"] == FAKE_EXE


def test_version_not_installed(tools, not_installed, monkeypatch):
    def forbidden(argv, **kw):  # pragma: no cover - must not be called
        raise AssertionError("run_cli called without an exe")
    monkeypatch.setattr(chrome, "run_cli", forbidden)
    res = tools["chrome_version"]()
    assert res["ok"] is False
    assert "not installed" in res["error"]


def test_version_run_failure_is_graceful(tools, installed, monkeypatch):
    monkeypatch.setattr(
        chrome, "run_cli",
        lambda argv, timeout=120, runner=None: {"ok": False,
                                                "error": "exited 1: boom"})
    res = tools["chrome_version"]()
    assert res["ok"] is False
    assert "boom" in res["error"]
