"""Microsoft Outlook adapter (catalog id ``outlook``, strategy ``com``).

Outlook is automated through its COM interface (ProgID
``Outlook.Application``): mail, calendar and contacts live behind the
MAPI namespace. Unlike Word/Excel/PowerPoint, Outlook does **not**
expose a ``Documents`` collection with an ``Open`` method, so
``outlook_open_file`` tries the generic COM open first and otherwise
falls back to launching the detected OUTLOOK.EXE with the file as its
argument (Outlook opens .msg/.pst files passed on the command line).
"""
from __future__ import annotations

import subprocess

from ... import apps_catalog
from ...core import check_path_allowed
from ._common import ComApp, audit, catalog_launch, catalog_status, err, ok

APP_ID = "outlook"
PROG_ID = "Outlook.Application"

# Module-level COM handle (lazy: nothing connects until a tool runs).
# Tests inject a fake by replacing this with
# ``ComApp(PROG_ID, factory=<callable returning a fake app>)``.
_com_app = ComApp(PROG_ID)


def _launch_with_file(exe_path: str, file_path: str):
    """Launch the Outlook executable with *file_path* as its argument.

    Factored out at module level so tests can monkeypatch it instead of
    spawning a real process.
    """
    return subprocess.Popen([str(exe_path), str(file_path)], close_fds=True)


def register(mcp):
    """Register the outlook_* tools on a FastMCP server."""

    @mcp.tool()
    def outlook_status() -> dict:
        """Report whether Microsoft Outlook is installed on this machine,
        where its executable lives, and how it can be automated
        (COM via the Outlook.Application ProgID)."""
        return catalog_status(APP_ID)

    @mcp.tool()
    def outlook_launch() -> dict:
        """Launch Microsoft Outlook. Fails with a clear error when
        Outlook is not installed on this machine. The launch is
        written to the audit log."""
        return catalog_launch(APP_ID)

    @mcp.tool()
    def outlook_open_file(path: str) -> dict:
        """Open a file in Microsoft Outlook (e.g. a .msg message file
        or a .pst data file).

        The path must live under an allowed root. Outlook has no
        Documents.Open collection, so this first attempts a COM open
        and, when that fails, launches the detected OUTLOOK.EXE with
        the file as its argument.
        """
        # Allowlist first: a disallowed path must return before any
        # COM connection or process launch is attempted.
        try:
            checked = check_path_allowed(path)
        except Exception as exc:  # noqa: BLE001 — tools never raise
            return err(exc)

        com_result = _com_app.open_via("Documents", str(checked))
        if com_result.get("ok"):
            return {**com_result, "app_id": APP_ID, "via": "com"}

        det = apps_catalog.detect_app(APP_ID)
        if not det.get("ok"):
            return det
        if not det.get("installed") or not det.get("path"):
            return err(
                "Outlook could not open the file via COM "
                f"({com_result.get('error')}) and no Outlook executable "
                "was found on this machine for the launch fallback.")
        try:
            proc = _launch_with_file(det["path"], str(checked))
        except Exception as exc:  # noqa: BLE001 — tools never raise
            return err(exc)
        audit("outlook_open_file", {"app_id": APP_ID, "path": str(checked),
                                    "via": "exe_launch", "exe": det["path"]})
        return ok(app_id=APP_ID, opened=True, path=str(checked),
                  via="exe_launch", exe=det["path"],
                  pid=getattr(proc, "pid", None),
                  com_error=com_result.get("error"))

    @mcp.tool()
    def outlook_list_folders() -> dict:
        """List the top-level mail folders in the running Outlook
        profile (via the MAPI namespace: account / store roots such as
        "Inbox" parents). Requires Outlook's COM interface, i.e.
        Windows with Outlook installed; returns a clear error
        otherwise. Read-only."""
        try:
            app = _com_app.app()
            namespace = None
            get_namespace = getattr(app, "GetNamespace", None)
            if callable(get_namespace):
                try:
                    namespace = get_namespace("MAPI")
                except Exception:  # noqa: BLE001 — fall through to Session
                    namespace = None
            if namespace is None:
                namespace = getattr(app, "Session", None)
            if namespace is None:
                return err("Outlook COM object exposes neither "
                           "GetNamespace('MAPI') nor Session; cannot "
                           "enumerate folders.")
            collection = getattr(namespace, "Folders", None)
            if collection is None:
                return err("Outlook MAPI namespace has no Folders collection.")

            items = []
            count = getattr(collection, "Count", None)
            if count is not None:
                for index in range(1, int(count) + 1):
                    try:
                        items.append(collection.Item(index))
                    except Exception:  # noqa: BLE001 — skip unreadable folder
                        continue
            else:
                items = list(collection)

            folders = []
            for folder in items:
                name = getattr(folder, "Name", None)
                folder_path = getattr(folder, "FolderPath", None)
                folders.append({
                    "name": str(name) if name is not None else None,
                    "folder_path": str(folder_path) if folder_path is not None else None,
                })
            audit("outlook_list_folders", {"count": len(folders)})
            return ok(app_id=APP_ID, folders=folders, count=len(folders))
        except Exception as exc:  # noqa: BLE001 — tools never raise
            return err(exc)

    return APP_ID
