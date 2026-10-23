"""Hermetic tests for the LibreOffice Writer adapter.

No real LibreOffice installation is required: conversion goes through
an injected fake runner and file opening through a monkeypatched
``_spawn``, so nothing is ever executed or launched.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from desktop_mcp import core
from desktop_mcp.adapters.apps import libreoffice_writer as writer
from desktop_mcp.adapters.apps.libreoffice_writer import (
    build_convert_argv,
    libreoffice_writer_convert,
    libreoffice_writer_open_file,
    register,
)


@pytest.fixture
def allowed(tmp_path, monkeypatch):
    """Restrict the core allowlist to the test's tmp dir."""
    monkeypatch.setattr(core, "ALLOWED_ROOTS", [tmp_path.resolve()])
    return tmp_path


class FakeMCP:
    """Minimal MCP stand-in capturing tools registered via @mcp.tool()."""

    def __init__(self):
        self.tools = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


# ------------------------------------------------------------------ register

def test_register_binds_four_tools():
    mcp = FakeMCP()
    names = register(mcp)
    expected = {
        "libreoffice_writer_status",
        "libreoffice_writer_launch",
        "libreoffice_writer_convert",
        "libreoffice_writer_open_file",
    }
    assert set(mcp.tools) == expected
    assert set(names) == expected


# ------------------------------------------------------------------- builder

def test_build_convert_argv_exact_with_default_format():
    assert build_convert_argv("in.docx", "outdir") == [
        "soffice", "--headless", "--convert-to", "pdf",
        "--outdir", "outdir", "in.docx",
    ]


def test_build_convert_argv_exact_with_explicit_format():
    assert build_convert_argv("in.docx", "outdir", "docx") == [
        "soffice", "--headless", "--convert-to", "docx",
        "--outdir", "outdir", "in.docx",
    ]


# ------------------------------------------------------------------- convert

def test_convert_with_injected_fake_runner(allowed, monkeypatch):
    inp = allowed / "doc.docx"
    inp.write_bytes(b"x")
    outdir = allowed / "out"
    outdir.mkdir()
    calls = []

    def fake_runner(argv, timeout):
        calls.append(list(argv))
        return (0, "out", "")

    monkeypatch.setattr(writer, "runner", fake_runner)
    result = libreoffice_writer_convert(str(inp), str(outdir))

    assert result["ok"] is True
    expected_argv = [
        "soffice", "--headless", "--convert-to", "pdf",
        "--outdir", str(outdir.resolve()), str(inp.resolve()),
    ]
    assert calls == [expected_argv]
    assert result["argv"] == expected_argv
    assert result["stdout"] == "out"


def test_convert_disallowed_input_rejected_runner_never_called(allowed):
    calls = []

    def fake_runner(argv, timeout):
        calls.append(list(argv))
        return (0, "out", "")

    outside = str(Path(allowed).parent / "outside-secret.docx")
    result = libreoffice_writer_convert(
        outside, str(allowed), runner=fake_runner)

    assert result["ok"] is False
    assert calls == []


def test_convert_missing_input_rejected_runner_never_called(allowed):
    calls = []

    def fake_runner(argv, timeout):
        calls.append(list(argv))
        return (0, "out", "")

    result = libreoffice_writer_convert(
        str(allowed / "missing.docx"), str(allowed), runner=fake_runner)

    assert result["ok"] is False
    assert calls == []


# ----------------------------------------------------------------- open_file

def test_open_file_with_monkeypatched_spawn(allowed, monkeypatch):
    doc = allowed / "doc.docx"
    doc.write_bytes(b"x")
    spawned = []

    def fake_spawn(argv):
        spawned.append(list(argv))
        return SimpleNamespace(pid=1234)

    monkeypatch.setattr(writer, "_spawn", fake_spawn)
    monkeypatch.setattr(
        shutil, "which",
        lambda name: "/fake/soffice" if name == "soffice" else None)

    result = libreoffice_writer_open_file(str(doc))

    assert result["ok"] is True
    assert spawned == [["soffice", str(doc.resolve())]]


def test_open_file_disallowed_path_rejected(allowed, monkeypatch):
    spawned = []
    monkeypatch.setattr(writer, "_spawn",
                        lambda argv: spawned.append(list(argv)))
    result = libreoffice_writer_open_file(
        str(Path(allowed).parent / "outside.docx"))
    assert result["ok"] is False
    assert spawned == []
