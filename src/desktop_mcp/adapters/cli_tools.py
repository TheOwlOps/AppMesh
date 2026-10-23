"""CLI tools adapter — wrap well-known command-line programs as MCP tools.

Many desktop workflows do not need a GUI at all: ffmpeg/ffprobe (media),
ImageMagick (images), 7-Zip (archives), Git (repos) and Blender's
background mode (renders) are all scriptable. This adapter exposes them
through one consistent pattern:

- every tool is a PURE argv builder (``build_*``) plus a call through the
  shared runner :func:`_run`;
- :func:`_run` accepts an injectable ``runner`` (used by tests) returning
  ``(returncode, stdout, stderr)``; without one it checks
  ``shutil.which`` first and reports a clear "install X" error instead of
  a raw FileNotFoundError;
- input paths must pass ``core.check_path_allowed`` AND exist; output
  paths must pass the allowlist; mutating runs are audit-logged.

All public functions return ``core.ok`` / ``core.err`` dicts and never
raise across the MCP boundary.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Optional

from ..core import audit, check_path_allowed, err, ok

# A runner takes argv and returns (returncode, stdout, stderr).
Runner = Callable[[list], tuple]

MAX_OUTPUT_CHARS = 8000

INSTALL_HINTS = {
    "ffmpeg": "Install ffmpeg (ffprobe ships with it): Windows "
              "`winget install Gyan.FFmpeg`, macOS `brew install ffmpeg`, "
              "Linux `sudo apt install ffmpeg`.",
    "ffprobe": "ffprobe ships with ffmpeg. Install ffmpeg: Windows "
               "`winget install Gyan.FFmpeg`, macOS `brew install ffmpeg`, "
               "Linux `sudo apt install ffmpeg`.",
    "magick": "Install ImageMagick: Windows "
              "`winget install ImageMagick.ImageMagick`, macOS "
              "`brew install imagemagick`, Linux `sudo apt install imagemagick`.",
    "7z": "Install 7-Zip: Windows `winget install 7zip.7zip`, macOS "
          "`brew install sevenzip`, Linux `sudo apt install p7zip-full`.",
    "git": "Install Git: Windows `winget install Git.Git`, macOS "
           "`brew install git`, Linux `sudo apt install git`.",
    "blender": "Install Blender: Windows `winget install BlenderFoundation.Blender`, "
               "macOS `brew install --cask blender`, Linux `sudo apt install blender`.",
}


# --------------------------------------------------------------------------
# Shared runner + path helpers
# --------------------------------------------------------------------------

def _run(argv: list, timeout: int = 120, runner: Optional[Runner] = None) -> dict:
    """Run *argv* and return a result dict.

    Success: ``{"ok": True, "returncode": int, "stdout": str, "stderr": str}``
    (a non-zero returncode is still "ok" here — callers decide). Failure to
    even start the program (missing binary, timeout, OS error) returns an
    ``err`` dict.
    """
    if runner is not None:
        try:
            rc, out, err_text = runner(argv)
        except Exception as exc:  # a broken runner must not crash the tool
            return err(f"Runner for '{argv[0]}' failed: {exc}")
        return {"ok": True, "returncode": int(rc),
                "stdout": out or "", "stderr": err_text or ""}

    exe = argv[0]
    if shutil.which(exe) is None:
        hint = INSTALL_HINTS.get(exe, "")
        return err(f"Required program '{exe}' was not found on PATH. {hint}".strip())
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return err(f"'{exe}' timed out after {timeout}s: {' '.join(str(a) for a in argv)}")
    except OSError as exc:
        return err(exc)
    return {"ok": True, "returncode": proc.returncode,
            "stdout": proc.stdout, "stderr": proc.stderr}


def _check_failed(res: dict, label: str) -> Optional[dict]:
    """Return an err dict if the run exited non-zero, else None."""
    if res["returncode"] != 0:
        tail = (res["stderr"] or "").strip()[-500:]
        msg = f"'{label}' exited with code {res['returncode']}"
        if tail:
            msg += f": {tail}"
        return err(msg)
    return None


def _input_path(path: str | Path) -> Path:
    """Validate an input path: allowlisted and existing. Raises ValueError."""
    p = check_path_allowed(path)
    if not p.exists():
        raise ValueError(f"Input path does not exist: {p}")
    return p


def _output_path(path: str | Path) -> Path:
    """Validate an output path: allowlisted. Raises ValueError."""
    return check_path_allowed(path)


# --------------------------------------------------------------------------
# ffmpeg / ffprobe
# --------------------------------------------------------------------------

def build_ffprobe(path: str) -> list:
    return ["ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_format", "-show_streams", str(path)]


def cli_ffmpeg_probe(path: str, runner: Optional[Runner] = None) -> dict:
    """Probe a media file with ffprobe and return the parsed JSON info."""
    try:
        p = _input_path(path)
    except ValueError as exc:
        return err(exc)
    res = _run(build_ffprobe(str(p)), runner=runner)
    if not res["ok"]:
        return res
    failed = _check_failed(res, "ffprobe")
    if failed:
        return failed
    try:
        probe = json.loads(res["stdout"] or "{}")
    except json.JSONDecodeError as exc:
        return err(f"ffprobe returned invalid JSON: {exc}")
    return ok(path=str(p), probe=probe)


def build_ffmpeg_convert(inp: str, out: str, extra_args: list = []) -> list:
    return ["ffmpeg", "-y", "-i", str(inp), *[str(a) for a in extra_args], str(out)]


def cli_ffmpeg_convert(input_path: str, output_path: str, extra_args: Optional[list] = None,
                       runner: Optional[Runner] = None) -> dict:
    """Convert/transcode a media file with ffmpeg (overwrites the output)."""
    try:
        inp = _input_path(input_path)
        out = _output_path(output_path)
    except ValueError as exc:
        return err(exc)
    argv = build_ffmpeg_convert(str(inp), str(out), extra_args or [])
    out.parent.mkdir(parents=True, exist_ok=True)
    res = _run(argv, timeout=600, runner=runner)
    if not res["ok"]:
        return res
    failed = _check_failed(res, "ffmpeg")
    if failed:
        return failed
    audit("cli_ffmpeg_convert", {"argv": argv})
    return ok(input=str(inp), output=str(out), returncode=res["returncode"])


def build_ffmpeg_thumbnail(inp: str, out: str, at_seconds: float = 1.0) -> list:
    return ["ffmpeg", "-ss", str(at_seconds), "-i", str(inp),
            "-frames:v", "1", str(out)]


def cli_ffmpeg_thumbnail(input_path: str, output_path: str, at_seconds: float = 1.0,
                         runner: Optional[Runner] = None) -> dict:
    """Extract a single frame from a video as an image with ffmpeg."""
    try:
        inp = _input_path(input_path)
        out = _output_path(output_path)
    except ValueError as exc:
        return err(exc)
    argv = build_ffmpeg_thumbnail(str(inp), str(out), at_seconds)
    out.parent.mkdir(parents=True, exist_ok=True)
    res = _run(argv, runner=runner)
    if not res["ok"]:
        return res
    failed = _check_failed(res, "ffmpeg")
    if failed:
        return failed
    audit("cli_ffmpeg_thumbnail", {"argv": argv})
    return ok(input=str(inp), output=str(out), at_seconds=at_seconds,
              returncode=res["returncode"])


# --------------------------------------------------------------------------
# ImageMagick
# --------------------------------------------------------------------------

def build_magick_identify(path: str) -> list:
    return ["magick", "identify", "-format", "%m %wx%h %b", str(path)]


def cli_magick_identify(path: str, runner: Optional[Runner] = None) -> dict:
    """Identify an image with ImageMagick: format, dimensions, size."""
    try:
        p = _input_path(path)
    except ValueError as exc:
        return err(exc)
    res = _run(build_magick_identify(str(p)), runner=runner)
    if not res["ok"]:
        return res
    failed = _check_failed(res, "magick identify")
    if failed:
        return failed
    return ok(path=str(p), info=res["stdout"].strip())


def build_magick_resize(inp: str, out: str, width: int) -> list:
    return ["magick", str(inp), "-resize", f"{int(width)}x", str(out)]


def cli_magick_resize(input_path: str, output_path: str, width: int,
                      runner: Optional[Runner] = None) -> dict:
    """Resize an image to the given width (aspect kept) with ImageMagick."""
    try:
        inp = _input_path(input_path)
        out = _output_path(output_path)
    except ValueError as exc:
        return err(exc)
    argv = build_magick_resize(str(inp), str(out), width)
    out.parent.mkdir(parents=True, exist_ok=True)
    res = _run(argv, runner=runner)
    if not res["ok"]:
        return res
    failed = _check_failed(res, "magick")
    if failed:
        return failed
    audit("cli_magick_resize", {"argv": argv})
    return ok(input=str(inp), output=str(out), width=int(width),
              returncode=res["returncode"])


# --------------------------------------------------------------------------
# 7-Zip
# --------------------------------------------------------------------------

def build_7zip_list(archive: str) -> list:
    return ["7z", "l", "-slt", str(archive)]


def cli_7zip_list(archive: str, runner: Optional[Runner] = None) -> dict:
    """List the contents of an archive with 7-Zip (technical listing)."""
    try:
        p = _input_path(archive)
    except ValueError as exc:
        return err(exc)
    res = _run(build_7zip_list(str(p)), runner=runner)
    if not res["ok"]:
        return res
    failed = _check_failed(res, "7z")
    if failed:
        return failed
    out = res["stdout"]
    return ok(archive=str(p), output=out[:MAX_OUTPUT_CHARS],
              truncated=len(out) > MAX_OUTPUT_CHARS)


def build_7zip_extract(archive: str, outdir: str) -> list:
    return ["7z", "x", "-y", f"-o{outdir}", str(archive)]


def cli_7zip_extract(archive: str, output_dir: str,
                     runner: Optional[Runner] = None) -> dict:
    """Extract an archive into a directory with 7-Zip (overwrites files)."""
    try:
        p = _input_path(archive)
        outdir = _output_path(output_dir)
    except ValueError as exc:
        return err(exc)
    argv = build_7zip_extract(str(p), str(outdir))
    res = _run(argv, runner=runner)
    if not res["ok"]:
        return res
    failed = _check_failed(res, "7z")
    if failed:
        return failed
    audit("cli_7zip_extract", {"argv": argv})
    return ok(archive=str(p), output_dir=str(outdir),
              returncode=res["returncode"])


# --------------------------------------------------------------------------
# Git
# --------------------------------------------------------------------------

def build_git_status(repo: str) -> list:
    return ["git", "-C", str(repo), "status", "--porcelain=v1", "--branch"]


def cli_git_status(repo: str, runner: Optional[Runner] = None) -> dict:
    """Show a repo's working-tree status (porcelain v1, with branch line)."""
    try:
        p = _input_path(repo)
    except ValueError as exc:
        return err(exc)
    res = _run(build_git_status(str(p)), runner=runner)
    if not res["ok"]:
        return res
    failed = _check_failed(res, "git status")
    if failed:
        return failed
    out = res["stdout"]
    return ok(repo=str(p), output=out[:MAX_OUTPUT_CHARS],
              truncated=len(out) > MAX_OUTPUT_CHARS)


def build_git_log(repo: str, n: int = 10) -> list:
    return ["git", "-C", str(repo), "log", f"-{int(n)}", "--oneline"]


def cli_git_log(repo: str, n: int = 10, runner: Optional[Runner] = None) -> dict:
    """List the last n commits of a repo as [{hash, subject}]."""
    try:
        p = _input_path(repo)
    except ValueError as exc:
        return err(exc)
    res = _run(build_git_log(str(p), n), runner=runner)
    if not res["ok"]:
        return res
    failed = _check_failed(res, "git log")
    if failed:
        return failed
    commits = []
    for line in res["stdout"].splitlines():
        line = line.strip()
        if not line:
            continue
        hash_, _, subject = line.partition(" ")
        commits.append({"hash": hash_, "subject": subject})
    return ok(repo=str(p), commits=commits, count=len(commits))


# --------------------------------------------------------------------------
# Blender
# --------------------------------------------------------------------------

def build_blender_render(blend: str, out: str, frame: int = 1) -> list:
    return ["blender", "-b", str(blend), "-o", str(out), "-f", str(int(frame))]


def cli_blender_render(blend_path: str, output_prefix: str, frame: int = 1,
                       runner: Optional[Runner] = None) -> dict:
    """Render one frame of a .blend file in Blender background mode.

    `output_prefix` is a path prefix (Blender appends the frame number and
    extension, e.g. render_ -> render_0001.png).
    """
    try:
        blend = _input_path(blend_path)
        out = _output_path(output_prefix)
    except ValueError as exc:
        return err(exc)
    argv = build_blender_render(str(blend), str(out), frame)
    out.parent.mkdir(parents=True, exist_ok=True)
    res = _run(argv, timeout=1800, runner=runner)
    if not res["ok"]:
        return res
    failed = _check_failed(res, "blender")
    if failed:
        return failed
    audit("cli_blender_render", {"argv": argv})
    return ok(blend=str(blend), output_prefix=str(out), frame=int(frame),
              returncode=res["returncode"])


# --------------------------------------------------------------------------
# MCP registration
# --------------------------------------------------------------------------

def register(mcp) -> list:
    """Register the cli_* tools on a FastMCP server. Returns tool names.

    The wrappers expose only user-facing parameters (the test-only
    ``runner`` argument stays out of the MCP schema); references to the
    module-level implementations are captured first so the same-named
    wrappers do not recurse.
    """
    # NOTE: the wrappers below reuse the impl names, which makes those
    # names local to this function — so capture the module-level impls
    # through globals() instead of by bare name.
    _g = globals()
    _probe = _g["cli_ffmpeg_probe"]
    _convert = _g["cli_ffmpeg_convert"]
    _thumbnail = _g["cli_ffmpeg_thumbnail"]
    _identify = _g["cli_magick_identify"]
    _resize = _g["cli_magick_resize"]
    _zip_list = _g["cli_7zip_list"]
    _zip_extract = _g["cli_7zip_extract"]
    _git_status = _g["cli_git_status"]
    _git_log = _g["cli_git_log"]
    _render = _g["cli_blender_render"]

    @mcp.tool()
    def cli_ffmpeg_probe(path: str) -> dict:
        """Probe a media file with ffprobe: streams, codec, duration, size
        (parsed JSON). Requires ffmpeg installed."""
        return _probe(path)

    @mcp.tool()
    def cli_ffmpeg_convert(input_path: str, output_path: str,
                           extra_args: list = None) -> dict:
        """Convert/transcode a media file with ffmpeg (extra ffmpeg args
        such as codecs/filters go in extra_args). Overwrites the output."""
        return _convert(input_path, output_path, extra_args)

    @mcp.tool()
    def cli_ffmpeg_thumbnail(input_path: str, output_path: str,
                             at_seconds: float = 1.0) -> dict:
        """Extract a single frame from a video as an image (ffmpeg)."""
        return _thumbnail(input_path, output_path, at_seconds)

    @mcp.tool()
    def cli_magick_identify(path: str) -> dict:
        """Identify an image with ImageMagick: format, dimensions, size."""
        return _identify(path)

    @mcp.tool()
    def cli_magick_resize(input_path: str, output_path: str, width: int) -> dict:
        """Resize an image to the given width in pixels (aspect kept)
        with ImageMagick."""
        return _resize(input_path, output_path, width)

    @mcp.tool()
    def cli_7zip_list(archive: str) -> dict:
        """List the contents of an archive with 7-Zip (technical listing)."""
        return _zip_list(archive)

    @mcp.tool()
    def cli_7zip_extract(archive: str, output_dir: str) -> dict:
        """Extract an archive into a directory with 7-Zip (overwrites)."""
        return _zip_extract(archive, output_dir)

    @mcp.tool()
    def cli_git_status(repo: str) -> dict:
        """Show a git repo's working-tree status (porcelain, with branch)."""
        return _git_status(repo)

    @mcp.tool()
    def cli_git_log(repo: str, n: int = 10) -> dict:
        """List the last n commits of a git repo as {hash, subject}."""
        return _git_log(repo, n)

    @mcp.tool()
    def cli_blender_render(blend_path: str, output_prefix: str, frame: int = 1) -> dict:
        """Render one frame of a .blend file with Blender in background
        mode. output_prefix gets the frame number + extension appended."""
        return _render(blend_path, output_prefix, frame)

    return ["cli_ffmpeg_probe", "cli_ffmpeg_convert", "cli_ffmpeg_thumbnail",
            "cli_magick_identify", "cli_magick_resize", "cli_7zip_list",
            "cli_7zip_extract", "cli_git_status", "cli_git_log",
            "cli_blender_render"]
