"""Visual Studio adapter — EnvDTE COM automation with a devenv fallback.

Strategy (catalog id ``visual-studio``): ``com`` via the ``VisualStudio.DTE``
ProgID. Opening a solution goes through ``DTE.Solution.Open(path)`` and
opening a file through ``DTE.ItemOperations.OpenFile(path)``. When COM is
unavailable (not on Windows, pywin32 missing, no running/registered DTE),
the tools fall back to launching ``devenv.exe`` with the target as its
argument.

COM access is lazy and fully guarded: importing or registering this module
works on any platform, and every tool returns ``core.ok`` / ``core.err``
dicts — tools never raise.
"""
from __future__ import annotations

import subprocess

from ... import apps_catalog
from ...core import audit, check_path_allowed, err, ok
from ._common import ComApp, catalog_launch, catalog_status

APP_ID = "visual-studio"
PROG_ID = "VisualStudio.DTE"

# Lazy singleton: the DTE object is only created on first use, so importing
# this module never touches COM (and works fine on Linux without pywin32).
_com = ComApp(PROG_ID)


def _spawn(argv: list[str]):
    """Start *argv* as a detached process. Factored out for tests."""
    return subprocess.Popen(argv, close_fds=True)


def _launch_devenv_with(target: str) -> dict:
    """Fallback: launch devenv.exe with *target* (solution or file) as arg."""
    det = apps_catalog.detect_app(APP_ID)
    exe = det.get("path") if det.get("ok") else None
    argv = [exe or "devenv.exe", target]
    try:
        proc = _spawn(argv)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)
    audit("visual_studio_devenv_launch", {"argv": argv})
    return ok(opened=True, via="devenv", launched=True, path=target,
              argv=argv, pid=getattr(proc, "pid", None))


def _com_call(chain: str, path: str):
    """Walk a guarded attribute chain on the DTE object and call its leaf.

    ``chain`` is dotted, e.g. ``"Solution.Open"`` or
    ``"ItemOperations.OpenFile"``; every intermediate attribute must exist
    and the leaf must be callable. Raises RuntimeError with a readable
    message when any link is missing, so the caller can fall back.
    """
    obj = _com.app()
    parts = chain.split(".")
    for part in parts[:-1]:
        obj = getattr(obj, part, None)
        if obj is None:
            raise RuntimeError(f"VisualStudio.DTE has no attribute chain '{chain}'.")
    leaf = getattr(obj, parts[-1], None)
    if not callable(leaf):
        raise RuntimeError(f"VisualStudio.DTE '{chain}' is not callable.")
    return leaf(path)


def _open_via_com_or_devenv(chain: str, kind: str, path: str) -> dict:
    """Shared body of the open tools: allowlist → COM → devenv fallback."""
    try:
        checked = check_path_allowed(path)
    except Exception as exc:  # noqa: BLE001 — rejection happens before COM/spawn
        return err(exc)
    target = str(checked)
    try:
        _com_call(chain, target)
    except Exception:  # noqa: BLE001 — any COM failure falls back to devenv
        return _launch_devenv_with(target)
    audit("visual_studio_com_open", {"kind": kind, "chain": chain, "path": target})
    return ok(opened=True, via="com", prog_id=PROG_ID, path=target)


def open_solution(path: str) -> dict:
    """Open a solution (.sln) in Visual Studio via DTE, else devenv."""
    return _open_via_com_or_devenv("Solution.Open", "solution", path)


def open_file(path: str) -> dict:
    """Open a single file in Visual Studio via DTE, else devenv."""
    return _open_via_com_or_devenv("ItemOperations.OpenFile", "file", path)


def register(mcp):
    """Bind the visual_studio_* tools on a FastMCP server."""

    @mcp.tool()
    def visual_studio_status() -> dict:
        """Report whether Visual Studio is installed, its devenv path,
        detection source, and its automation strategy (EnvDTE COM)."""
        return catalog_status(APP_ID)

    @mcp.tool()
    def visual_studio_launch() -> dict:
        """Launch Visual Studio (devenv.exe) from the app catalog."""
        return catalog_launch(APP_ID)

    @mcp.tool()
    def visual_studio_open_solution(path: str) -> dict:
        """Open a solution file (.sln) in Visual Studio.

        The path must pass the AppMesh allowlist. Primary route: EnvDTE
        COM — ``VisualStudio.DTE.Solution.Open(path)``. Fallback: launch
        devenv.exe with the solution path as its argument.
        """
        return open_solution(path)

    @mcp.tool()
    def visual_studio_open_file(path: str) -> dict:
        """Open a single file in Visual Studio.

        The path must pass the AppMesh allowlist. Primary route: EnvDTE
        COM — ``VisualStudio.DTE.ItemOperations.OpenFile(path)``.
        Fallback: launch devenv.exe with the file path as its argument.
        """
        return open_file(path)
