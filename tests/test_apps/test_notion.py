"""Hermetic tests for the Notion adapter.

No real browser/app is ever opened: ``_open_url`` is monkeypatched with
a recorder, the audit hook is replaced with a recorder, and catalog
status/launch plus window focus are stubbed at the adapter's namespace
so delegation can be asserted exactly.
"""
from __future__ import annotations

import pytest

from desktop_mcp.adapters.apps import notion
from desktop_mcp.adapters.apps.notion import _is_notion_url


class FakeMcp:
    """Minimal FastMCP stand-in: captures tools registered via @mcp.tool()."""

    def __init__(self):
        self.tools = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


@pytest.fixture()
def tools():
    mcp = FakeMcp()
    notion.register(mcp)
    return mcp.tools


@pytest.fixture()
def opener(monkeypatch):
    """Replace notion._open_url and notion.audit with recorders.

    Returns (opened_urls, audit_records).
    """
    opened: list[str] = []
    audits: list[tuple[str, dict]] = []
    monkeypatch.setattr(notion, "_open_url", lambda url: opened.append(url))
    monkeypatch.setattr(
        notion, "audit", lambda tool, detail: audits.append((tool, detail)))
    return opened, audits


# ------------------------------------------------------------------ register

def test_register_binds_all_tools(tools):
    assert {
        "notion_status",
        "notion_launch",
        "notion_open_page",
        "notion_focus",
    } <= set(tools)


# ---------------------------------------------------------------- delegation

def test_status_delegates_to_catalog(tools, monkeypatch):
    sentinel = {"ok": True, "app_id": "notion", "marker": "status"}
    seen = []
    monkeypatch.setattr(
        notion, "catalog_status", lambda app_id: seen.append(app_id) or sentinel)
    assert tools["notion_status"]() is sentinel
    assert seen == ["notion"]


def test_launch_delegates_to_catalog(tools, monkeypatch):
    sentinel = {"ok": True, "app_id": "notion", "launched": True}
    seen = []
    monkeypatch.setattr(
        notion, "catalog_launch", lambda app_id: seen.append(app_id) or sentinel)
    assert tools["notion_launch"]() is sentinel
    assert seen == ["notion"]


def test_focus_delegates_to_focus_window(tools, monkeypatch):
    sentinel = {"ok": True, "focused": True, "window": "Notion"}
    seen = []
    monkeypatch.setattr(
        notion, "focus_window", lambda title: seen.append(title) or sentinel)
    assert tools["notion_focus"]() is sentinel
    assert seen == ["Notion"]


# --------------------------------------------------------------- open_page

@pytest.mark.parametrize("url", [
    "https://www.notion.so/My-Page-123",
    "https://team.notion.site/doc",
    "https://notion.so/My-Page-123",
])
def test_open_page_accepts_valid_notion_urls(tools, opener, url):
    opened, audits = opener
    result = tools["notion_open_page"](url)
    assert result["ok"] is True
    assert result["opened"] is True
    assert result["url"] == url
    assert opened == [url]  # opener receives the exact URL
    assert audits == [("notion_open_page", {"url": url})]


@pytest.mark.parametrize("url", [
    "http://www.notion.so/x",          # wrong scheme
    "https://evilnotion.so/x",         # lookalike host
    "https://notion.so.evil.com/x",    # notion.so is only a subdomain here
    "not a url",                       # unparseable / no scheme or host
    "https://example.com/page",        # unrelated domain
])
def test_open_page_rejects_invalid_urls_without_opening(tools, opener, url):
    opened, audits = opener
    result = tools["notion_open_page"](url)
    assert result["ok"] is False
    assert result["error"]
    assert opened == []   # opener must NOT be called
    assert audits == []   # nothing was opened, so nothing is audited


def test_open_page_opener_exception_returns_err_not_raise(tools, monkeypatch):
    def boom(url):
        raise OSError("no handler registered (test double)")

    monkeypatch.setattr(notion, "_open_url", boom)
    monkeypatch.setattr(notion, "audit", lambda tool, detail: None)
    result = tools["notion_open_page"]("https://www.notion.so/My-Page-123")
    assert result["ok"] is False
    assert "no handler registered" in result["error"]


# --------------------------------------------------------- validation helper

@pytest.mark.parametrize("url", [
    "https://www.notion.so/My-Page-123",
    "https://notion.so/My-Page-123",
    "https://team.notion.site/doc",
    "https://www.notion.site/doc",
    "https://notion.site/doc",
    "https://WWW.NOTION.SO/Case-Insensitive-Host",
])
def test_is_notion_url_accepts(url):
    assert _is_notion_url(url) is True


@pytest.mark.parametrize("url", [
    "http://www.notion.so/x",
    "https://evilnotion.so/x",
    "https://notion.so.evil.com/x",
    "https://sub.notion.so/x",              # only notion.so / www.notion.so
    "https://team.notion.site.evil.com/x",  # notion.site only a subdomain
    "https://www.notion.so@evil.com/x",     # userinfo trick: host is evil.com
    "not a url",
    "https://example.com/page",
    "",
    "   ",
    None,
])
def test_is_notion_url_rejects(url):
    assert _is_notion_url(url) is False
