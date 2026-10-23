"""Generic system tools: info, processes, launching apps, screenshots,
window listing, UI-tree inspection and synthetic keyboard input.

Design rules (see core.py):
- Every public function returns ``core.ok(...)`` / ``core.err(...)`` dicts
  and never raises.
- Anything that writes to disk or launches a program checks the path
  allowlist first (when the target is an existing file path) and writes
  an audit record.
- Windows-only capabilities (pywinauto / pyautogui) are imported lazily
  inside the functions so this module imports cleanly on any platform
  and the pure helpers stay testable everywhere.
"""
from __future__ import annotations

import csv
import getpass
import io
import os
import platform
import shutil
import socket
import subprocess
import sys
from pathlib import Path

from . import core
from .core import err, ok

_WINDOWS_ONLY_MSG = (
    "Windows-only tool: this capability requires a Windows host "
    "with the desktop-mcp[windows] extras installed."
)

_UI_TREE_MAX_LINES = 400

# Executable suffixes that should be started as processes (Windows);
# anything else that exists on disk is opened with its associated app.
_WINDOWS_EXECUTABLE_SUFFIXES = {".exe", ".bat", ".cmd", ".com"}


# ---------------------------------------------------------------------------
# Pure helpers (hermetically testable)
# ---------------------------------------------------------------------------

def _parse_process_list(text: str, fmt: str) -> list[dict]:
    """Parse raw process-list output into ``[{"pid": int, "name": str, ...}]``.

    ``fmt`` is ``"csv"`` for Windows ``tasklist /FO CSV`` output, or
    ``"ps"`` for POSIX ``ps -eo pid,comm`` output. Aliases ``"tasklist"`` /
    ``"windows"`` and ``"posix"`` / ``"unix"`` are accepted. Malformed or
    blank lines are skipped rather than raising.
    """
    fmt_norm = (fmt or "").strip().lower()
    if fmt_norm in {"csv", "tasklist", "windows", "win32"}:
        return _parse_tasklist_csv(text)
    if fmt_norm in {"ps", "posix", "unix", "linux", "macos"}:
        return _parse_ps(text)
    raise ValueError(f"Unknown process list format: {fmt!r} (expected 'csv' or 'ps')")


def _parse_tasklist_csv(text: str) -> list[dict]:
    processes: list[dict] = []
    reader = csv.reader(io.StringIO(text or ""))
    for row in reader:
        if not row or len(row) < 2:
            continue
        name = (row[0] or "").strip()
        pid_raw = (row[1] or "").strip()
        # Skip the header row ("Image Name","PID",...).
        if name.lower() == "image name" or pid_raw.lower() == "pid":
            continue
        try:
            pid = int(pid_raw)
        except ValueError:
            continue
        entry: dict = {"pid": pid, "name": name}
        if len(row) >= 5 and row[4].strip():
            entry["memory"] = row[4].strip()
        processes.append(entry)
    return processes


def _parse_ps(text: str) -> list[dict]:
    processes: list[dict] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split(None, 1)
        if len(parts) < 2:
            continue
        pid_raw, name = parts[0], parts[1].strip()
        # Skip the header line ("PID COMMAND" / "PID COMM").
        if pid_raw.upper() == "PID":
            continue
        try:
            pid = int(pid_raw)
        except ValueError:
            continue
        if name:
            processes.append({"pid": pid, "name": name})
    return processes


def _looks_like_path(target: str) -> bool:
    """True when *target* is written as a path rather than a bare app name."""
    return (
        "/" in target
        or "\\" in target
        or target.startswith(".")
        or target.startswith("~")
    )


# ---------------------------------------------------------------------------
# Tools (plain functions — register() binds these to MCP)
# ---------------------------------------------------------------------------

def system_info() -> dict:
    """Get basic information about this machine: operating system and
    platform, hostname, current user, Python version, and whether the
    host is Windows. Safe and read-only; call this first to understand
    what environment you are controlling."""
    try:
        try:
            user = getpass.getuser()
        except Exception:
            user = os.environ.get("USER") or os.environ.get("USERNAME") or ""
        return ok(
            platform=platform.platform(),
            system=platform.system(),
            hostname=socket.gethostname(),
            user=user,
            python_version=platform.python_version(),
            is_windows=core.IS_WINDOWS,
        )
    except Exception as exc:  # never raise across the MCP boundary
        return err(exc)


def system_list_processes(limit: int = 50) -> dict:
    """List running processes on this machine (pid and name), most
    useful for checking whether an application such as AutoCAD or
    CapCut is already running before launching or automating it.
    Returns at most ``limit`` processes (default 50)."""
    try:
        if limit is None or int(limit) < 1:
            limit = 50
        limit = int(limit)
        if core.IS_WINDOWS:
            cmd, fmt = ["tasklist", "/FO", "CSV", "/NH"], "csv"
            # tasklist /NH has no header, but the parser skips one if present;
            # use the headered form instead so parsing stays unambiguous.
            cmd = ["tasklist", "/FO", "CSV"]
        else:
            cmd, fmt = ["ps", "-eo", "pid,comm"], "ps"
        completed = subprocess.run(
            cmd, capture_output=True, text=True, timeout=15, check=False,
        )
        output = completed.stdout or ""
        if not output.strip() and completed.returncode != 0:
            return err(
                f"Process listing failed ({' '.join(cmd)} exited "
                f"{completed.returncode}): {(completed.stderr or '').strip()}"
            )
        processes = _parse_process_list(output, fmt)
        return ok(
            processes=processes[:limit],
            count=min(len(processes), limit),
            total=len(processes),
            truncated=len(processes) > limit,
        )
    except Exception as exc:
        return err(exc)


def system_launch_app(target: str, args: list[str] | None = None) -> dict:
    """Launch an application or open a file with its associated
    application on this machine. ``target`` is either the path to an
    executable/document (which must live under an allowed root when it
    is an existing file) or the name of a program on PATH such as
    ``notepad``. Optional ``args`` are passed to executables. Use this
    to start AutoCAD, CapCut, or any other desktop program before
    automating it."""
    try:
        if not target or not str(target).strip():
            return err("target must not be empty")
        target = str(target).strip()
        arg_list = [str(a) for a in (args or [])]

        raw_path = Path(target).expanduser()
        pid: int | None = None

        if raw_path.exists():
            # Existing file path: enforce the allowlist before touching it.
            resolved = core.check_path_allowed(raw_path)
            if core.IS_WINDOWS:
                if resolved.suffix.lower() in _WINDOWS_EXECUTABLE_SUFFIXES:
                    proc = subprocess.Popen([str(resolved), *arg_list])
                    pid = proc.pid
                else:
                    os.startfile(str(resolved))  # type: ignore[attr-defined]
            elif resolved.is_dir():
                proc = subprocess.Popen([str(resolved), *arg_list])
                pid = proc.pid
            else:
                proc = subprocess.Popen(
                    [str(resolved), *arg_list],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                pid = proc.pid
            launched_target = str(resolved)
        elif _looks_like_path(target):
            return err(f"Path not found: {target}")
        else:
            # Bare program name: resolve via PATH, launch as a process.
            if core.IS_WINDOWS:
                proc = subprocess.Popen([target, *arg_list])
            else:
                proc = subprocess.Popen(
                    [target, *arg_list],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            pid = proc.pid
            launched_target = shutil.which(target) or target

        core.audit("system_launch_app", {"target": launched_target, "args": arg_list, "pid": pid})
        return ok(target=launched_target, pid=pid, launched=True)
    except Exception as exc:
        return err(exc)


def system_screenshot(path: str) -> dict:
    """Take a screenshot of the whole desktop and save it as an image
    file at ``path`` (which must be under an allowed root, e.g. the
    user's home directory). Requires Pillow. Use this to visually check
    the state of an application before or after automating it."""
    try:
        if not path or not str(path).strip():
            return err("path must not be empty")
        resolved = core.check_path_allowed(path)
        try:
            from PIL import ImageGrab
        except ImportError:
            return err(
                "Pillow is not installed, so screenshots are unavailable. "
                "Install Pillow (it is part of the desktop-mcp[windows] extras): "
                "pip install Pillow  /  pip install \"desktop-mcp[windows]\""
            )
        image = ImageGrab.grab()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        image.save(str(resolved))
        core.audit("system_screenshot", {"path": str(resolved)})
        width, height = image.size
        return ok(path=str(resolved), width=width, height=height)
    except Exception as exc:
        return err(exc)


def system_list_windows() -> dict:
    """List the top-level windows currently open on the desktop
    (title, class name, handle, visibility). Windows-only. Use this to
    find the exact window title of an application before calling
    system_ui_tree or sending it keyboard input."""
    try:
        if not core.IS_WINDOWS:
            return err(_WINDOWS_ONLY_MSG)
        try:
            from pywinauto import Desktop
        except ImportError:
            return err(
                "pywinauto is not installed. Install the Windows extras: "
                "pip install \"desktop-mcp[windows]\""
            )
        windows = []
        for win in Desktop(backend="uia").windows():
            try:
                info = win.element_info
                rect = win.rectangle()
                windows.append({
                    "title": win.window_text(),
                    "class_name": win.class_name(),
                    "handle": int(info.handle),
                    "pid": int(info.process_id),
                    "visible": bool(win.is_visible()),
                    "enabled": bool(win.is_enabled()),
                    "rect": [rect.left, rect.top, rect.right, rect.bottom],
                })
            except Exception:
                continue
        return ok(windows=windows, count=len(windows))
    except Exception as exc:
        return err(exc)


def system_ui_tree(window_title_contains: str = "") -> dict:
    """Dump the UI Automation control tree of a window as nested text
    (control type, name, class, rectangle for every element), truncated
    to about 400 lines. Windows-only. ``window_title_contains`` selects
    the first window whose title contains that text (case-insensitive);
    leave it empty to inspect the foreground/active window. Use this to
    discover the names of buttons, menus and fields you can interact
    with in an application that has no API, such as CapCut."""
    try:
        if not core.IS_WINDOWS:
            return err(_WINDOWS_ONLY_MSG)
        try:
            from pywinauto import Desktop
        except ImportError:
            return err(
                "pywinauto is not installed. Install the Windows extras: "
                "pip install \"desktop-mcp[windows]\""
            )

        desktop = Desktop(backend="uia")
        target_win = None
        needle = (window_title_contains or "").strip().lower()
        if needle:
            for win in desktop.windows():
                try:
                    if needle in (win.window_text() or "").lower():
                        target_win = win
                        break
                except Exception:
                    continue
            if target_win is None:
                return err(f"No window found with title containing: {window_title_contains!r}")
        else:
            try:
                target_win = desktop.active()
            except Exception:
                target_win = None
            if target_win is None:
                wins = desktop.windows()
                if not wins:
                    return err("No top-level windows found")
                target_win = wins[0]

        lines: list[str] = []

        def _walk(ctrl, depth: int) -> None:
            if len(lines) >= _UI_TREE_MAX_LINES:
                return
            try:
                info = ctrl.element_info
                rect = ctrl.rectangle()
                label = (
                    f"{'  ' * depth}{info.control_type or 'Control'} "
                    f"{ctrl.window_text()!r} class={ctrl.class_name()!r} "
                    f"rect=({rect.left},{rect.top},{rect.right},{rect.bottom})"
                )
            except Exception as exc:
                label = f"{'  ' * depth}<unreadable control: {exc}>"
            lines.append(label)
            if len(lines) >= _UI_TREE_MAX_LINES:
                return
            try:
                children = ctrl.children()
            except Exception:
                children = []
            for child in children:
                _walk(child, depth + 1)
                if len(lines) >= _UI_TREE_MAX_LINES:
                    return

        _walk(target_win, 0)
        truncated = len(lines) >= _UI_TREE_MAX_LINES
        if truncated:
            lines = lines[:_UI_TREE_MAX_LINES]
        try:
            title = target_win.window_text()
        except Exception:
            title = ""
        return ok(
            title=title,
            tree="\n".join(lines),
            lines=len(lines),
            truncated=truncated,
        )
    except Exception as exc:
        return err(exc)


def system_send_hotkey(keys: str | list[str]) -> dict:
    """Send a keyboard shortcut to the currently focused window, e.g.
    ``ctrl+s`` to save, ``ctrl+shift+s``, ``alt+f4``, or a single key
    such as ``enter`` or ``esc``. Windows-only. Focus the target
    application first (system_launch_app / system_list_windows) —
    the shortcut goes to whatever window has focus."""
    try:
        if not core.IS_WINDOWS:
            return err(_WINDOWS_ONLY_MSG)
        if isinstance(keys, (list, tuple)):
            tokens = [str(k).strip().lower() for k in keys if str(k).strip()]
        elif isinstance(keys, str):
            tokens = [part.strip().lower() for part in keys.split("+") if part.strip()]
        else:
            return err("keys must be a string like 'ctrl+s' or a list of key names")
        if not tokens:
            return err("keys must not be empty")
        try:
            import pyautogui
        except ImportError:
            return err(
                "pyautogui is not installed. Install the Windows extras: "
                "pip install \"desktop-mcp[windows]\""
            )
        if len(tokens) == 1:
            pyautogui.press(tokens[0])
        else:
            pyautogui.hotkey(*tokens)
        core.audit("system_send_hotkey", {"keys": tokens})
        return ok(keys=tokens, sent=True)
    except Exception as exc:
        return err(exc)


def system_type_text(text: str) -> dict:
    """Type text into the currently focused window/control as if it
    were typed on the keyboard. Windows-only. Note: synthetic typing
    works reliably for ASCII text; for long or non-ASCII text prefer
    setting the value through system_ui_tree-discovered controls or a
    file-based workflow when the application supports one."""
    try:
        if not core.IS_WINDOWS:
            return err(_WINDOWS_ONLY_MSG)
        if text is None:
            return err("text must not be None")
        text = str(text)
        try:
            import pyautogui
        except ImportError:
            return err(
                "pyautogui is not installed. Install the Windows extras: "
                "pip install \"desktop-mcp[windows]\""
            )
        pyautogui.write(text, interval=0.01)
        # Audit only the length — never log the typed content itself.
        core.audit("system_type_text", {"length": len(text)})
        return ok(typed=len(text))
    except Exception as exc:
        return err(exc)


# ---------------------------------------------------------------------------
# MCP registration
# ---------------------------------------------------------------------------

_TOOLS = (
    system_info,
    system_list_processes,
    system_launch_app,
    system_screenshot,
    system_list_windows,
    system_send_hotkey,
    system_type_text,
    system_ui_tree,
)


def register(mcp) -> None:
    """Bind all system_* functions as MCP tools on *mcp*.

    Thin wrappers: the plain functions above are registered directly,
    so each function's name becomes the tool name and its docstring
    becomes the tool description the LLM sees.
    """
    for fn in _TOOLS:
        mcp.tool()(fn)
