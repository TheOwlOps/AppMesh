"""DaVinci Resolve adapter — plugin strategy (official scripting API).

Catalog entry: ``davinci-resolve``, strategy ``plugin``, exe
``Resolve.exe``, no COM ProgID. Blackmagic Design ships an official
Python/Lua scripting API (the ``fusionscript`` / ``DaVinciResolveScript``
module) — that is the automation surface this adapter probes.

Honest limits (stated in every relevant docstring below):
- The scripting API requires DaVinci Resolve to be **running**; a script
  connects to the live instance via ``scriptapp("Resolve")``.
- **External** scripting (connecting from an outside process, which is
  what this adapter does) requires the **Studio** version of Resolve.
  The free version only allows scripting from inside the app.
- There is no supported headless / file-only automation for Resolve
  projects comparable to e.g. CapCut draft editing.

Every tool returns a plain ``core.ok`` / ``core.err`` dict and never
raises. The fusionscript probe is guarded end-to-end: any import or
connection failure is reported as ``scripting_available: False`` with a
short reason, never as an exception.
"""
from __future__ import annotations

import contextlib
import importlib
import inspect
import os
import subprocess
import sys
from pathlib import Path

from ... import apps_catalog
from ...apps_catalog import detect_app  # noqa: F401 — re-exported in this
# module's namespace so tests (and callers) can monkeypatch
# ``davinci_resolve.detect_app`` and have status/launch/open honour it.
from ...core import audit, check_path_allowed, err, ok
from ._common import catalog_launch, catalog_status

APP_ID = "davinci-resolve"


# --------------------------------------------------------------- detection

@contextlib.contextmanager
def _using_module_detect():
    """Run a block with ``apps_catalog.detect_app`` swapped for this
    module's ``detect_app`` global when the two differ.

    ``catalog_status`` / ``catalog_launch`` look ``detect_app`` up on the
    ``apps_catalog`` module at call time, so swapping that attribute is
    enough to make the module-level (patchable) name authoritative.
    """
    original = apps_catalog.detect_app
    module_detect = globals().get("detect_app")
    if module_detect is not None and module_detect is not original:
        apps_catalog.detect_app = module_detect  # type: ignore[misc]
        try:
            yield
        finally:
            apps_catalog.detect_app = original  # type: ignore[misc]
    else:
        yield


# ------------------------------------------------------- scripting probe

def _candidate_module_dirs() -> list[Path]:
    """Directories that may contain ``DaVinciResolveScript.py``.

    Pure-ish helper: reads the ``RESOLVE_SCRIPT_LIB`` / ``RESOLVE_SCRIPT_API``
    environment variables documented by Blackmagic Design plus the typical
    install locations per platform. Existence is checked by the caller.
    """
    dirs: list[Path] = []

    lib = os.environ.get("RESOLVE_SCRIPT_LIB", "").strip()
    if lib:
        lib_path = Path(lib).expanduser()
        # RESOLVE_SCRIPT_LIB usually points at the fusionscript shared
        # library file itself; the Python wrapper lives alongside it or
        # under the Developer's Scripting/Modules folder.
        dirs.append(lib_path if lib_path.suffix == "" else lib_path.parent)
        dirs.append(lib_path.parent)

    api = os.environ.get("RESOLVE_SCRIPT_API", "").strip()
    if api:
        api_path = Path(api).expanduser()
        dirs.append(api_path / "Modules")
        dirs.append(api_path)

    dirs.extend([
        Path("C:/ProgramData/Blackmagic Design/DaVinci Resolve/Support"
             "/Developer/Scripting/Modules"),
        Path("/Library/Application Support/Blackmagic Design/DaVinci Resolve"
             "/Developer/Scripting/Modules"),
        Path("/opt/resolve/Developer/Scripting/Modules"),
        Path("/opt/resolve/Developer/Scripting"),
        Path.home() / ".local" / "share" / "DaVinciResolve" / "Developer"
        / "Scripting" / "Modules",
    ])

    # De-duplicate, preserving order.
    seen: set[str] = set()
    unique: list[Path] = []
    for d in dirs:
        key = str(d)
        if key not in seen:
            seen.add(key)
            unique.append(d)
    return unique


def _default_import_fusionscript(name: str = "fusionscript"):
    """Import the Resolve scripting module, trying standard locations.

    Tries, in order: a plain ``import fusionscript``, a plain
    ``import DaVinciResolveScript``, then each candidate directory from
    :func:`_candidate_module_dirs` (temporarily prepended to ``sys.path``).
    Raises :class:`ImportError` with an aggregated reason if every
    attempt fails — callers (the probe) convert that to a dict.
    """
    names = [name]
    alt = "DaVinciResolveScript" if name == "fusionscript" else "fusionscript"
    if alt not in names:
        names.append(alt)

    errors: list[str] = []
    for mod_name in names:
        try:
            return importlib.import_module(mod_name)
        except Exception as exc:  # noqa: BLE001 — collect and try next route
            errors.append(f"{mod_name}: {exc}")

    for directory in _candidate_module_dirs():
        try:
            if not directory.is_dir():
                continue
        except Exception:
            continue
        dir_str = str(directory)
        added = dir_str not in sys.path
        if added:
            sys.path.insert(0, dir_str)
        try:
            for mod_name in names:
                try:
                    return importlib.import_module(mod_name)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{mod_name} via {dir_str}: {exc}")
        finally:
            if added:
                try:
                    sys.path.remove(dir_str)
                except ValueError:
                    pass

    raise ImportError(
        "Could not import the DaVinci Resolve scripting module "
        f"({' ; '.join(errors) if errors else 'no candidate locations found'}). "
        "Set RESOLVE_SCRIPT_LIB / RESOLVE_SCRIPT_API to the Resolve "
        "Developer/Scripting location, or run the probe from an "
        "environment where fusionscript is importable."
    )


def _call_import_fn(import_fn, name: str):
    """Call an injected *import_fn* with the module *name* if it wants one.

    Fakes in tests may be ``lambda name: module`` or ``lambda: module``;
    inspect the signature so both shapes work. A fake that raises
    ``ImportError`` itself propagates unchanged.
    """
    try:
        sig = inspect.signature(import_fn)
        params = [
            p for p in sig.parameters.values()
            if p.kind in (inspect.Parameter.POSITIONAL_ONLY,
                          inspect.Parameter.POSITIONAL_OR_KEYWORD,
                          inspect.Parameter.VAR_POSITIONAL)
        ]
        required = [p for p in params if p.default is inspect.Parameter.empty
                    and p.kind != inspect.Parameter.VAR_POSITIONAL]
        takes_varargs = any(p.kind == inspect.Parameter.VAR_POSITIONAL
                            for p in params)
        if required or takes_varargs or len(params) >= 1:
            return import_fn(name)
        return import_fn()
    except (ValueError, TypeError):
        # Signature introspection failed (some callables) — best effort:
        # try with the name, fall back to no-arg only on a TypeError that
        # is about arguments (an ImportError from the fake must surface).
        try:
            return import_fn(name)
        except TypeError:
            return import_fn()


def _probe_scripting(import_fn=None) -> dict:
    """Probe the real DaVinci Resolve scripting API. Never raises.

    ``import_fn`` is injectable for tests: a callable returning the
    fusionscript-like module (accepting the module name or no argument).
    When omitted, :func:`_default_import_fusionscript` tries the standard
    locations (direct import, ``RESOLVE_SCRIPT_LIB`` / ``RESOLVE_SCRIPT_API``
    and the typical Resolve support directories).

    Returns an ``ok(...)`` dict in every case — availability is data, not
    an error: ``scripting_available`` is True only when the module loads
    AND ``scriptapp("Resolve")`` returns a live Resolve object. Otherwise
    it is False with a short ``scripting_reason``.
    """
    try:
        if import_fn is not None:
            module = _call_import_fn(import_fn, "fusionscript")
        else:
            module = _default_import_fusionscript("fusionscript")
    except Exception as exc:  # noqa: BLE001 — probe never raises
        return ok(scripting_available=False,
                  scripting_reason=f"fusionscript import failed: {exc}",
                  resolve_connected=False, resolve=None)

    if module is None:
        return ok(scripting_available=False,
                  scripting_reason="fusionscript import returned None",
                  resolve_connected=False, resolve=None)

    scriptapp_fn = getattr(module, "scriptapp", None)
    if scriptapp_fn is None:
        return ok(
            scripting_available=False,
            scripting_reason=(
                "Scripting module loaded but exposes no scriptapp() — "
                "this does not look like the Resolve fusionscript module."),
            resolve_connected=False, resolve=None)

    try:
        resolve_obj = scriptapp_fn("Resolve")
    except Exception as exc:  # noqa: BLE001
        return ok(scripting_available=False,
                  scripting_reason=f"scriptapp('Resolve') failed: {exc}",
                  resolve_connected=False, resolve=None)

    if resolve_obj is None:
        return ok(
            scripting_available=False,
            scripting_reason=(
                "scriptapp('Resolve') returned None — DaVinci Resolve is "
                "likely not running, or external scripting is unavailable "
                "(external scripting requires the Studio version)."),
            resolve_connected=False, resolve=None)

    try:
        resolve_repr = str(resolve_obj)
    except Exception:  # noqa: BLE001
        resolve_repr = "Resolve instance"
    return ok(scripting_available=True, scripting_reason=None,
              resolve_connected=True, resolve=resolve_repr)


# ------------------------------------------------------------ open helper

def _spawn_project(exe: str, file_path: str):
    """Start *exe* with *file_path* as its argument. Factored for tests."""
    return subprocess.Popen([str(exe), str(file_path)], close_fds=True)


# ------------------------------------------------------------------ tools

def davinci_resolve_status() -> dict:
    """Report DaVinci Resolve installation status plus scripting availability.

    Returns the catalog detection result (installed, executable path,
    automation strategy "plugin") enriched with a live probe of the real
    DaVinci Resolve scripting API: ``scripting_available`` is True only
    when the fusionscript module loads and ``scriptapp("Resolve")``
    connects to a running Resolve instance. Note that the scripting API
    requires Resolve to be running, and external scripting requires the
    Studio version of DaVinci Resolve — the free version only allows
    scripts run from inside the app, so a False here with Resolve
    installed usually means "not running" or "not Studio", not a broken
    install. This tool never raises; probe failures are reported as
    ``scripting_available: False`` with a short reason.
    """
    try:
        with _using_module_detect():
            base = catalog_status(APP_ID)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        base = err(exc)
    try:
        probe = _probe_scripting()
    except Exception as exc:  # noqa: BLE001 — probe is guarded, belt+braces
        probe = ok(scripting_available=False,
                   scripting_reason=f"scripting probe failed: {exc}",
                   resolve_connected=False, resolve=None)
    result = dict(base)
    # catalog_status reads strategy/name/prog_id off the get_app() wrapper
    # rather than its nested "app" record, so those fields can arrive as
    # None — fill any gaps straight from the catalog record (enrichment).
    if not result.get("strategy") or not result.get("name"):
        try:
            record = apps_catalog.get_app(APP_ID)
            app_rec = record.get("app") or {}
            if not result.get("strategy"):
                result["strategy"] = app_rec.get("strategy")
            if not result.get("name"):
                result["name"] = app_rec.get("name")
            if result.get("prog_id") is None:
                result["prog_id"] = app_rec.get("prog_id")
        except Exception:  # noqa: BLE001 — enrichment is best-effort
            pass
    result["scripting_available"] = bool(probe.get("scripting_available"))
    result["scripting_reason"] = probe.get("scripting_reason")
    result["resolve_connected"] = bool(probe.get("resolve_connected"))
    return result


def davinci_resolve_launch() -> dict:
    """Launch DaVinci Resolve (Resolve.exe) if it is installed.

    Fails with a clear error when Resolve is not installed on this
    machine. Note that launching only starts the app: driving it
    afterwards goes through the scripting API (see
    davinci_resolve_scripting_status), which requires the Studio version
    for external scripting. The launch is written to the audit log.
    """
    try:
        with _using_module_detect():
            return catalog_launch(APP_ID)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)


def davinci_resolve_scripting_status() -> dict:
    """Probe the DaVinci Resolve scripting API (fusionscript) on its own.

    Attempts to load the fusionscript / DaVinciResolveScript module from
    the standard locations (including the RESOLVE_SCRIPT_LIB and
    RESOLVE_SCRIPT_API environment variables) and, if it loads, to
    connect via ``scriptapp("Resolve")``. Returns
    ``scripting_available`` (True only on a live connection),
    ``scripting_reason`` when unavailable, and ``resolve_connected``.
    The scripting API requires DaVinci Resolve to be running, and
    external scripting requires the Studio version — the free version
    restricts scripting to scripts run inside the app. This probe never
    raises; every failure is reported inside the returned dict.
    """
    try:
        probe = _probe_scripting()
    except Exception as exc:  # noqa: BLE001 — tools never raise
        probe = ok(scripting_available=False,
                   scripting_reason=f"scripting probe failed: {exc}",
                   resolve_connected=False, resolve=None)
    result = dict(probe)
    result["app_id"] = APP_ID
    return result


def davinci_resolve_open_project_file(path: str) -> dict:
    """Open a DaVinci Resolve project file (.drp) with the detected Resolve.exe.

    The path must live under an allowed root (see the server allowlist)
    and must end in .drp; anything else is rejected with an error dict.
    Resolve must be installed — its executable is found via the catalog
    detection, and the file is passed to it as a launch argument. Note
    that fully programmatic project control still requires the scripting
    API (Resolve running + Studio version for external scripting); this
    tool only asks Resolve to open the file.
    """
    try:
        checked = check_path_allowed(path)
    except Exception as exc:  # noqa: BLE001 — tools never raise
        return err(exc)
    if checked.suffix.lower() != ".drp":
        return err(f"Not a DaVinci Resolve project file (.drp): {checked}")
    if not checked.exists():
        return err(f"Project file not found: {checked}")
    try:
        with _using_module_detect():
            det = detect_app(APP_ID)
    except Exception as exc:  # noqa: BLE001
        return err(exc)
    if not det.get("ok"):
        return det
    if not det.get("installed"):
        return err("DaVinci Resolve is not installed (Resolve.exe was not "
                   "found on this machine), so the project cannot be opened.")
    exe = det.get("path")
    if not exe:
        return err("DaVinci Resolve detection returned no executable path.")
    try:
        proc = _spawn_project(exe, str(checked))
        pid = getattr(proc, "pid", None)
        audit("davinci_resolve_open_project_file",
              {"app_id": APP_ID, "path": str(checked), "exe": exe, "pid": pid})
        return ok(app_id=APP_ID, opened=True, path=str(checked),
                  executable=str(exe), pid=pid)
    except Exception as exc:  # noqa: BLE001
        return err(exc)


# --------------------------------------------------------------- register

def register(mcp):
    """Register the davinci_resolve_* tools on a FastMCP server.

    Binds davinci_resolve_status, davinci_resolve_launch,
    davinci_resolve_scripting_status and
    davinci_resolve_open_project_file. Returns the tool names.
    """
    mcp.tool()(davinci_resolve_status)
    mcp.tool()(davinci_resolve_launch)
    mcp.tool()(davinci_resolve_scripting_status)
    mcp.tool()(davinci_resolve_open_project_file)
    return [
        "davinci_resolve_status",
        "davinci_resolve_launch",
        "davinci_resolve_scripting_status",
        "davinci_resolve_open_project_file",
    ]
