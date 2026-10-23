"""Core shared services: path allowlist, audit log, platform helpers.

Design rules for the whole project:
- Every tool returns a plain ``dict`` (JSON-serialisable). Errors are
  ``{"ok": False, "error": "..."}`` — tools never raise across the MCP boundary.
- Anything that writes to disk must pass ``check_path_allowed`` first.
- Every mutating call is appended to the audit log (JSONL).
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

IS_WINDOWS = sys.platform.startswith("win")


def _default_allowed_roots() -> list[Path]:
    roots = [Path.home()]
    extra = os.environ.get("DESKTOP_MCP_ALLOWED_PATHS", "")
    for part in extra.split(os.pathsep):
        if part.strip():
            roots.append(Path(part.strip()).expanduser())
    return [r.resolve() for r in roots]


ALLOWED_ROOTS: list[Path] = _default_allowed_roots()


def check_path_allowed(path: str | Path) -> Path:
    """Resolve *path* and require it to live under an allowed root.

    Raises ValueError with a user-readable message when not allowed.
    """
    p = Path(path).expanduser().resolve()
    for root in ALLOWED_ROOTS:
        if p == root or root in p.parents:
            return p
    allowed = ", ".join(str(r) for r in ALLOWED_ROOTS)
    raise ValueError(f"Path not allowed: {p}. Allowed roots: {allowed}. "
                     "Extend via DESKTOP_MCP_ALLOWED_PATHS (os.pathsep-separated).")


def audit_log_path() -> Path:
    base = os.environ.get("DESKTOP_MCP_HOME")
    home = Path(base).expanduser() if base else Path.home() / ".desktop-mcp"
    home.mkdir(parents=True, exist_ok=True)
    return home / "audit.jsonl"


def audit(tool: str, detail: dict) -> None:
    """Append one JSONL audit record. Fail-open: auditing must never break a tool."""
    try:
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "tool": tool, **detail}
        with audit_log_path().open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def ok(**fields) -> dict:
    return {"ok": True, **fields}


def err(exc: Exception | str) -> dict:
    return {"ok": False, "error": str(exc)}
