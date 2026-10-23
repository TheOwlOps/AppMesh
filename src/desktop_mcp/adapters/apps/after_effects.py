"""Adobe After Effects adapter (catalog id: ``after-effects``).

Catalog strategy: ``plugin`` — the catalog notes say "ExtendScript
(.jsx) via command line -r; render with aerender.exe."

After Effects' real external scripting route is ExtendScript:

- ``AfterFX.exe -r script.jsx`` launches After Effects and runs the
  ExtendScript (.jsx) file ``script.jsx`` — that is what
  ``after_effects_run_script`` does.
- ``aerender.exe`` is Adobe's headless renderer. It ships next to
  ``AfterFX.exe`` in the same install folder and renders a composition
  without opening the GUI, e.g.
  ``aerender.exe -project proj.aep -comp "Main" -output out.mov`` —
  that is what ``after_effects_render`` does.

Executable resolution follows the catalog: the AfterFX path comes from
``detect_app(APP_ID)``; ``aerender.exe`` is resolved from the detected
install directory (its sibling), falling back to a ``PATH`` probe via
``shutil.which``. When an executable cannot be found the tools return
a clear ``err`` dict — they never raise.

All script/project/output paths must pass
``core.check_path_allowed`` before anything runs. All tools return
``core.ok`` / ``core.err`` dicts and never raise. Execution goes
through :func:`_common.run_cli`, whose ``runner`` parameter is
injectable for tests.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from ... import apps_catalog
from ...apps_catalog import detect_app, get_app  # noqa: F401  (module-namespace seams for tests)
from ...core import audit, check_path_allowed, err, ok
from . import _common
from ._common import catalog_launch, catalog_status, run_cli

APP_ID = "after-effects"

#: ExtendScript runs can do real work inside After Effects; give them
#: five minutes before the CLI runner gives up.
RUN_SCRIPT_TIMEOUT = 300

#: Renders are long-running by nature; give aerender an hour.
RENDER_TIMEOUT = 3600

#: Names probed on PATH when aerender is not found next to AfterFX.
AERENDER_EXE_NAMES = ("aerender.exe", "aerender")


# ------------------------------------------------------------------ builders

def build_extendscript_command(afterfx_exe, script_path) -> list[str]:
    """Pure argv builder for running an ExtendScript file in After Effects.

    ``AfterFX.exe -r script.jsx`` runs the .jsx ExtendScript file:

    >>> build_extendscript_command("AfterFX.exe", "fix.jsx")
    ['AfterFX.exe', '-r', 'fix.jsx']
    """
    return [str(afterfx_exe), "-r", str(script_path)]


def build_aerender_command(aerender_exe, project_path, comp, output_path) -> list[str]:
    """Pure argv builder for one headless aerender render.

    >>> build_aerender_command("aerender.exe", "p.aep", "Main", "o.mov")
    ['aerender.exe', '-project', 'p.aep', '-comp', 'Main', '-output', 'o.mov']
    """
    return [str(aerender_exe), "-project", str(project_path),
            "-comp", str(comp), "-output", str(output_path)]


# ------------------------------------------------------- executable resolution

def _sibling_exe(exe_path, sibling_name: str) -> str:
    """Path of *sibling_name* next to *exe_path* (either separator)."""
    text = str(exe_path)
    if "\\" in text:
        return text.rsplit("\\", 1)[0] + "\\" + sibling_name
    return str(Path(text).parent / sibling_name)


def resolve_afterfx_exe() -> str | None:
    """Detected AfterFX.exe path, or ``None`` when not installed.

    Detection goes through this module's ``detect_app`` reference (the
    catalog's detector; kept module-level so tests can substitute it).
    """
    try:
        det = detect_app(APP_ID)
    except Exception:  # noqa: BLE001 — a broken probe means "not found"
        return None
    if not isinstance(det, dict) or not det.get("ok"):
        return None
    if not det.get("installed") or not det.get("path"):
        return None
    return str(det["path"])


def resolve_aerender_exe(which_fn=None) -> str | None:
    """Resolve aerender.exe, or ``None`` when it cannot be found.

    aerender ships next to AfterFX, so the sibling of the detected
    AfterFX executable wins first. Otherwise fall back to a PATH probe
    (``shutil.which``, or the injected *which_fn*) for ``aerender.exe``
    / ``aerender``.
    """
    afterfx = resolve_afterfx_exe()
    if afterfx:
        return _sibling_exe(afterfx, "aerender.exe")
    which = which_fn if which_fn is not None else shutil.which
    for name in AERENDER_EXE_NAMES:
        try:
            found = which(name)
        except Exception:  # noqa: BLE001 — a broken probe means "not found"
            found = None
        if found:
            return str(found)
    return None


def _catalog_status() -> dict:
    """``_common.catalog_status(APP_ID)``, honouring a monkeypatched
    module-level ``detect_app``.

    ``catalog_status`` resolves detection through the ``apps_catalog``
    module, so a ``detect_app`` patched into *this* module's namespace
    (the test seam) is swapped in for the duration of the call.
    """
    original = apps_catalog.detect_app
    if detect_app is not original:
        apps_catalog.detect_app = detect_app  # type: ignore[misc]
        try:
            return catalog_status(APP_ID)
        finally:
            apps_catalog.detect_app = original  # type: ignore[misc]
    return catalog_status(APP_ID)


# --------------------------------------------------------------------- tools

def after_effects_status() -> dict:
    """Report whether Adobe After Effects is installed on this machine.

    Returns the catalog detection result (installed flag, executable
    path and how it was found) plus After Effects' catalog automation
    strategy (plugin — ExtendScript / aerender).
    """
    try:
        return _catalog_status()
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def after_effects_launch() -> dict:
    """Launch Adobe After Effects if it is installed.

    Fails with a clear error dict when no AfterFX executable was
    detected on this machine. The launch is written to the audit log.
    """
    try:
        return catalog_launch(APP_ID)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def after_effects_run_script(script_path: str, runner=None) -> dict:
    """Run an ExtendScript (.jsx) file in Adobe After Effects.

    Runs ``AfterFX.exe -r <script_path>``: After Effects launches (or
    reuses its running instance) and executes the script. *script_path*
    must live under an allowed root (see ``core.check_path_allowed``).

    Fails with a clear error dict when the path is not allowed or
    After Effects is not installed. *runner* is injectable for tests
    and is forwarded to :func:`_common.run_cli` (a callable
    ``(argv, timeout) -> (returncode, stdout, stderr)``).
    """
    try:
        checked = check_path_allowed(script_path)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)
    exe = resolve_afterfx_exe()
    if not exe:
        return err(
            "Adobe After Effects is not installed (AfterFX.exe was not "
            "found on this machine), so the ExtendScript file cannot "
            "be run. Install After Effects or check the catalog "
            "detection with after_effects_status.")
    argv = build_extendscript_command(exe, str(checked))
    res = run_cli(argv, timeout=RUN_SCRIPT_TIMEOUT, runner=runner)
    if not res.get("ok"):
        return res
    audit("after_effects_run_script",
          {"app_id": APP_ID, "script": str(checked), "exe": exe, "argv": argv})
    return ok(app_id=APP_ID, script=str(checked), exe=exe, argv=argv,
              returncode=res.get("returncode"),
              stdout=res.get("stdout", ""), stderr=res.get("stderr", ""))


def after_effects_render(project_path: str, comp: str, output_path: str,
                         runner=None) -> dict:
    """Render one composition headlessly with aerender.

    Runs ``aerender.exe -project <project_path> -comp <comp>
    -output <output_path>``. *project_path* is an After Effects
    project (.aep); *comp* is the exact name of the composition to
    render; *output_path* is the movie/image-sequence file to write.
    Both paths must live under an allowed root (see
    ``core.check_path_allowed``).

    aerender ships next to AfterFX.exe, so it is resolved from the
    detected After Effects install folder, falling back to PATH.
    Fails with a clear error dict when a path is not allowed, *comp*
    is empty, or aerender cannot be found. *runner* is injectable for
    tests and is forwarded to :func:`_common.run_cli`.
    """
    try:
        project = check_path_allowed(project_path)
        output = check_path_allowed(output_path)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)
    if not str(comp or "").strip():
        return err("A composition name is required for aerender "
                   "(pass the exact comp name from the project).")
    exe = resolve_aerender_exe()
    if not exe:
        return err(
            "aerender.exe was not found on this machine: Adobe After "
            "Effects does not appear to be installed (aerender ships "
            "next to AfterFX.exe), and no aerender executable was "
            "found on PATH, so the project cannot be rendered.")
    argv = build_aerender_command(exe, str(project), str(comp), str(output))
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)
    res = run_cli(argv, timeout=RENDER_TIMEOUT, runner=runner)
    if not res.get("ok"):
        return res
    audit("after_effects_render",
          {"app_id": APP_ID, "project": str(project), "comp": str(comp),
           "output": str(output), "exe": exe, "argv": argv})
    return ok(app_id=APP_ID, project=str(project), comp=str(comp),
              output=str(output), exe=exe, argv=argv,
              returncode=res.get("returncode"),
              stdout=res.get("stdout", ""), stderr=res.get("stderr", ""))


def register(mcp):
    """Register the after_effects_* tools on a FastMCP server.

    Returns the list of tool names bound.
    """
    # Capture the module-level impls first: the wrappers below reuse
    # the same names, which would otherwise make them recurse.
    _g = globals()
    _status = _g["after_effects_status"]
    _launch = _g["after_effects_launch"]
    _run_script = _g["after_effects_run_script"]
    _render = _g["after_effects_render"]

    @mcp.tool()
    def after_effects_status() -> dict:
        """Check whether Adobe After Effects is installed on this
        machine, where AfterFX.exe lives, and which automation
        strategy it uses (plugin — ExtendScript via AfterFX -r, plus
        headless renders via aerender.exe)."""
        return _status()

    @mcp.tool()
    def after_effects_launch() -> dict:
        """Launch Adobe After Effects. Fails with a clear error when
        After Effects is not installed."""
        return _launch()

    @mcp.tool()
    def after_effects_run_script(script_path: str) -> dict:
        """Run an ExtendScript (.jsx) file in Adobe After Effects via
        ``AfterFX.exe -r <script_path>``. Use this to automate After
        Effects: create/open projects, add comps/layers, apply
        effects, or queue renders from inside the app. The script
        path must be under an allowed root."""
        return _run_script(script_path)

    @mcp.tool()
    def after_effects_render(project_path: str, comp: str,
                             output_path: str) -> dict:
        """Render a composition headlessly with aerender:
        ``aerender.exe -project <project> -comp <comp> -output
        <output>``. *comp* is the exact composition name inside the
        .aep project. Both paths must be under an allowed root.
        Renders can take a long time for large projects."""
        return _render(project_path, comp, output_path)

    return ["after_effects_status", "after_effects_launch",
            "after_effects_run_script", "after_effects_render"]
