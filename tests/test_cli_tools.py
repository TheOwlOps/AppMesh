"""Hermetic tests for the CLI tools adapter — fake runners only.

No real ffmpeg / ImageMagick / 7z / blender is required: every happy path
injects a runner returning a canned (returncode, stdout, stderr) tuple,
and builders are asserted argv-for-argv.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from desktop_mcp import core
from desktop_mcp.adapters import cli_tools
from desktop_mcp.adapters.cli_tools import (
    build_7zip_extract,
    build_7zip_list,
    build_blender_render,
    build_ffmpeg_convert,
    build_ffmpeg_thumbnail,
    build_ffprobe,
    build_git_log,
    build_git_status,
    build_magick_identify,
    build_magick_resize,
    cli_7zip_list,
    cli_blender_render,
    cli_ffmpeg_convert,
    cli_ffmpeg_probe,
    cli_git_log,
    cli_git_status,
    cli_magick_identify,
)


@pytest.fixture
def allowed(tmp_path, monkeypatch):
    """Restrict the core allowlist to the test's tmp dir."""
    monkeypatch.setattr(core, "ALLOWED_ROOTS", [tmp_path.resolve()])
    return tmp_path


def _mkfile(base: Path, name: str, data: bytes = b"x") -> Path:
    p = base / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def fake_runner(result, calls=None):
    def run(argv):
        if calls is not None:
            calls.append(list(argv))
        return result
    return run


# ---------------------------------------------------------------------------
# Builders — exact argv
# ---------------------------------------------------------------------------

def test_build_ffprobe_argv():
    assert build_ffprobe("a.mp4") == [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", "a.mp4",
    ]


def test_build_ffmpeg_convert_argv():
    assert build_ffmpeg_convert("i.mp4", "o.mkv") == [
        "ffmpeg", "-y", "-i", "i.mp4", "o.mkv",
    ]
    assert build_ffmpeg_convert("i.mp4", "o.mkv", ["-c:v", "libx264"]) == [
        "ffmpeg", "-y", "-i", "i.mp4", "-c:v", "libx264", "o.mkv",
    ]


def test_build_ffmpeg_thumbnail_argv():
    assert build_ffmpeg_thumbnail("i.mp4", "o.jpg") == [
        "ffmpeg", "-ss", "1.0", "-i", "i.mp4", "-frames:v", "1", "o.jpg",
    ]
    assert build_ffmpeg_thumbnail("i.mp4", "o.jpg", at_seconds=2.5) == [
        "ffmpeg", "-ss", "2.5", "-i", "i.mp4", "-frames:v", "1", "o.jpg",
    ]


def test_build_magick_identify_argv():
    assert build_magick_identify("a.png") == [
        "magick", "identify", "-format", "%m %wx%h %b", "a.png",
    ]


def test_build_magick_resize_argv():
    assert build_magick_resize("a.png", "b.png", 800) == [
        "magick", "a.png", "-resize", "800x", "b.png",
    ]


def test_build_7zip_argv():
    assert build_7zip_list("a.zip") == ["7z", "l", "-slt", "a.zip"]
    assert build_7zip_extract("a.zip", "outdir") == [
        "7z", "x", "-y", "-ooutdir", "a.zip",
    ]


def test_build_git_argv():
    assert build_git_status("repo") == [
        "git", "-C", "repo", "status", "--porcelain=v1", "--branch",
    ]
    assert build_git_log("repo") == ["git", "-C", "repo", "log", "-10", "--oneline"]
    assert build_git_log("repo", n=3) == ["git", "-C", "repo", "log", "-3", "--oneline"]


def test_build_blender_render_argv():
    assert build_blender_render("s.blend", "render_") == [
        "blender", "-b", "s.blend", "-o", "render_", "-f", "1",
    ]
    assert build_blender_render("s.blend", "render_", frame=24)[-1] == "24"


# ---------------------------------------------------------------------------
# Happy paths via injected runners
# ---------------------------------------------------------------------------

def test_probe_parses_json(allowed):
    media = _mkfile(allowed, "clip.mp4")
    payload = {"streams": [{"codec_name": "h264"}], "format": {"duration": "3.5"}}
    calls = []
    res = cli_ffmpeg_probe(str(media),
                           runner=fake_runner((0, json.dumps(payload), ""), calls))
    assert res["ok"] is True
    assert res["probe"] == payload
    assert calls == [build_ffprobe(str(media.resolve()))]


def test_git_log_parses_lines(allowed):
    repo = allowed / "repo"
    repo.mkdir()
    out = "abc1234 first commit\ndef5678 fix: second thing\n\n"
    res = cli_git_log(str(repo), runner=fake_runner((0, out, "")))
    assert res["ok"] is True
    assert res["commits"] == [
        {"hash": "abc1234", "subject": "first commit"},
        {"hash": "def5678", "subject": "fix: second thing"},
    ]
    assert res["count"] == 2


def test_git_status_passthrough(allowed):
    repo = allowed / "repo"
    repo.mkdir()
    res = cli_git_status(str(repo), runner=fake_runner((0, "## main\n M a.py\n", "")))
    assert res["ok"] is True
    assert res["output"] == "## main\n M a.py\n"
    assert res["truncated"] is False


def test_7zip_list_passthrough_and_truncation(allowed):
    arc = _mkfile(allowed, "a.zip")
    res = cli_7zip_list(str(arc), runner=fake_runner((0, "Path = a.zip\n", "")))
    assert res["ok"] is True
    assert res["output"] == "Path = a.zip\n"
    assert res["truncated"] is False

    big = "y" * 9000
    res = cli_7zip_list(str(arc), runner=fake_runner((0, big, "")))
    assert res["ok"] is True
    assert len(res["output"]) == cli_tools.MAX_OUTPUT_CHARS
    assert res["truncated"] is True


def test_magick_identify_happy(allowed):
    img = _mkfile(allowed, "a.png")
    res = cli_magick_identify(str(img),
                              runner=fake_runner((0, "PNG 800x600 12345B\n", "")))
    assert res["ok"] is True
    assert res["info"] == "PNG 800x600 12345B"


def test_convert_argv_and_ok(allowed):
    inp = _mkfile(allowed, "in.mp4")
    out = allowed / "sub" / "out.mkv"
    calls = []
    res = cli_ffmpeg_convert(str(inp), str(out), ["-c:v", "libx264"],
                             runner=fake_runner((0, "", ""), calls))
    assert res["ok"] is True
    assert res["output"] == str(out.resolve())
    assert calls == [build_ffmpeg_convert(str(inp.resolve()), str(out.resolve()),
                                          ["-c:v", "libx264"])]


def test_blender_render_argv(allowed):
    blend = _mkfile(allowed, "scene.blend")
    prefix = allowed / "renders" / "frame_"
    calls = []
    res = cli_blender_render(str(blend), str(prefix), frame=7,
                             runner=fake_runner((0, "Saved: frame_0007.png", ""), calls))
    assert res["ok"] is True
    assert res["frame"] == 7
    assert calls == [build_blender_render(str(blend.resolve()),
                                          str(prefix.resolve()), 7)]


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------

def test_missing_binary_error(allowed, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    media = _mkfile(allowed, "clip.mp4")
    res = cli_ffmpeg_probe(str(media))  # no runner -> real which() check
    assert res["ok"] is False
    assert "ffprobe" in res["error"]
    assert "not found" in res["error"]

    repo = allowed / "repo"
    repo.mkdir()
    res = cli_git_status(str(repo))
    assert res["ok"] is False
    assert "'git'" in res["error"]


def test_disallowed_path_rejected_before_running(allowed):
    calls = []
    res = cli_ffmpeg_probe("/etc/passwd.mp4",
                           runner=fake_runner((0, "{}", ""), calls))
    assert res["ok"] is False
    assert "not allowed" in res["error"].lower()
    assert calls == []  # runner never invoked


def test_missing_input_file(allowed):
    res = cli_ffmpeg_probe(str(allowed / "nope.mp4"),
                           runner=fake_runner((0, "{}", "")))
    assert res["ok"] is False
    assert "does not exist" in res["error"]


def test_nonzero_exit_reports_stderr(allowed):
    media = _mkfile(allowed, "clip.mp4")
    res = cli_ffmpeg_probe(str(media),
                           runner=fake_runner((1, "", "boom happened")))
    assert res["ok"] is False
    assert "code 1" in res["error"]
    assert "boom happened" in res["error"]


def test_probe_invalid_json(allowed):
    media = _mkfile(allowed, "clip.mp4")
    res = cli_ffmpeg_probe(str(media), runner=fake_runner((0, "not json", "")))
    assert res["ok"] is False
    assert "invalid JSON" in res["error"]


# ---------------------------------------------------------------------------
# Auditing + registration
# ---------------------------------------------------------------------------

def test_convert_is_audited(allowed, monkeypatch, tmp_path):
    home = tmp_path / "mcp-home"
    monkeypatch.setenv("DESKTOP_MCP_HOME", str(home))
    inp = _mkfile(allowed, "in.mp4")
    out = allowed / "out.mkv"
    res = cli_ffmpeg_convert(str(inp), str(out),
                             runner=fake_runner((0, "", "")))
    assert res["ok"] is True
    log = (home / "audit.jsonl").read_text(encoding="utf-8")
    assert "cli_ffmpeg_convert" in log
    assert "ffmpeg" in log


class _FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


def test_register_binds_all_tools():
    mcp = _FakeMCP()
    names = cli_tools.register(mcp)
    expected = {
        "cli_ffmpeg_probe", "cli_ffmpeg_convert", "cli_ffmpeg_thumbnail",
        "cli_magick_identify", "cli_magick_resize", "cli_7zip_list",
        "cli_7zip_extract", "cli_git_status", "cli_git_log",
        "cli_blender_render",
    }
    assert set(names) == expected
    assert set(mcp.tools) == expected
