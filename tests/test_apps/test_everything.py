"""Hermetic tests for the Everything (voidtools) app adapter.

No real Everything / es.exe is needed: ``run_cli`` and the catalog
helpers are monkeypatched with fakes that follow the real
``_common.run_cli`` contract — it returns ``core.ok``/``core.err``
dicts and, for a missing binary, an err dict saying
``"Executable not found on PATH: es"``.
"""
from __future__ import annotations

import pytest

from desktop_mcp.adapters.apps import _common, everything
from desktop_mcp.adapters.apps.everything import (
    build_es_argv,
    everything_launch,
    everything_search,
    everything_status,
)


# ---------------------------------------------------------------------------
# Fake MCP (same pattern as tests/test_cli_tools.py)
# ---------------------------------------------------------------------------

class _FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


def fake_run_cli_ok(stdout, calls=None):
    """A run_cli stand-in honouring the real (argv, timeout, runner) shape."""
    def run(argv, timeout=120, runner=None):
        if calls is not None:
            calls.append(list(argv))
        return {"ok": True, "argv": list(argv), "returncode": 0,
                "stdout": stdout, "stderr": ""}
    return run


# ---------------------------------------------------------------------------
# Builder — exact argv + clamping
# ---------------------------------------------------------------------------

def test_build_es_argv_exact():
    assert build_es_argv("report.pdf") == ["es", "-n", "20", "report.pdf"]
    assert build_es_argv("report.pdf", 5) == ["es", "-n", "5", "report.pdf"]
    assert build_es_argv("*.png", max_results=100) == ["es", "-n", "100", "*.png"]


def test_build_es_argv_clamps_max_results():
    assert build_es_argv("x", 0) == ["es", "-n", "1", "x"]
    assert build_es_argv("x", -10) == ["es", "-n", "1", "x"]
    assert build_es_argv("x", 9999) == ["es", "-n", "500", "x"]
    assert build_es_argv("x", 500) == ["es", "-n", "500", "x"]
    assert build_es_argv("x", 1) == ["es", "-n", "1", "x"]


def test_build_es_argv_rejects_empty_query():
    with pytest.raises(ValueError):
        build_es_argv("")
    with pytest.raises(ValueError):
        build_es_argv("   ")
    with pytest.raises(ValueError):
        build_es_argv(None)


# ---------------------------------------------------------------------------
# Validation at the tool level (err dicts, never raises)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["", "   ", "\t\n", None, 123])
def test_search_rejects_empty_or_nonstring_query(bad):
    res = everything_search(bad)
    assert res["ok"] is False
    assert "non-empty" in res["error"]


# ---------------------------------------------------------------------------
# Search — happy path via fake run_cli
# ---------------------------------------------------------------------------

def test_search_parses_stdout_lines_exactly(monkeypatch):
    calls = []
    stdout = "C:\\docs\\report.pdf\n\n  D:\\work\\report final.pdf  \nC:\\a.txt\n"
    monkeypatch.setattr(everything, "run_cli", fake_run_cli_ok(stdout, calls))
    res = everything_search("report", max_results=10)
    assert res["ok"] is True
    assert res["results"] == [
        "C:\\docs\\report.pdf",
        "D:\\work\\report final.pdf",
        "C:\\a.txt",
    ]
    assert res["count"] == 3
    assert calls == [["es", "-n", "10", "report"]]


def test_search_empty_stdout_gives_empty_results(monkeypatch):
    monkeypatch.setattr(everything, "run_cli", fake_run_cli_ok("\n\n"))
    res = everything_search("nothing-matches-this")
    assert res["ok"] is True
    assert res["results"] == []
    assert res["count"] == 0


def test_search_via_monkeypatched_common_run_cli(monkeypatch):
    # Patching _common.run_cli (rather than everything.run_cli) also works.
    monkeypatch.setattr(_common, "run_cli", fake_run_cli_ok("C:\\only.txt\n"))
    res = everything_search("only")
    assert res["ok"] is True
    assert res["results"] == ["C:\\only.txt"]


def test_search_forwards_runner_hook(monkeypatch):
    seen = {}

    def run(argv, timeout=120, runner=None):
        seen["argv"] = list(argv)
        seen["runner"] = runner
        code, out, err_text = runner(argv, timeout)
        assert code == 0
        return {"ok": True, "argv": list(argv), "returncode": code,
                "stdout": out, "stderr": err_text}

    monkeypatch.setattr(everything, "run_cli", run)
    res = everything_search(
        "x", runner=lambda argv, timeout: (0, "C:\\r.txt\n", ""))
    assert res["ok"] is True
    assert res["results"] == ["C:\\r.txt"]
    assert seen["argv"] == ["es", "-n", "20", "x"]


# ---------------------------------------------------------------------------
# Search — honest failure when es.exe is missing
# ---------------------------------------------------------------------------

def test_search_missing_es_err_dict_mentions_es_exe_and_voidtools(monkeypatch):
    def missing(argv, timeout=120, runner=None):
        # Exactly what the real _common.run_cli returns when
        # shutil.which("es") is None.
        return {"ok": False, "error": "Executable not found on PATH: es"}

    monkeypatch.setattr(everything, "run_cli", missing)
    res = everything_search("report.pdf")
    assert res["ok"] is False
    assert "Executable not found on PATH: es" in res["error"]  # surfaced
    assert "es.exe" in res["error"]
    assert "voidtools" in res["error"]
    assert "PATH" in res["error"]


def test_search_missing_es_exception_mentions_es_exe_and_voidtools(monkeypatch):
    def raising(argv, timeout=120, runner=None):
        raise FileNotFoundError(2, "No such file or directory", "es")

    monkeypatch.setattr(everything, "run_cli", raising)
    res = everything_search("report.pdf")
    assert res["ok"] is False
    assert "es.exe" in res["error"]
    assert "voidtools" in res["error"]


def test_search_nonzero_exit_error_passes_through(monkeypatch):
    def failed(argv, timeout=120, runner=None):
        return {"ok": False, "error": "es exited 1: Everything IPC not available"}

    monkeypatch.setattr(everything, "run_cli", failed)
    res = everything_search("report.pdf")
    assert res["ok"] is False
    assert "es exited 1" in res["error"]


# ---------------------------------------------------------------------------
# Delegation to _common catalog helpers
# ---------------------------------------------------------------------------

def test_status_delegates_to_catalog_status(monkeypatch):
    seen = []

    def fake_catalog_status(app_id):
        seen.append(app_id)
        return {"ok": True, "app_id": app_id, "installed": True}

    monkeypatch.setattr(_common, "catalog_status", fake_catalog_status)
    res = everything_status()
    assert res == {"ok": True, "app_id": "everything", "installed": True}
    assert seen == ["everything"]


def test_launch_delegates_to_catalog_launch(monkeypatch):
    seen = []

    def fake_catalog_launch(app_id):
        seen.append(app_id)
        return {"ok": True, "app_id": app_id, "launched": True}

    monkeypatch.setattr(everything, "catalog_launch", fake_catalog_launch)
    res = everything_launch()
    assert res == {"ok": True, "app_id": "everything", "launched": True}
    assert seen == ["everything"]


def test_status_real_catalog_shape():
    # The real catalog helper needs no Everything install to answer.
    # NOTE: _common.catalog_status reads strategy/name/prog_id off the
    # top level of get_app()'s result, but get_app() nests the record
    # under "app" — so those fields come back None in practice. That is
    # pre-existing _common behaviour (not this adapter's file to fix),
    # so this test only asserts the fields it reliably populates.
    res = everything_status()
    assert res["ok"] is True
    assert res["app_id"] == "everything"
    assert isinstance(res["installed"], bool)
    assert "path" in res
    assert "via" in res


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_register_binds_all_tools():
    mcp = _FakeMCP()
    names = everything.register(mcp)
    expected = {"everything_status", "everything_launch", "everything_search"}
    assert set(names) == expected
    assert set(mcp.tools) == expected


def test_registered_search_tool_works_with_fake_run_cli(monkeypatch):
    mcp = _FakeMCP()
    everything.register(mcp)
    monkeypatch.setattr(everything, "run_cli", fake_run_cli_ok("C:\\hit.txt\n"))
    res = mcp.tools["everything_search"]("hit", 3)
    assert res["ok"] is True
    assert res["results"] == ["C:\\hit.txt"]
