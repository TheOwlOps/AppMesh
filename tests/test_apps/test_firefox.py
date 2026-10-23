"""Hermetic tests for the Firefox adapter.

No real browser, no network, no real subprocess: detection is faked via
the adapter's ``_exe_path`` helper, spawning via its ``_spawn`` helper,
and the version probe via the injected ``run_cli`` name.
"""
from __future__ import annotations

import pytest

from desktop_mcp.adapters.apps import firefox

FAKE_EXE = r"C:\Program Files\Mozilla Firefox\firefox.exe"


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
    firefox.register(mcp)
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

    monkeypatch.setattr(firefox, "_spawn", fake_spawn)
    return calls


@pytest.fixture()
def installed(monkeypatch):
    monkeypatch.setattr(firefox, "_exe_path", lambda: FAKE_EXE)


@pytest.fixture()
def not_installed(monkeypatch):
    monkeypatch.setattr(firefox, "_exe_path", lambda: None)


# ------------------------------------------------------------ registration

def test_register_binds_required_tools(tools):
    assert {"firefox_status", "firefox_launch", "firefox_open_url",
            "firefox_version"} <= set(tools)


# ------------------------------------------------------- status / launch

def test_status_delegates_to_catalog(monkeypatch, tools):
    seen = []
    monkeypatch.setattr(firefox, "catalog_status",
                        lambda app_id: seen.append(app_id) or {"ok": True})
    assert tools["firefox_status"]() == {"ok": True}
    assert seen == ["firefox"]


def test_launch_delegates_to_catalog(monkeypatch, tools):
    seen = []
    monkeypatch.setattr(firefox, "catalog_launch",
                        lambda app_id: seen.append(app_id) or {"ok": True})
    assert tools["firefox_launch"]() == {"ok": True}
    assert seen == ["firefox"]


# -------------------------------------------------------------- open_url

@pytest.mark.parametrize("url", ["https://example.com",
                                 "https://example.com/a?b=1#frag",
                                 "http://example.com/page"])
def test_open_url_accepts_http_and_https(tools, installed, spawned, url):
    res = tools["firefox_open_url"](url)
    assert res["ok"] is True
    assert res["opened"] is True
    assert res["url"] == url


@pytest.mark.parametrize("bad", ["file:///etc/passwd",
                                 "javascript:alert(1)",
                                 "ftp://example.com/x",
                                 "about:config",
                                 "notaurl",
                                 "",
                                 "   ",
                                 "https://"])
def test_open_url_rejects_non_http_schemes(tools, installed, spawned, bad):
    res = tools["firefox_open_url"](bad)
    assert res["ok"] is False
    assert res["error"]
    assert spawned == []  # nothing was launched for a rejected URL


def test_open_url_spawn_argv_is_exe_then_url(tools, installed, spawned):
    res = tools["firefox_open_url"]("https://example.com")
    assert spawned == [[FAKE_EXE, "https://example.com"]]
    assert res["argv"] == [FAKE_EXE, "https://example.com"]
    assert res["pid"] == 4242
    assert res["path"] == FAKE_EXE


def test_open_url_not_installed(tools, not_installed, spawned):
    res = tools["firefox_open_url"]("https://example.com")
    assert res["ok"] is False
    assert "not installed" in res["error"]
    assert spawned == []


def test_open_url_spawn_failure_is_graceful(tools, installed, monkeypatch):
    def boom(argv):
        raise OSError("cannot start")
    monkeypatch.setattr(firefox, "_spawn", boom)
    res = tools["firefox_open_url"]("https://example.com")
    assert res["ok"] is False
    assert "cannot start" in res["error"]


def test_exe_path_helper_uses_detect_app(monkeypatch):
    monkeypatch.setattr(
        firefox.apps_catalog, "detect_app",
        lambda app_id: {"ok": True, "installed": True, "path": FAKE_EXE})
    assert firefox._exe_path() == FAKE_EXE
    monkeypatch.setattr(
        firefox.apps_catalog, "detect_app",
        lambda app_id: {"ok": True, "installed": False, "path": None})
    assert firefox._exe_path() is None


# --------------------------------------------------------------- version

def test_version_installed(tools, installed, monkeypatch):
    calls = []

    def fake_run_cli(argv, timeout=120, runner=None):
        calls.append(list(argv))
        return {"ok": True, "argv": argv, "returncode": 0,
                "stdout": "Mozilla Firefox 128.0\n", "stderr": ""}

    monkeypatch.setattr(firefox, "run_cli", fake_run_cli)
    res = tools["firefox_version"]()
    assert calls == [[FAKE_EXE, "--version"]]
    assert res["ok"] is True
    assert res["version"] == "Mozilla Firefox 128.0"
    assert res["path"] == FAKE_EXE


def test_version_via_injected_runner(installed, monkeypatch):
    # The module-level implementation also accepts a runner forwarded
    # to the real _common.run_cli (runner signature: (argv, timeout)).
    seen = []

    def runner(argv, timeout):
        seen.append((list(argv), timeout))
        return (0, "Mozilla Firefox 115.0\n", "")

    res = firefox.get_version(runner=runner)
    assert seen == [([FAKE_EXE, "--version"], 120)]
    assert res["ok"] is True
    assert res["version"] == "Mozilla Firefox 115.0"


def test_version_not_installed(tools, not_installed, monkeypatch):
    def forbidden(argv, **kw):  # pragma: no cover - must not be called
        raise AssertionError("run_cli called without an exe")
    monkeypatch.setattr(firefox, "run_cli", forbidden)
    res = tools["firefox_version"]()
    assert res["ok"] is False
    assert "not installed" in res["error"]


def test_version_run_failure_is_graceful(tools, installed, monkeypatch):
    monkeypatch.setattr(
        firefox, "run_cli",
        lambda argv, timeout=120, runner=None: {"ok": False,
                                                "error": "exited 1: boom"})
    res = tools["firefox_version"]()
    assert res["ok"] is False
    assert "boom" in res["error"]
