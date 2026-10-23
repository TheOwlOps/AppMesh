"""CapCut adapter — edit projects through their draft files.

CapCut desktop has NO official API. Every project is a folder under the
CapCut drafts root containing ``draft_content.json`` (older / some builds:
``draft_info.json``). This adapter reads and edits that JSON directly:

- listing projects, summarising duration / tracks / materials,
- reading on-screen texts (captions) with their timeline positions,
- exporting captions as SRT,
- find-and-replace on caption text (with a timestamped backup first).

IMPORTANT: CapCut must be CLOSED while a draft is edited. CapCut keeps the
draft in memory and will overwrite on-disk changes when it saves or exits.

Newer CapCut versions may encrypt/obfuscate the draft JSON; when parsing
fails this adapter reports that clearly instead of guessing.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

from ..core import audit, check_path_allowed, err, ok

DRAFT_FILENAMES = ("draft_content.json", "draft_info.json")
MATERIAL_KINDS = ("videos", "audios", "texts", "images", "stickers", "effects")


def us_to_srt_time(us: int | float) -> str:
    """Convert microseconds to an SRT timestamp ``HH:MM:SS,mmm``.

    Pure helper. Negative values clamp to zero; sub-millisecond remainders
    are truncated.
    """
    total_ms = max(0, int(us)) // 1000
    ms = total_ms % 1000
    total_s = total_ms // 1000
    s = total_s % 60
    total_m = total_s // 60
    m = total_m % 60
    h = total_m // 60
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _default_projects_root() -> Path:
    """Resolve the CapCut drafts root when none is given explicitly."""
    env = os.environ.get("CAPCUT_PROJECTS_DIR")
    if env:
        return Path(env).expanduser()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft"
    if sys.platform.startswith("win"):
        return (Path.home() / "AppData" / "Local" / "CapCut" / "User Data"
                / "Projects" / "com.lveditor.draft")
    if sys.platform == "darwin":
        return Path.home() / "Movies" / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft"
    return Path.home() / ".capcut-drafts"


def _parse_text_content(content) -> tuple[str, object]:
    """Extract the visible text from a ``materials.texts[].content`` value.

    Real drafts store ``content`` as a *stringified* JSON object like
    ``{"text": "hello", "styles": [...]}``. Be defensive: the value may
    already be a dict, a plain string, or JSON without a ``text`` key.

    Returns ``(text, parsed)`` where ``parsed`` is the decoded dict when the
    content was structured, else ``None``.
    """
    if isinstance(content, dict):
        return str(content.get("text", "")), content
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
        except (ValueError, TypeError):
            return content, None
        if isinstance(parsed, dict):
            return str(parsed.get("text", "")), parsed
        if isinstance(parsed, str):
            return parsed, None
        return content, None
    return "", None


class CapCutAdapter:
    """Read/edit CapCut draft projects under one drafts root."""

    def __init__(self, projects_root: str | Path | None = None):
        if projects_root is not None:
            self.projects_root = Path(projects_root).expanduser()
        else:
            self.projects_root = _default_projects_root()

    # -- internal helpers (raise ValueError with readable messages) ----------

    @staticmethod
    def _draft_file(folder: Path) -> Path | None:
        for name in DRAFT_FILENAMES:
            candidate = folder / name
            if candidate.is_file():
                return candidate
        return None

    def resolve_project(self, name_or_path: str | Path) -> Path:
        """Resolve a project folder name (inside the root) or absolute path."""
        raw = Path(str(name_or_path)).expanduser()
        if raw.is_absolute():
            # Absolute paths must be allowlisted — unless they live inside
            # the explicitly configured drafts root, which is trusted.
            try:
                resolved = raw.resolve()
                if not (resolved == self.projects_root.resolve()
                        or self.projects_root.resolve() in resolved.parents):
                    resolved = check_path_allowed(resolved)
            except ValueError:
                raise
            except Exception as exc:  # pragma: no cover - defensive
                raise ValueError(str(exc)) from exc
            folder = resolved
            if folder.is_file():  # caller pointed straight at the draft file
                folder = folder.parent
        else:
            folder = (self.projects_root / raw).resolve()
        if not folder.is_dir():
            raise ValueError(
                f"CapCut project not found: {name_or_path!r} "
                f"(looked for a folder at {folder}; drafts root is {self.projects_root})")
        if self._draft_file(folder) is None:
            raise ValueError(
                f"No draft file in {folder}: expected one of {', '.join(DRAFT_FILENAMES)}. "
                "Open the project in CapCut once so it writes its draft, then close CapCut.")
        return folder

    def _load(self, project: str | Path) -> tuple[Path, Path, dict]:
        """Return (project_folder, draft_file, parsed_draft) or raise ValueError."""
        folder = self.resolve_project(project)
        draft_file = self._draft_file(folder)
        assert draft_file is not None  # guaranteed by resolve_project
        try:
            data = json.loads(draft_file.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError(
                f"Could not parse draft file {draft_file}: {exc}. "
                "Newer CapCut versions may encrypt/obfuscate draft_content.json; "
                "this project cannot be edited as plain JSON.") from exc
        if not isinstance(data, dict):
            raise ValueError(f"Draft file {draft_file} did not contain a JSON object.")
        return folder, draft_file, data

    @staticmethod
    def _text_entries(data: dict) -> list[dict]:
        """Join text-track segments to text materials -> timed text entries (µs)."""
        materials = data.get("materials") or {}
        texts_by_id = {}
        for entry in materials.get("texts") or []:
            if isinstance(entry, dict) and entry.get("id"):
                text, _ = _parse_text_content(entry.get("content", ""))
                texts_by_id[entry["id"]] = text
        entries: list[dict] = []
        for track in data.get("tracks") or []:
            if not isinstance(track, dict) or track.get("type") != "text":
                continue
            for segment in track.get("segments") or []:
                if not isinstance(segment, dict):
                    continue
                material_id = segment.get("material_id")
                if material_id not in texts_by_id:
                    continue
                timerange = segment.get("target_timerange") or {}
                entries.append({
                    "material_id": material_id,
                    "text": texts_by_id[material_id],
                    "start_us": int(timerange.get("start", 0) or 0),
                    "duration_us": int(timerange.get("duration", 0) or 0),
                })
        entries.sort(key=lambda e: e["start_us"])
        return entries

    # -- public API (always returns dicts, never raises) ---------------------

    def list_projects(self) -> dict:
        """List project folders under the drafts root that contain a draft file."""
        try:
            projects = []
            if self.projects_root.is_dir():
                for folder in sorted(self.projects_root.iterdir()):
                    if not folder.is_dir():
                        continue
                    draft_file = self._draft_file(folder)
                    if draft_file is None:
                        continue
                    mtime = draft_file.stat().st_mtime
                    projects.append({
                        "name": folder.name,
                        "path": str(folder),
                        "modified_iso": datetime.fromtimestamp(mtime).isoformat(),
                        "draft_file": str(draft_file),
                    })
            projects.sort(key=lambda p: p["modified_iso"], reverse=True)
            return ok(projects=projects, count=len(projects),
                      projects_root=str(self.projects_root))
        except Exception as exc:
            return err(exc)

    def load_draft(self, project: str) -> dict:
        """Load and parse a project's draft JSON."""
        try:
            folder, draft_file, data = self._load(project)
            return ok(project=folder.name, path=str(folder),
                      draft_file=str(draft_file), draft=data)
        except Exception as exc:
            return err(exc)

    def summary(self, project: str) -> dict:
        """Summarise a project: duration, track counts, material counts."""
        try:
            folder, draft_file, data = self._load(project)
            track_counts: dict[str, int] = {}
            for track in data.get("tracks") or []:
                if isinstance(track, dict):
                    ttype = str(track.get("type", "unknown"))
                    track_counts[ttype] = track_counts.get(ttype, 0) + 1
            materials = data.get("materials") or {}
            material_counts = {
                kind: len(entries) for kind, entries in materials.items()
                if isinstance(entries, list)
            }
            duration_us = int(data.get("duration", 0) or 0)
            return ok(
                name=folder.name,
                path=str(folder),
                draft_file=str(draft_file),
                duration_us=duration_us,
                duration_s=duration_us / 1_000_000,
                track_counts=track_counts,
                material_counts=material_counts,
                text_count=len(materials.get("texts") or []),
            )
        except Exception as exc:
            return err(exc)

    def get_texts(self, project: str) -> dict:
        """All caption/text entries with timeline positions, sorted by start."""
        try:
            folder, _, data = self._load(project)
            texts = [{
                "material_id": e["material_id"],
                "text": e["text"],
                "start_s": e["start_us"] / 1_000_000,
                "duration_s": e["duration_us"] / 1_000_000,
            } for e in self._text_entries(data)]
            return ok(project=folder.name, texts=texts, count=len(texts))
        except Exception as exc:
            return err(exc)

    def export_srt(self, project: str) -> dict:
        """Export the project's text track as an SRT subtitle string."""
        try:
            folder, _, data = self._load(project)
            entries = self._text_entries(data)
            blocks = []
            for idx, e in enumerate(entries, start=1):
                start = us_to_srt_time(e["start_us"])
                end = us_to_srt_time(e["start_us"] + e["duration_us"])
                blocks.append(f"{idx}\n{start} --> {end}\n{e['text']}")
            srt = "\n\n".join(blocks)
            if srt:
                srt += "\n"
            return ok(project=folder.name, srt=srt, count=len(entries))
        except Exception as exc:
            return err(exc)

    def replace_text(self, project: str, old: str, new: str) -> dict:
        """Find-and-replace inside caption texts. Backs up the draft first.

        CapCut must be closed while this runs — it overwrites draft files
        on save/exit. The draft is copied to
        ``<draft>.bak-YYYYmmddHHMMSS`` before any write, and the change is
        written to the audit log.
        """
        try:
            if not old:
                return err("`old` must be a non-empty string.")
            folder, draft_file, data = self._load(project)
            # Writes must be allowlisted — a draft inside the configured
            # drafts root is already trusted (resolve_project enforced it).
            root_resolved = self.projects_root.resolve()
            if not (draft_file.resolve() == root_resolved
                    or root_resolved in draft_file.resolve().parents):
                check_path_allowed(draft_file)
            materials = data.get("materials") or {}
            replacements = 0
            for entry in materials.get("texts") or []:
                if not isinstance(entry, dict) or "content" not in entry:
                    continue
                content = entry["content"]
                text, parsed = _parse_text_content(content)
                if old not in text:
                    continue
                occurrences = text.count(old)
                replaced = text.replace(old, new)
                if isinstance(content, dict):
                    updated = dict(content)
                    updated["text"] = replaced
                    entry["content"] = updated
                elif parsed is not None:
                    updated = dict(parsed)
                    updated["text"] = replaced
                    entry["content"] = json.dumps(updated, ensure_ascii=False)
                else:  # plain-string content
                    entry["content"] = replaced
                replacements += occurrences
            if replacements == 0:
                return ok(project=folder.name, replacements=0,
                          draft_file=str(draft_file),
                          note=f"No text containing {old!r} was found; draft left unchanged.")
            stamp = datetime.now().strftime("%Y%m%d%H%M%S")
            backup = draft_file.with_name(f"{draft_file.name}.bak-{stamp}")
            shutil.copy2(draft_file, backup)
            draft_file.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            audit("capcut_replace_text", {
                "project": folder.name,
                "draft_file": str(draft_file),
                "old": old,
                "new": new,
                "replacements": replacements,
                "backup": str(backup),
            })
            return ok(project=folder.name, replacements=replacements,
                      draft_file=str(draft_file), backup=str(backup))
        except Exception as exc:
            return err(exc)

    def list_materials(self, project: str, kind: str = "videos") -> dict:
        """List materials of one kind (videos/audios/texts/...) with their paths."""
        try:
            folder, _, data = self._load(project)
            materials = data.get("materials") or {}
            if kind not in materials:
                return err(f"Unknown material kind {kind!r}. "
                           f"Available: {', '.join(sorted(materials)) or '(none)'}")
            items = [{
                "id": entry.get("id"),
                "path": entry.get("path", ""),
            } for entry in materials.get(kind) or [] if isinstance(entry, dict)]
            return ok(project=folder.name, kind=kind, materials=items, count=len(items))
        except Exception as exc:
            return err(exc)


def register(mcp) -> CapCutAdapter:
    """Register the capcut_* tools on a FastMCP server. Returns the adapter."""
    adapter = CapCutAdapter()

    @mcp.tool()
    def capcut_list_projects() -> dict:
        """List CapCut projects found in the local drafts folder.

        Each entry has the project name, folder path, last-modified time
        and the draft file that would be edited.
        """
        return adapter.list_projects()

    @mcp.tool()
    def capcut_summary(project: str) -> dict:
        """Summarise a CapCut project: duration (s), track counts by type,
        material counts by kind and how many text/caption materials it has.

        `project` is a project folder name from capcut_list_projects, or an
        absolute path to the project folder.
        """
        return adapter.summary(project)

    @mcp.tool()
    def capcut_get_texts(project: str) -> dict:
        """Get all texts/captions of a CapCut project with timeline positions
        (start_s, duration_s in seconds), sorted by start time."""
        return adapter.get_texts(project)

    @mcp.tool()
    def capcut_export_srt(project: str) -> dict:
        """Export a CapCut project's captions as an SRT subtitle string."""
        return adapter.export_srt(project)

    @mcp.tool()
    def capcut_replace_text(project: str, old: str, new: str) -> dict:
        """Replace text inside a CapCut project's captions (find & replace).

        IMPORTANT: CapCut must be closed while editing — it overwrites the
        draft file on save/exit. A timestamped backup
        (draft_content.json.bak-YYYYmmddHHMMSS) is created before writing,
        and the change is audit-logged. Returns the replacement count.
        """
        return adapter.replace_text(project, old, new)

    @mcp.tool()
    def capcut_list_materials(project: str, kind: str = "videos") -> dict:
        """List a CapCut project's materials of one kind
        (videos, audios, texts, images, ...) with their ids and file paths."""
        return adapter.list_materials(project, kind)

    return adapter
