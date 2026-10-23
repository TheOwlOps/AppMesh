"""Hermetic tests for the 100-app catalog (apps_catalog.py).

No real apps are probed or launched: detection goes through fake
``which_fn`` callables and launches through a monkeypatched ``_spawn``.
"""
from __future__ import annotations

import pytest

from desktop_mcp import apps_catalog as cat
from desktop_mcp.apps_catalog import (
    APPS,
    CATEGORIES,
    STRATEGIES,
    _common_paths,
    _probe,
    detect_app,
    detect_apps,
    get_app,
    launch_app_from_catalog,
    list_apps,
)

EXPECTED_CATEGORY_COUNTS = {
    "CAD & 3D": 14,
    "Video & Audio": 16,
    "Design & Photo": 12,
    "Office & Productivity": 14,
    "Dev Tools": 18,
    "Browsers": 6,
    "Communication": 10,
    "Utilities & Media": 10,
}


def fake_which(found: dict[str, str]):
    """Build a which_fn that only 'finds' the exe names in *found*."""
    def _which(name):
        return found.get(name)
    return _which


# ------------------------------------------------------------------ catalog

def test_exactly_100_apps():
    assert len(APPS) == 100


def test_unique_ids():
    ids = [a["id"] for a in APPS]
    assert len(ids) == len(set(ids))
    assert all(i == i.lower() and " " not in i for i in ids)


def test_every_entry_has_required_fields_and_valid_strategy():
    for app in APPS:
        assert set(app) == {"id", "name", "category", "strategy",
                            "exe_names", "prog_id", "notes"}, app["id"]
        assert app["name"] and app["notes"], app["id"]
        assert app["category"] in EXPECTED_CATEGORY_COUNTS, app["id"]
        assert app["strategy"] in STRATEGIES, app["id"]
        assert isinstance(app["exe_names"], list), app["id"]
        assert all(isinstance(e, str) and e for e in app["exe_names"]), app["id"]
        assert app["prog_id"] is None or isinstance(app["prog_id"], str), app["id"]
        if app["strategy"] == "com":
            assert app["prog_id"], f"COM app without ProgID: {app['id']}"


def test_category_counts_match_plan_and_sum_to_100():
    assert sum(EXPECTED_CATEGORY_COUNTS.values()) == 100
    for category, expected in EXPECTED_CATEGORY_COUNTS.items():
        result = list_apps(category)
        assert result["ok"] and result["count"] == expected, category
    total = sum(list_apps(c)["count"] for c in CATEGORIES)
    assert total == 100
    assert list_apps()["count"] == 100
    assert list_apps("No Such Category")["ok"] is False


def test_autocad_and_capcut_present_with_right_strategy():
    acad = get_app("autocad")
    assert acad["ok"] and acad["app"]["strategy"] == "com"
    assert acad["app"]["prog_id"] == "AutoCAD.Application"
    cap = get_app("capcut")
    assert cap["ok"] and cap["app"]["strategy"] == "file"


# ------------------------------------------------------------------- queries

def test_get_app_known_and_unknown():
    result = get_app("excel")
    assert result["ok"] and result["app"]["name"] == "Microsoft Excel"
    assert get_app("EXCEL")["ok"]  # ids are case-insensitive
    missing = get_app("definitely-not-an-app")
    assert missing["ok"] is False and "Unknown app id" in missing["error"]


def test_list_apps_returns_copies():
    result = list_apps()
    assert result["ok"] and result["count"] == 100
    result["apps"][0]["name"] = "MUTATED"
    assert APPS[0]["name"] != "MUTATED"


# ----------------------------------------------------------------- detection

def test_probe_returns_first_found_or_none():
    which = fake_which({"b.exe": "/usr/bin/b"})
    assert _probe(["a.exe", "b.exe", "c.exe"], which) == "/usr/bin/b"
    assert _probe(["a.exe"], which) is None
    assert _probe([], which) is None


def test_detect_app_installed_via_fake_which():
    which = fake_which({"acad.exe": "C:/Program Files/Autodesk/acad.exe"})
    result = detect_app("autocad", which_fn=which)
    assert result["ok"] and result["installed"] is True
    assert result["path"].endswith("acad.exe")
    assert result["via"] == "path" and result["strategy"] == "com"


def test_detect_app_not_installed_and_unknown():
    result = detect_app("autocad", which_fn=fake_which({}))
    assert result["ok"] and result["installed"] is False and result["path"] is None
    assert detect_app("nope", which_fn=fake_which({}))["ok"] is False


def test_detect_apps_summary_counts():
    which = fake_which({"acad.exe": "/x/acad.exe", "git.exe": "/usr/bin/git"})
    result = detect_apps(which_fn=which)
    assert result["ok"] and result["count"] == 100
    assert result["installed_count"] == 2
    by_id = {a["id"]: a for a in result["apps"]}
    assert by_id["autocad"]["installed"] and by_id["git"]["installed"]
    assert not by_id["capcut"]["installed"]
    cad_only = detect_apps("CAD & 3D", which_fn=which)
    assert cad_only["count"] == 14 and cad_only["installed_count"] == 1


def test_common_paths_are_windows_style_guesses(monkeypatch):
    monkeypatch.setenv("ProgramFiles", "C:/Program Files")
    monkeypatch.setenv("ProgramFiles(x86)", "C:/Program Files (x86)")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    paths = _common_paths(get_app("autocad")["app"])
    assert paths, "expected candidate paths"
    assert all(str(p).endswith("acad.exe") for p in paths)
    assert any("Program Files" in str(p) for p in paths)


# -------------------------------------------------------------------- launch

class _FakeProc:
    pid = 4242


def test_launch_uses_spawn_and_audits(monkeypatch, tmp_path):
    monkeypatch.setenv("DESKTOP_MCP_HOME", str(tmp_path))
    spawned = []
    monkeypatch.setattr(cat, "_spawn", lambda path: spawned.append(path) or _FakeProc())
    which = fake_which({"acad.exe": "C:/Program Files/Autodesk/acad.exe"})
    result = launch_app_from_catalog("autocad", which_fn=which)
    assert result["ok"] and result["launched"] is True and result["pid"] == 4242
    assert spawned == ["C:/Program Files/Autodesk/acad.exe"]
    audit_file = tmp_path / "audit.jsonl"
    assert audit_file.exists() and "apps_launch" in audit_file.read_text(encoding="utf-8")


def test_launch_not_installed_and_unknown(monkeypatch):
    monkeypatch.setattr(cat, "_spawn",
                        lambda path: pytest.fail("must not spawn for missing apps"))
    result = launch_app_from_catalog("autocad", which_fn=fake_which({}))
    assert result["ok"] is False and "not installed" in result["error"]
    assert launch_app_from_catalog("nope", which_fn=fake_which({}))["ok"] is False


# ------------------------------------------------------------------ register

class _FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


def test_register_binds_five_apps_tools():
    mcp = _FakeMCP()
    cat.register(mcp)
    assert set(mcp.tools) == {"apps_list", "apps_get", "apps_detect",
                              "apps_detect_one", "apps_launch"}
    assert mcp.tools["apps_list"]()["count"] == 100
    assert mcp.tools["apps_get"]("capcut")["app"]["strategy"] == "file"
