"""App catalog — the 100 desktop apps AppMesh knows about.

The catalog is the map an agent uses before touching any app:

- ``apps_list``    browse the 100 apps (optionally by category),
- ``apps_get``     full record for one app (how to automate it),
- ``apps_detect``  which of them are actually installed on this machine,
- ``apps_launch``  start an installed app by its catalog id.

Each entry records the *strategy* an adapter would use to automate the
app, in the project's preference order:

- ``com``     real COM Automation interface (deepest, most stable),
- ``plugin``  official scripting/plugin API (Python, ExtendScript, …),
- ``cli``     command-line interface / headless mode / debug protocol,
- ``file``    no API, but the project file format is editable on disk,
- ``ui``      nothing else: Windows UI Automation / keyboard / vision.

Detection is best-effort: PATH lookup first (``shutil.which``), then —
on Windows only — the usual install folders under Program Files /
Program Files (x86) / %LOCALAPPDATA%\\Programs.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .core import IS_WINDOWS, audit, err, ok

STRATEGIES = ("com", "cli", "file", "ui", "plugin")

CAD = "CAD & 3D"
VIDEO = "Video & Audio"
DESIGN = "Design & Photo"
OFFICE = "Office & Productivity"
DEV = "Dev Tools"
BROWSERS = "Browsers"
COMMS = "Communication"
UTILS = "Utilities & Media"


def _app(app_id, name, category, strategy, exe_names, prog_id, notes):
    return {
        "id": app_id,
        "name": name,
        "category": category,
        "strategy": strategy,
        "exe_names": list(exe_names),
        "prog_id": prog_id,
        "notes": notes,
    }


APPS: list[dict] = [
    # ---------------------------------------------------------- CAD & 3D (14)
    _app("autocad", "AutoCAD", CAD, "com", ["acad.exe"], "AutoCAD.Application",
         "Full COM Automation (pyautocad): draw, edit entities, layers, plot; adapter autocad_* already built."),
    _app("solidworks", "SolidWorks", CAD, "com", ["SLDWORKS.exe"], "SldWorks.Application",
         "COM API via pywin32: parts, assemblies, drawings, export STEP/PDF."),
    _app("revit", "Revit", CAD, "plugin", ["Revit.exe"], None,
         "No COM: automate via .NET RevitAPI add-in, Dynamo or journal-file playback."),
    _app("fusion-360", "Fusion 360", CAD, "plugin", ["Fusion360.exe"], None,
         "Built-in Python add-in API (adsk) and scripts folder; launch then run script."),
    _app("sketchup", "SketchUp", CAD, "plugin", ["SketchUp.exe"], None,
         "Ruby API via Plugins folder / Ruby console; LayOut files are separate."),
    _app("blender", "Blender", CAD, "plugin", ["blender.exe"], None,
         "Full Python API (bpy): run headless with blender --background --python script.py."),
    _app("3ds-max", "3ds Max", CAD, "plugin", ["3dsmax.exe"], None,
         "MAXScript and pymxs (Python) scripting; batch via 3dsmaxbatch.exe."),
    _app("maya", "Maya", CAD, "plugin", ["maya.exe"], None,
         "MEL and Python (maya.cmds / OpenMaya); headless via mayapy.exe."),
    _app("rhino", "Rhino", CAD, "plugin", ["Rhino.exe"], None,
         "RhinoPython / RhinoScript and grasshopper files; headless via Rhino.Inside or /runscript."),
    _app("freecad", "FreeCAD", CAD, "plugin", ["FreeCAD.exe"], None,
         "Python console and macros; headless via FreeCADCmd.exe."),
    _app("inventor", "Autodesk Inventor", CAD, "com", ["Inventor.exe"], "Inventor.Application",
         "COM API via pywin32: parts, assemblies, iLogic rules."),
    _app("catia", "CATIA", CAD, "com", ["CNEXT.exe"], "CATIA.Application",
         "COM Automation (VBScript heritage): documents, part design, macros."),
    _app("cinema-4d", "Cinema 4D", CAD, "plugin", ["Cinema 4D.exe"], None,
         "Python SDK (c4d) plugins/scripts; render via Commandline.exe."),
    _app("houdini", "Houdini", CAD, "plugin", ["houdini.exe", "hython.exe"], None,
         "hou Python module; headless scripting with hython.exe."),

    # ----------------------------------------------------- Video & Audio (16)
    _app("capcut", "CapCut", VIDEO, "file", ["CapCut.exe"], None,
         "No API: edit draft_content.json in the drafts folder (adapter capcut_* already built)."),
    _app("premiere-pro", "Adobe Premiere Pro", VIDEO, "plugin", ["Adobe Premiere Pro.exe"], None,
         "ExtendScript / UXP panels; project is XML-ish .prproj (gzip) — scripting preferred."),
    _app("after-effects", "Adobe After Effects", VIDEO, "plugin", ["AfterFX.exe"], None,
         "ExtendScript (.jsx) via command line -r; render with aerender.exe."),
    _app("davinci-resolve", "DaVinci Resolve", VIDEO, "plugin", ["Resolve.exe"], None,
         "Official Python/Lua scripting API (needs Studio for external scripting)."),
    _app("obs-studio", "OBS Studio", VIDEO, "plugin", ["obs64.exe"], None,
         "obs-websocket plugin gives full remote control; scenes are JSON in %APPDATA%."),
    _app("vlc", "VLC", VIDEO, "cli", ["vlc.exe"], None,
         "Rich CLI flags, RC interface and Lua HTTP API for playback control."),
    _app("filmora", "Filmora", VIDEO, "ui", ["Filmora.exe"], None,
         "No public API: UI automation only; project files are proprietary."),
    _app("camtasia", "Camtasia", VIDEO, "ui", ["CamtasiaStudio.exe"], None,
         "No scripting API: UI automation; .cmproj is a folder with XML assets inside."),
    _app("audacity", "Audacity", VIDEO, "plugin", ["audacity.exe"], None,
         "Macro commands and mod-script-pipe named pipe for scripted editing."),
    _app("fl-studio", "FL Studio", VIDEO, "ui", ["FL64.exe"], None,
         "No automation API: UI automation; some scripting via Piano roll / Edison in-app."),
    _app("ableton-live", "Ableton Live", VIDEO, "ui", ["Ableton Live.exe"], None,
         "Live Object Model needs Max for Live inside the app; outside control is UI/MIDI only."),
    _app("vegas-pro", "VEGAS Pro", VIDEO, "plugin", ["VEGAS Pro.exe", "vegas.exe"], None,
         "C#/JScript scripting API (ScriptHost) run from Tools > Scripting."),
    _app("handbrake", "HandBrake", VIDEO, "cli", ["HandBrake.exe", "HandBrakeCLI.exe"], None,
         "Fully scriptable via HandBrakeCLI.exe with presets (JSON)."),
    _app("clipchamp", "Clipchamp", VIDEO, "ui", ["Clipchamp.exe"], None,
         "Store app, no API: UI automation only."),
    _app("audition", "Adobe Audition", VIDEO, "plugin", ["Adobe Audition.exe"], None,
         "ExtendScript via scripting panel; batch process through the app."),
    _app("reaper", "REAPER", VIDEO, "plugin", ["reaper.exe"], None,
         "ReaScript (Lua/Python/EEL) plus CLI render: reaper.exe -renderproject."),

    # ---------------------------------------------------- Design & Photo (12)
    _app("photoshop", "Adobe Photoshop", DESIGN, "com", ["Photoshop.exe"], "Photoshop.Application",
         "COM + ExtendScript/UXP: actions, layers, batch export via scripting."),
    _app("illustrator", "Adobe Illustrator", DESIGN, "com", ["Illustrator.exe"], "Illustrator.Application",
         "COM + ExtendScript: documents, artboards, SVG/PDF export."),
    _app("lightroom", "Adobe Lightroom Classic", DESIGN, "plugin", ["Lightroom.exe"], None,
         "Lua plugin SDK inside the app; catalog is SQLite — read-only mining possible."),
    _app("indesign", "Adobe InDesign", DESIGN, "com", ["InDesign.exe"], "InDesign.Application",
         "COM + ExtendScript: documents, stories, PDF export."),
    _app("gimp", "GIMP", DESIGN, "plugin", ["gimp-2.10.exe", "gimp.exe"], None,
         "Batch mode with Script-Fu / Python-Fu: gimp -i -b commands."),
    _app("inkscape", "Inkscape", DESIGN, "cli", ["inkscape.exe"], None,
         "Excellent CLI: --actions, conversions SVG/PDF/PNG are first-class."),
    _app("coreldraw", "CorelDRAW", DESIGN, "com", ["CorelDRW.exe"], "CorelDRAW.Application",
         "COM/VBA automation: documents, shapes, export."),
    _app("figma", "Figma", DESIGN, "ui", ["Figma.exe"], None,
         "Desktop app has no local API (Plugin API runs inside the app; REST API is cloud) — UI only locally."),
    _app("canva", "Canva", DESIGN, "ui", ["Canva.exe"], None,
         "Electron wrapper of the web app: UI automation only."),
    _app("affinity-photo", "Affinity Photo", DESIGN, "ui", ["Photo.exe"], None,
         "No scripting API: UI automation; macros are in-app only."),
    _app("affinity-designer", "Affinity Designer", DESIGN, "ui", ["Designer.exe"], None,
         "No scripting API: UI automation only."),
    _app("paint-net", "Paint.NET", DESIGN, "ui", ["paintdotnet.exe"], None,
         "Plugin DLLs run in-app; outside control is UI automation."),

    # ---------------------------------------------- Office & Productivity (14)
    _app("word", "Microsoft Word", OFFICE, "com", ["WINWORD.EXE"], "Word.Application",
         "Full COM via pywin32/python-docx for files; live app control via COM."),
    _app("excel", "Microsoft Excel", OFFICE, "com", ["EXCEL.EXE"], "Excel.Application",
         "Full COM via pywin32/xlwings: cells, formulas, macros, charts."),
    _app("powerpoint", "Microsoft PowerPoint", OFFICE, "com", ["POWERPNT.EXE"], "PowerPoint.Application",
         "Full COM: slides, shapes, export to PDF/video."),
    _app("outlook", "Microsoft Outlook", OFFICE, "com", ["OUTLOOK.EXE"], "Outlook.Application",
         "COM: mail, calendar, contacts via MAPI namespaces."),
    _app("access", "Microsoft Access", OFFICE, "com", ["MSACCESS.EXE"], "Access.Application",
         "COM: databases, queries, reports."),
    _app("visio", "Microsoft Visio", OFFICE, "com", ["VISIO.EXE"], "Visio.Application",
         "COM: diagrams, shapes, connectors."),
    _app("onenote", "Microsoft OneNote", OFFICE, "com", ["ONENOTE.EXE"], "OneNote.Application",
         "COM (limited): notebooks/pages hierarchy XML; newer Store builds are UI-only."),
    _app("libreoffice-writer", "LibreOffice Writer", OFFICE, "cli", ["soffice.exe"], None,
         "soffice --headless --convert-to for documents; UNO bridge for deep control."),
    _app("libreoffice-calc", "LibreOffice Calc", OFFICE, "cli", ["soffice.exe"], None,
         "soffice headless conversions (xlsx/csv/pdf); UNO bridge for cell-level control."),
    _app("libreoffice-impress", "LibreOffice Impress", OFFICE, "cli", ["soffice.exe"], None,
         "soffice headless export of presentations; UNO bridge for slide control."),
    _app("notion", "Notion", OFFICE, "ui", ["Notion.exe"], None,
         "Electron wrapper: UI automation locally; official API is cloud-side."),
    _app("obsidian", "Obsidian", OFFICE, "cli", ["Obsidian.exe"], None,
         "obsidian:// URI scheme + plain Markdown vault files on disk; plugins in-app."),
    _app("foxit-pdf", "Foxit PDF Editor", OFFICE, "ui", ["FoxitPDFEditor.exe", "Foxit Reader.exe"], None,
         "No general scripting API for the editor: UI automation (some batch tools via menus)."),
    _app("adobe-acrobat", "Adobe Acrobat", OFFICE, "com", ["Acrobat.exe"], "AcroExch.App",
         "COM/IAC (AcroExch): open docs, page ops; JavaScript API inside the app."),

    # --------------------------------------------------------- Dev Tools (18)
    _app("vscode", "Visual Studio Code", DEV, "cli", ["Code.exe"], None,
         "CLI (code --command), workspace files, extensions API; settings are JSON."),
    _app("visual-studio", "Visual Studio", DEV, "com", ["devenv.exe"], "VisualStudio.DTE",
         "EnvDTE COM automation + devenv CLI switches for builds (/Build, /Deploy)."),
    _app("pycharm", "PyCharm", DEV, "ui", ["pycharm64.exe"], None,
         "JetBrains IDE: UI automation; headless via built-in inspections/format CLI modes."),
    _app("intellij-idea", "IntelliJ IDEA", DEV, "ui", ["idea64.exe"], None,
         "JetBrains IDE: UI automation; some headless commands (format, inspections)."),
    _app("webstorm", "WebStorm", DEV, "ui", ["webstorm64.exe"], None,
         "JetBrains IDE: UI automation; project files are XML on disk."),
    _app("android-studio", "Android Studio", DEV, "ui", ["studio64.exe"], None,
         "UI automation for the IDE; real work via Gradle/adb CLI alongside."),
    _app("sublime-text", "Sublime Text", DEV, "plugin", ["sublime_text.exe"], None,
         "Python plugin API (Packages folder) + CLI flags for opening files."),
    _app("notepad-plus-plus", "Notepad++", DEV, "ui", ["notepad++.exe"], None,
         "Plugin/macros in-app; CLI flags for opening files — mostly UI tier."),
    _app("eclipse", "Eclipse", DEV, "ui", ["eclipse.exe"], None,
         "UI automation; headless builds via workspace/ant/maven alongside."),
    _app("git", "Git", DEV, "cli", ["git.exe"], None,
         "Pure CLI — the agent just runs it; no GUI needed."),
    _app("docker-desktop", "Docker Desktop", DEV, "cli", ["Docker Desktop.exe"], None,
         "Drive it through the docker CLI; the GUI itself is UI-tier."),
    _app("postman", "Postman", DEV, "ui", ["Postman.exe"], None,
         "GUI is UI-tier; collections run headless via newman CLI."),
    _app("insomnia", "Insomnia", DEV, "ui", ["Insomnia.exe"], None,
         "GUI is UI-tier; inso CLI runs collections/documents headless."),
    _app("windows-terminal", "Windows Terminal", DEV, "cli", ["wt.exe"], None,
         "CLI args open tabs/panes/profiles; settings are JSON."),
    _app("powershell", "PowerShell", DEV, "cli", ["pwsh.exe", "powershell.exe"], None,
         "Pure CLI: the agent runs scripts with -File / -Command."),
    _app("wsl", "WSL", DEV, "cli", ["wsl.exe"], None,
         "CLI: wsl -d <distro> -- command runs Linux shells from Windows."),
    _app("sourcetree", "SourceTree", DEV, "ui", ["SourceTree.exe"], None,
         "Git GUI with no automation API: UI tier; prefer git CLI for real work."),
    _app("datagrip", "DataGrip", DEV, "ui", ["datagrip64.exe"], None,
         "JetBrains IDE: UI automation; data sources are XML config on disk."),

    # ------------------------------------------------------------ Browsers (6)
    _app("chrome", "Google Chrome", BROWSERS, "cli", ["chrome.exe"], None,
         "Chrome DevTools Protocol: launch with --remote-debugging-port and drive via websocket."),
    _app("edge", "Microsoft Edge", BROWSERS, "cli", ["msedge.exe"], None,
         "Chromium-based: same CDP remote-debugging approach as Chrome."),
    _app("firefox", "Mozilla Firefox", BROWSERS, "cli", ["firefox.exe"], None,
         "Marionette / Remote Agent (--remote-debugging-port gives CDP subset); profiles on disk."),
    _app("brave", "Brave", BROWSERS, "cli", ["brave.exe"], None,
         "Chromium-based: CDP remote debugging like Chrome."),
    _app("opera", "Opera", BROWSERS, "cli", ["opera.exe"], None,
         "Chromium-based: CDP remote debugging like Chrome."),
    _app("coc-coc", "Cốc Cốc", BROWSERS, "cli", ["CocCoc.exe", "browser.exe"], None,
         "Chromium-based (Vietnam): CDP remote debugging like Chrome."),

    # ------------------------------------------------------- Communication (10)
    _app("zalo", "Zalo", COMMS, "ui", ["Zalo.exe"], None,
         "No official desktop API: UI automation only (this is why ChannelHub uses unofficial libs)."),
    _app("telegram", "Telegram Desktop", COMMS, "ui", ["Telegram.exe"], None,
         "Desktop GUI is UI-tier; real automation goes through the Bot API / MTProto cloud-side."),
    _app("discord", "Discord", COMMS, "ui", ["Discord.exe"], None,
         "GUI is UI-tier; bots use the cloud API, not the desktop app."),
    _app("slack", "Slack", COMMS, "ui", ["slack.exe"], None,
         "GUI is UI-tier; automation via Slack Web API cloud-side."),
    _app("teams", "Microsoft Teams", COMMS, "ui", ["ms-teams.exe", "Teams.exe"], None,
         "GUI is UI-tier; automation via Microsoft Graph cloud-side."),
    _app("zoom", "Zoom", COMMS, "cli", ["Zoom.exe"], None,
         "Join/start via zoommtg:// URL scheme and CLI-ish links; in-meeting control is UI-tier."),
    _app("skype", "Skype", COMMS, "ui", ["Skype.exe"], None,
         "No automation API: UI automation only."),
    _app("messenger", "Messenger", COMMS, "ui", ["Messenger.exe"], None,
         "Desktop app is a web wrapper: UI automation only."),
    _app("whatsapp", "WhatsApp", COMMS, "ui", ["WhatsApp.exe"], None,
         "Desktop app is a web wrapper: UI automation only; Business API is cloud-side."),
    _app("viber", "Viber", COMMS, "ui", ["Viber.exe"], None,
         "No automation API: UI automation only."),

    # ------------------------------------------------- Utilities & Media (10)
    _app("7zip", "7-Zip", UTILS, "cli", ["7z.exe", "7zFM.exe"], None,
         "Fully scriptable via 7z.exe CLI (the GUI is just a shell over it)."),
    _app("winrar", "WinRAR", UTILS, "cli", ["WinRAR.exe", "Rar.exe"], None,
         "Scriptable via Rar.exe / WinRAR.exe command-line switches."),
    _app("everything", "Everything (Voidtools)", UTILS, "cli", ["Everything.exe"], None,
         "Instant file search: es.exe CLI and an optional built-in HTTP server."),
    _app("powertoys", "PowerToys", UTILS, "ui", ["PowerToys.exe"], None,
         "Settings are JSON under %LOCALAPPDATA%\\Microsoft\\PowerToys; GUI itself is UI-tier."),
    _app("spotify", "Spotify", UTILS, "ui", ["Spotify.exe"], None,
         "GUI is UI-tier; playback automation via Spotify Web API cloud-side."),
    _app("itunes", "iTunes", UTILS, "com", ["iTunes.exe"], "iTunes.Application",
         "Legacy but real COM API: library, playlists, playback control."),
    _app("potplayer", "PotPlayer", UTILS, "cli", ["PotPlayerMini64.exe", "PotPlayer.exe"], None,
         "Command-line switches for playback/seeking; deeper control is UI-tier."),
    _app("kmplayer", "KMPlayer", UTILS, "ui", ["KMPlayer64.exe", "KMPlayer.exe"], None,
         "No automation API: UI automation; basic CLI file-open only."),
    _app("ccleaner", "CCleaner", UTILS, "ui", ["CCleaner64.exe", "CCleaner.exe"], None,
         "Has silent /AUTO switches for cleaning runs; GUI config is UI-tier."),
    _app("teamviewer", "TeamViewer", UTILS, "cli", ["TeamViewer.exe"], None,
         "CLI commands to start/stop service and show ID; session control is UI-tier."),
]

_BY_ID: dict[str, dict] = {a["id"]: a for a in APPS}
CATEGORIES: list[str] = list(dict.fromkeys(a["category"] for a in APPS))


def _norm(app_id: str) -> str:
    return (app_id or "").strip().lower()


# ------------------------------------------------------------------ queries

def list_apps(category: str = "") -> dict:
    """List catalog apps, optionally filtered by exact category name."""
    try:
        cat = (category or "").strip().lower()
        apps = APPS if not cat else [a for a in APPS if a["category"].lower() == cat]
        if cat and not apps:
            return err(f"Unknown category: {category!r}. Categories: {', '.join(CATEGORIES)}")
        return ok(apps=[dict(a) for a in apps], count=len(apps), categories=CATEGORIES)
    except Exception as exc:  # pragma: no cover - defensive
        return err(exc)


def get_app(app_id: str) -> dict:
    """Return the full catalog record for one app id."""
    app = _BY_ID.get(_norm(app_id))
    if app is None:
        return err(f"Unknown app id: {app_id!r}. Use apps_list to browse all {len(APPS)} ids.")
    return ok(app=dict(app))


# --------------------------------------------------------------- detection

def _probe(exe_names, which_fn) -> str | None:
    """Return the first exe path ``which_fn`` finds, else None. Pure helper."""
    for name in exe_names or []:
        try:
            found = which_fn(name)
        except Exception:
            found = None
        if found:
            return str(found)
    return None


def _program_roots() -> list[Path]:
    roots: list[Path] = []
    for env_key in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        val = os.environ.get(env_key)
        if val:
            roots.append(Path(val))
    if not roots:  # minimal fallback so the helper still returns guesses
        roots = [Path("C:/Program Files"), Path("C:/Program Files (x86)")]
    return roots


def _common_paths(app: dict) -> list[Path]:
    """Guess install locations for *app* under the usual Windows roots.

    Pure helper (no disk access): for each exe name, candidates are
    ``<root>/<App Name>/<exe>``, ``<root>/<exe stem>/<exe>`` and
    ``<root>/Programs/<App Name>/<exe>`` (the %LOCALAPPDATA% layout),
    plus the bare ``<root>/<exe>``.
    """
    candidates: list[Path] = []
    seen: set[str] = set()
    name_folder = app.get("name", "")
    for exe in app.get("exe_names") or []:
        stem = Path(exe).stem
        for root in _program_roots():
            for folder in dict.fromkeys([name_folder, stem, f"Programs/{name_folder}"]):
                cand = root / folder / exe
                key = str(cand).lower()
                if key not in seen:
                    seen.add(key)
                    candidates.append(cand)
            bare = root / exe
            if str(bare).lower() not in seen:
                seen.add(str(bare).lower())
                candidates.append(bare)
    return candidates


def _detect_one(app: dict, which_fn) -> dict:
    """Plain detection record for one catalog entry (no ok/err wrapper)."""
    path = _probe(app["exe_names"], which_fn)
    via = "path" if path else None
    if not path and IS_WINDOWS:
        for cand in _common_paths(app):
            if cand.exists():
                path, via = str(cand), "common_dir"
                break
    return {
        "id": app["id"],
        "name": app["name"],
        "category": app["category"],
        "strategy": app["strategy"],
        "prog_id": app["prog_id"],
        "installed": bool(path),
        "path": path,
        "via": via,
    }


def detect_app(app_id: str, which_fn=shutil.which) -> dict:
    """Detect whether one catalog app is installed on this machine."""
    app = _BY_ID.get(_norm(app_id))
    if app is None:
        return err(f"Unknown app id: {app_id!r}. Use apps_list to browse all {len(APPS)} ids.")
    try:
        return ok(**_detect_one(app, which_fn))
    except Exception as exc:
        return err(exc)


def detect_apps(category: str = "", which_fn=shutil.which) -> dict:
    """Detect installed state for every catalog app (or one category)."""
    try:
        cat = (category or "").strip().lower()
        apps = APPS if not cat else [a for a in APPS if a["category"].lower() == cat]
        if cat and not apps:
            return err(f"Unknown category: {category!r}. Categories: {', '.join(CATEGORIES)}")
        results = [_detect_one(a, which_fn) for a in apps]
        installed = sum(1 for r in results if r["installed"])
        return ok(apps=results, count=len(results), installed_count=installed)
    except Exception as exc:
        return err(exc)


# ------------------------------------------------------------------ launch

def _spawn(path: str):
    """Start *path* as a detached process. Factored out for testability."""
    return subprocess.Popen([str(path)], close_fds=True)


def launch_app_from_catalog(app_id: str, which_fn=shutil.which) -> dict:
    """Launch an installed catalog app by id. Audited."""
    det = detect_app(app_id, which_fn=which_fn)
    if not det.get("ok"):
        return det
    if not det["installed"]:
        return err(f"{det['name']} is not installed (none of its executables were found on this machine).")
    try:
        proc = _spawn(det["path"])
        pid = getattr(proc, "pid", None)
        audit("apps_launch", {"app_id": det["id"], "name": det["name"],
                              "path": det["path"], "pid": pid})
        return ok(app_id=det["id"], name=det["name"], launched=True,
                  path=det["path"], pid=pid)
    except Exception as exc:
        return err(exc)


# ---------------------------------------------------------------- register

def register(mcp):
    """Register the apps_* tools on a FastMCP server. Returns the catalog."""

    @mcp.tool()
    def apps_list(category: str = "") -> dict:
        """List the 100 desktop apps in the AppMesh catalog.

        Optionally filter by category (exact name, e.g. "CAD & 3D",
        "Video & Audio", "Design & Photo", "Office & Productivity",
        "Dev Tools", "Browsers", "Communication", "Utilities & Media").
        Each entry gives the app's automation strategy (com / plugin /
        cli / file / ui), executable names to probe and its COM ProgID.
        """
        return list_apps(category)

    @mcp.tool()
    def apps_get(app_id: str) -> dict:
        """Get the full catalog record for one app by its slug id
        (e.g. "autocad", "capcut", "excel"): name, category, automation
        strategy, executable names, COM ProgID and how an agent can
        automate it."""
        return get_app(app_id)

    @mcp.tool()
    def apps_detect(category: str = "") -> dict:
        """Detect which catalog apps are actually installed on this
        machine (PATH lookup, plus common Windows install folders).
        Optionally restrict to one category. Returns per-app
        installed/path info and an installed_count."""
        return detect_apps(category)

    @mcp.tool()
    def apps_detect_one(app_id: str) -> dict:
        """Detect whether a single catalog app (by slug id) is
        installed, and where its executable lives."""
        return detect_app(app_id)

    @mcp.tool()
    def apps_launch(app_id: str) -> dict:
        """Launch an installed catalog app by its slug id
        (e.g. "autocad"). Fails with a clear error when the app is
        not installed. The launch is written to the audit log."""
        return launch_app_from_catalog(app_id)

    return APPS
