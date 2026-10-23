"""Hermetic tests for desktop_mcp.system_tools.

No real apps are launched, and neither pywinauto nor Pillow is required:
the tests cover the pure parsing helper, the system_info shape, and the
allowlist rejection paths (which fail before any side effect happens).
"""
from __future__ import annotations

import sys

import pytest

from desktop_mcp import core, system_tools
from desktop_mcp.system_tools import _parse_process_list


# ---------------------------------------------------------------------------
# _parse_process_list — Windows tasklist CSV
# ---------------------------------------------------------------------------

TASKLIST_CSV = (
    '"Image Name","PID","Session Name","Session#","Mem Usage"\r\n'
    '"System Idle Process","0","Services","0","8 K"\r\n'
    '"acad.exe","4321","Console","1","512,340 K"\r\n'
    '"CapCut.exe","9876","Console","1","1,024,000 K"\r\n'
)


def test_parse_tasklist_csv():
    procs = _parse_process_list(TASKLIST_CSV, "csv")
    assert [p["pid"] for p in procs] == [0, 4321, 9876]
    assert procs[1]["name"] == "acad.exe"
    assert procs[2]["name"] == "CapCut.exe"
    assert procs[1]["memory"] == "512,340 K"


def test_parse_tasklist_csv_alias_and_garbage_lines():
    text = '"name.exe","123","Console","1","100 K"\n\nnot,a,valid,row\n'
    procs = _parse_process_list(text, "tasklist")
    assert procs == [{"pid": 123, "name": "name.exe", "memory": "100 K"}]


# ---------------------------------------------------------------------------
# _parse_process_list — POSIX ps
# ---------------------------------------------------------------------------

PS_OUTPUT = """    PID COMMAND
      1 systemd
    842 python3
  12007 desktop-mcp
"""


def test_parse_ps():
    procs = _parse_process_list(PS_OUTPUT, "ps")
    assert procs == [
        {"pid": 1, "name": "systemd"},
        {"pid": 842, "name": "python3"},
        {"pid": 12007, "name": "desktop-mcp"},
    ]


def test_parse_ps_keeps_multiword_command_and_skips_garbage():
    text = "  PID COMM\n  55 /usr/bin/some daemon --flag\nbogus line\n  77 ok\n"
    procs = _parse_process_list(text, "posix")
    assert procs == [
        {"pid": 55, "name": "/usr/bin/some daemon --flag"},
        {"pid": 77, "name": "ok"},
    ]


def test_parse_unknown_format_raises_value_error():
    with pytest.raises(ValueError):
        _parse_process_list("whatever", "yaml")


def test_parse_empty_text_returns_empty_list():
    assert _parse_process_list("", "csv") == []
    assert _parse_process_list("", "ps") == []


# ---------------------------------------------------------------------------
# system_info
# ---------------------------------------------------------------------------

def test_system_info_shape():
    info = system_tools.system_info()
    assert info["ok"] is True
    assert isinstance(info["platform"], str) and info["platform"]
    assert isinstance(info["hostname"], str) and info["hostname"]
    assert isinstance(info["user"], str)
    assert isinstance(info["python_version"], str) and info["python_version"]
    assert info["is_windows"] is core.IS_WINDOWS
    assert info["python_version"].split(".")[0] == str(sys.version_info.major)


# ---------------------------------------------------------------------------
# system_launch_app — allowlist rejection (no real app is launched)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(core.IS_WINDOWS, reason="/etc/passwd is a POSIX path")
def test_launch_app_rejects_existing_path_outside_allowed_roots():
    result = system_tools.system_launch_app("/etc/passwd")
    assert result["ok"] is False
    assert "not allowed" in result["error"].lower()


def test_launch_app_rejects_empty_target():
    result = system_tools.system_launch_app("")
    assert result["ok"] is False


def test_launch_app_missing_path_like_target_errors_without_launching():
    result = system_tools.system_launch_app("./definitely/not/a/real/app.bin")
    assert result["ok"] is False
    assert "not found" in result["error"].lower() or "not allowed" in result["error"].lower()


# ---------------------------------------------------------------------------
# system_screenshot — allowlist rejection happens before Pillow is needed
# ---------------------------------------------------------------------------

@pytest.mark.skipif(core.IS_WINDOWS, reason="/etc/... is a POSIX path")
def test_screenshot_rejects_path_outside_allowed_roots():
    result = system_tools.system_screenshot("/etc/desktop-mcp-test/shot.png")
    assert result["ok"] is False
    assert "not allowed" in result["error"].lower()


def test_screenshot_rejects_empty_path():
    result = system_tools.system_screenshot("")
    assert result["ok"] is False


# ---------------------------------------------------------------------------
# Windows-only tools degrade to a clear error dict off Windows
# ---------------------------------------------------------------------------

@pytest.mark.skipif(core.IS_WINDOWS, reason="Windows-only behaviour differs on Windows")
def test_windows_only_tools_return_error_dict_on_posix():
    for result in (
        system_tools.system_list_windows(),
        system_tools.system_ui_tree("anything"),
        system_tools.system_send_hotkey("ctrl+s"),
        system_tools.system_type_text("hello"),
    ):
        assert result["ok"] is False
        assert "windows-only" in result["error"].lower()
