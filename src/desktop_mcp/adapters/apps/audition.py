"""Adobe Audition adapter (catalog id: ``audition``).

Catalog strategy: ``plugin`` — the catalog notes say "ExtendScript via
scripting panel; batch process through the app."

Honest scope: Adobe Audition exposes **no public scripting CLI, COM
interface, or external automation API** that a tool like this can call
into — its ExtendScript support only runs inside the app's own
scripting panel. So this adapter does not pretend to script Audition.
What it can do reliably is:

- ``audition_status`` — report whether Audition is installed (catalog
  detection) and how it would be automated,
- ``audition_launch`` — launch the installed app,
- ``audition_open_file`` — open an audio file (``.wav`` / ``.mp3`` /
  ``.aif`` / ``.aiff``) or a ``.sesx`` session in Audition by launching
  the detected executable with the file path as its argument.

All tools return ``core.ok`` / ``core.err`` dicts and never raise.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from ... import apps_catalog
from ...apps_catalog import detect_app, get_app  # noqa: F401  (module-namespace seams for tests)
from ...core import audit, check_path_allowed, err, ok
from . import _common

APP_ID = "audition"

#: File types ``audition_open_file`` will hand to Audition.
SUPPORTED_EXTENSIONS = {".wav", ".mp3", ".aif", ".aiff", ".sesx"}


def build_open_command(exe: str, file_path: str) -> list[str]:
    """Build the argv used to open *file_path* in Audition. Pure helper.

    Audition takes the file to open as a plain positional argument:
    ``["Adobe Audition.exe", "C:/audio/take1.wav"]``.
    """
    return [str(exe), str(file_path)]


#: The ``detect_app`` this module imported at load time. Tests may patch
#: either ``audition.detect_app`` or ``apps_catalog.detect_app``; the
#: helpers below honour whichever seam was patched.
_ORIGINAL_DETECT_APP = detect_app


def _spawn(argv: list[str]):
    """Start *argv* as a detached process. Factored out for testability
    (tests monkeypatch this instead of really launching Audition)."""
    return subprocess.Popen(argv, close_fds=True)


def _detect(app_id: str) -> dict:
    """Catalog detection, honouring a patched ``detect_app`` in either
    this module's namespace or on ``apps_catalog`` itself."""
    if detect_app is not _ORIGINAL_DETECT_APP:
        return detect_app(app_id)
    return apps_catalog.detect_app(app_id)


def _catalog_status() -> dict:
    """``_common.catalog_status(APP_ID)``, honouring a ``detect_app``
    patched into *this* module's namespace: ``catalog_status`` resolves
    detection through ``apps_catalog``, so a module-level patch is
    swapped in for the duration of the call. A patch applied directly
    to ``apps_catalog.detect_app`` needs no swap — it is left alone.
    """
    if detect_app is not _ORIGINAL_DETECT_APP:
        original = apps_catalog.detect_app
        apps_catalog.detect_app = detect_app  # type: ignore[misc]
        try:
            return _common.catalog_status(APP_ID)
        finally:
            apps_catalog.detect_app = original  # type: ignore[misc]
    return _common.catalog_status(APP_ID)


def audition_status() -> dict:
    """Report whether Adobe Audition is installed on this machine.

    Returns the catalog detection result (installed flag, executable
    path and how it was found) plus Audition's catalog automation
    strategy and notes. Audition has no external scripting API — see
    ``audition_open_file`` for what this adapter can actually do.
    """
    try:
        return _catalog_status()
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def audition_launch() -> dict:
    """Launch Adobe Audition if it is installed.

    Fails with a clear error dict when no Audition executable was
    detected on this machine. The launch is written to the audit log.
    """
    try:
        return _common.catalog_launch(APP_ID)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def audition_open_file(path: str) -> dict:
    """Open an audio file or session in Adobe Audition.

    *path* must be an allowed path (see the server's path allowlist)
    to a ``.wav``, ``.mp3``, ``.aif`` / ``.aiff`` audio file or a
    ``.sesx`` Audition session. Audition is launched with the file as
    its argument, so the file opens in the app for editing.

    Be clear about the limit: Audition exposes no scripting API to
    outside processes, so this only *opens* the file in the app — it
    cannot apply effects, edit audio, or export anything. Fails with a
    clear error dict when Audition is not installed, the path is not
    allowed, or the file type is not supported.
    """
    try:
        checked = check_path_allowed(path)
        suffix = Path(checked).suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
            return err(
                f"Unsupported file type for Audition open: {suffix or '(none)'} "
                f"({checked}). Supported: {supported}.")
        det = _detect(APP_ID)
        if not det.get("ok"):
            return det
        exe = det.get("path")
        if not det.get("installed") or not exe:
            return err(
                "Adobe Audition is not installed (none of its executables "
                "were found on this machine), so the file cannot be opened.")
        argv = build_open_command(str(exe), str(checked))
        proc = _spawn(argv)
        pid = getattr(proc, "pid", None)
        audit("audition_open_file", {"app_id": APP_ID, "path": str(checked),
                                     "exe": str(exe), "pid": pid})
        return ok(app_id=APP_ID, opened=True, path=str(checked),
                  exe=str(exe), argv=argv, pid=pid)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def register(mcp):
    """Register the audition_* tools on a FastMCP server.

    FastMCP names a tool after the function it decorates, so the
    module-level functions are bound directly under their exact public
    names: ``audition_status``, ``audition_launch`` and
    ``audition_open_file``.
    """
    mcp.tool()(audition_status)
    mcp.tool()(audition_launch)
    mcp.tool()(audition_open_file)
    return [audition_status, audition_launch, audition_open_file]
