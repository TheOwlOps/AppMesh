"""Hermetic tests for the Obsidian adapter.

No real Obsidian, no real URI dispatch: opening a note goes through the
adapter's module-level ``_open_uri`` helper, which is monkeypatched to
record the URI instead of handing it to the OS, and the audit hook is
patched out so nothing is written to disk.
"""
from __future__ import annotations

import urllib.parse

import pytest

from desktop_mcp.adapters.apps import obsidian
from desktop_mcp.adapters.apps.obsidian import build_obsidian_uri


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
    obsidian.register(mcp)
    return mcp.tools


@pytest.fixture()
def opened(monkeypatch):
    """Patch _open_uri to record URIs instead of opening anything."""
    calls = []

    def fake_open(uri):
        calls.append(uri)

    monkeypatch.setattr(obsidian, "_open_uri", fake_open)
    # Keep the test hermetic: record audit calls instead of writing JSONL.
    monkeypatch.setattr(obsidian, "audit", lambda tool, detail: None)
    return calls


# ------------------------------------------------------------ registration

def test_register_binds_required_tools(tools):
    assert {
        "obsidian_status",
        "obsidian_launch",
        "obsidian_open_vault_note",
        "obsidian_focus",
    } <= set(tools)


# ------------------------------------------------------- status / launch / focus

def test_status_delegates_to_catalog(tools, monkeypatch):
    sentinel = {"ok": True, "app_id": "obsidian", "marker": "status"}
    seen = []
    monkeypatch.setattr(
        obsidian, "catalog_status",
        lambda app_id: seen.append(app_id) or sentinel)
    assert tools["obsidian_status"]() is sentinel
    assert seen == ["obsidian"]


def test_launch_delegates_to_catalog(tools, monkeypatch):
    sentinel = {"ok": True, "app_id": "obsidian", "launched": True}
    seen = []
    monkeypatch.setattr(
        obsidian, "catalog_launch",
        lambda app_id: seen.append(app_id) or sentinel)
    assert tools["obsidian_launch"]() is sentinel
    assert seen == ["obsidian"]


def test_focus_delegates_to_focus_window(tools, monkeypatch):
    sentinel = {"ok": True, "focused": True, "window": "Obsidian"}
    seen = []
    monkeypatch.setattr(
        obsidian, "focus_window",
        lambda title: seen.append(title) or sentinel)
    assert tools["obsidian_focus"]() is sentinel
    assert seen == ["Obsidian"]


# ------------------------------------------------------------- URI builder

def test_build_obsidian_uri_simple():
    assert build_obsidian_uri("Notes", "Hello") == (
        "obsidian://open?vault=Notes&file=Hello"
    )


def test_build_obsidian_uri_does_not_force_md_extension():
    assert build_obsidian_uri("Notes", "Daily/2026-10-23") == (
        "obsidian://open?vault=Notes&file=Daily%2F2026-10-23"
    )
    # An explicit extension is kept as-is, not doubled.
    assert build_obsidian_uri("Notes", "Hello.md") == (
        "obsidian://open?vault=Notes&file=Hello.md"
    )


def test_build_obsidian_uri_encoding_exact():
    vault = "My Vault"
    note = "Folder/Ghi chú #1"
    uri = build_obsidian_uri(vault, note)
    # Exact literal expectation from the spec.
    assert uri == (
        "obsidian://open?vault=My%20Vault"
        "&file=Folder%2FGhi%20ch%C3%BA%20%231"
    )
    # And it must match urllib.parse.quote(..., safe="") exactly.
    assert uri == (
        "obsidian://open?vault="
        + urllib.parse.quote(vault, safe="")
        + "&file="
        + urllib.parse.quote(note, safe="")
    )
    assert "vault=My%20Vault" in uri
    assert "file=Folder%2FGhi%20ch%C3%BA%20%231" in uri


# ---------------------------------------------------------- open_vault_note

def test_open_vault_note_opens_exact_built_uri(tools, opened):
    result = tools["obsidian_open_vault_note"]("My Vault", "Folder/Ghi chú #1")
    expected = build_obsidian_uri("My Vault", "Folder/Ghi chú #1")
    assert opened == [expected]
    assert result["ok"] is True
    assert result["opened"] is True
    assert result["uri"] == expected


def test_open_vault_note_simple(tools, opened):
    result = tools["obsidian_open_vault_note"]("Notes", "Hello")
    assert result == {
        "ok": True,
        "opened": True,
        "uri": "obsidian://open?vault=Notes&file=Hello",
    }
    assert opened == ["obsidian://open?vault=Notes&file=Hello"]


def test_open_vault_note_audits_successful_open(tools, monkeypatch):
    audits = []
    monkeypatch.setattr(obsidian, "_open_uri", lambda uri: None)
    monkeypatch.setattr(
        obsidian, "audit",
        lambda tool, detail: audits.append((tool, detail)))
    result = tools["obsidian_open_vault_note"]("Notes", "Hello")
    assert result["ok"] is True
    assert len(audits) == 1
    assert audits[0][0] == "obsidian_open_vault_note"
    assert audits[0][1]["uri"] == result["uri"]


@pytest.mark.parametrize("vault,note", [("", "Hello"), ("Notes", ""),
                                        ("", ""), ("   ", "Hello"),
                                        ("Notes", "   ")])
def test_open_vault_note_empty_vault_or_note_is_err(tools, opened, vault, note):
    result = tools["obsidian_open_vault_note"](vault, note)
    assert result["ok"] is False
    assert result["error"]
    assert opened == []  # opener must not be called for invalid input


def test_open_vault_note_opener_failure_is_graceful(tools, monkeypatch):
    def boom(uri):
        raise OSError("no handler for obsidian://")

    monkeypatch.setattr(obsidian, "_open_uri", boom)
    monkeypatch.setattr(obsidian, "audit", lambda tool, detail: None)
    result = tools["obsidian_open_vault_note"]("Notes", "Hello")
    assert result["ok"] is False
    assert "no handler" in result["error"]
