"""Shared helpers for the per-app adapters in this package.

Every per-app module follows the same contract:
- ``APP_ID`` — the exact catalog id from ``desktop_mcp.apps_catalog``.
- ``register(mcp)`` — binds at least ``<prefix>_status``, ``<prefix>_launch``
  and one strategy-specific action tool, where ``<prefix>`` is the module
  name (catalog id with ``-`` replaced by ``_``).
- All tools return ``core.ok`` / ``core.err`` dicts and never raise.
"""
from __future__ import annotations

import shutil
import subprocess

from ... import apps_catalog
from ...core import IS_WINDOWS, audit, err, ok  # noqa: F401  (re-exported for adapters)


def catalog_status(app_id: str) -> dict:
    """Detection + strategy info for *app_id* straight from the catalog."""
    det = apps_catalog.detect_app(app_id)
    if not det.get("ok"):
        return det
    # detect_app already merges the catalog record fields (name, strategy,
    # prog_id) into its result — prefer those. Fall back to get_app(),
    # unwrapping its {"app": {...}} envelope, for any field still missing.
    # (Reading fields straight off the get_app() wrapper silently yielded
    # None — bug caught by Squad 6's android-studio worker.)
    name, strategy, prog_id = det.get("name"), det.get("strategy"), det.get("prog_id")
    if name is None or strategy is None:
        got = apps_catalog.get_app(app_id)
        if got.get("ok"):
            inner = got.get("app") if isinstance(got.get("app"), dict) else got
            name = name if name is not None else inner.get("name")
            strategy = strategy if strategy is not None else inner.get("strategy")
            prog_id = prog_id if prog_id is not None else inner.get("prog_id")
    return ok(app_id=app_id, installed=det.get("installed", False),
              path=det.get("path"), via=det.get("via"),
              strategy=strategy, prog_id=prog_id, name=name or app_id)


def catalog_launch(app_id: str) -> dict:
    return apps_catalog.launch_app_from_catalog(app_id)


class ComApp:
    """Lazy COM connection to one application, injectable for tests."""

    def __init__(self, prog_id: str, factory=None):
        self.prog_id = prog_id
        self._factory = factory
        self._app = None

    def app(self):
        if self._app is None:
            if self._factory is not None:
                self._app = self._factory()
            else:
                if not IS_WINDOWS:
                    raise RuntimeError(
                        f"{self.prog_id} uses COM automation, which requires Windows.")
                try:
                    import win32com.client  # type: ignore
                except ImportError as exc:
                    raise RuntimeError(
                        "pywin32 is not installed; on Windows run: "
                        'pip install "desktop-mcp[windows]"') from exc
                self._app = win32com.client.Dispatch(self.prog_id)
        return self._app

    def open_via(self, collection: str, path: str) -> dict:
        """Open *path* through ``app.<collection>.Open(path)`` (Documents,
        Workbooks, Presentations, … depending on the app)."""
        from ...core import check_path_allowed
        try:
            checked = check_path_allowed(path)
            app = self.app()
            doc = getattr(app, collection).Open(str(checked))
            audit("com_open", {"prog_id": self.prog_id, "collection": collection,
                               "path": str(checked)})
            name = getattr(doc, "Name", None) or getattr(doc, "FullName", None)
            return ok(opened=True, document=str(name) if name else None,
                      path=str(checked))
        except Exception as exc:  # noqa: BLE001 — tools never raise
            return err(exc)


def run_cli(argv: list[str], timeout: int = 120, runner=None) -> dict:
    """Run *argv* and return ok({stdout, stderr, returncode}) / err dicts.

    ``runner`` is injectable for tests: a callable(argv, timeout) returning
    (returncode, stdout, stderr).
    """
    try:
        if runner is not None:
            code, out, err_text = runner(argv, timeout)
        else:
            if shutil.which(argv[0]) is None:
                return err(f"Executable not found on PATH: {argv[0]}")
            proc = subprocess.run(argv, capture_output=True, text=True,
                                  timeout=timeout)
            code, out, err_text = proc.returncode, proc.stdout, proc.stderr
        if code != 0:
            return err(f"{argv[0]} exited {code}: {(err_text or out)[:500]}")
        return ok(argv=argv, returncode=code, stdout=out, stderr=err_text)
    except Exception as exc:  # noqa: BLE001
        return err(exc)


def focus_window(title_contains: str) -> dict:
    """Bring a window whose title contains *title_contains* to the front
    (Windows UI Automation via pywinauto; graceful err elsewhere)."""
    try:
        if not IS_WINDOWS:
            return err("Window focus uses Windows UI Automation; "
                       "this machine is not running Windows.")
        try:
            from pywinauto import Desktop  # type: ignore
        except ImportError:
            return err('pywinauto is not installed; run: pip install "desktop-mcp[windows]"')
        for win in Desktop(backend="uia").windows():
            try:
                title = win.window_text() or ""
            except Exception:
                continue
            if title_contains.lower() in title.lower():
                win.set_focus()
                return ok(focused=True, window=title)
        return err(f"No window matching '{title_contains}' is currently open.")
    except Exception as exc:  # noqa: BLE001
        return err(exc)
