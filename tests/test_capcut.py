"""Hermetic tests for the CapCut adapter — synthetic draft files only."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from desktop_mcp.adapters.capcut import CapCutAdapter, us_to_srt_time

PROJECT_NAME = "My Project"


def _draft() -> dict:
    return {
        "duration": 5_000_000,
        "canvas_config": {"width": 1920, "height": 1080},
        "tracks": [
            {
                "id": "track-video",
                "type": "video",
                "segments": [
                    {
                        "id": "seg-video-1",
                        "material_id": "vid-1",
                        "target_timerange": {"start": 0, "duration": 5_000_000},
                        "source_timerange": {"start": 0, "duration": 5_000_000},
                    }
                ],
            },
            {
                "id": "track-text",
                "type": "text",
                "segments": [
                    {
                        "id": "seg-text-1",
                        "material_id": "txt-1",
                        "target_timerange": {"start": 500_000, "duration": 1_000_000},
                        "source_timerange": {"start": 0, "duration": 1_000_000},
                    },
                    {
                        "id": "seg-text-2",
                        "material_id": "txt-2",
                        "target_timerange": {"start": 2_000_000, "duration": 1_500_000},
                        "source_timerange": {"start": 0, "duration": 1_500_000},
                    },
                ],
            },
        ],
        "materials": {
            "videos": [
                {"id": "vid-1", "path": "C:\\Videos\\clip.mp4", "duration": 5_000_000},
            ],
            "audios": [],
            "texts": [
                {
                    "id": "txt-1",
                    "content": json.dumps({"text": "Hello world", "styles": []}),
                },
                {
                    "id": "txt-2",
                    "content": json.dumps({"text": "Second caption", "styles": []}),
                },
            ],
        },
    }


@pytest.fixture()
def projects_root(tmp_path: Path) -> Path:
    root = tmp_path / "com.lveditor.draft"
    project_dir = root / PROJECT_NAME
    project_dir.mkdir(parents=True)
    (project_dir / "draft_content.json").write_text(
        json.dumps(_draft()), encoding="utf-8")
    # A folder without a draft file must be ignored by list_projects.
    (root / "not-a-project").mkdir()
    return root


@pytest.fixture()
def adapter(projects_root: Path) -> CapCutAdapter:
    return CapCutAdapter(projects_root=projects_root)


def test_list_projects_finds_project(adapter: CapCutAdapter):
    result = adapter.list_projects()
    assert result["ok"] is True
    assert result["count"] == 1
    project = result["projects"][0]
    assert project["name"] == PROJECT_NAME
    assert project["path"].endswith(PROJECT_NAME)
    assert project["draft_file"].endswith("draft_content.json")
    assert project["modified_iso"]


def test_summary_counts(adapter: CapCutAdapter):
    result = adapter.summary(PROJECT_NAME)
    assert result["ok"] is True
    assert result["name"] == PROJECT_NAME
    assert result["duration_s"] == pytest.approx(5.0)
    assert result["track_counts"] == {"video": 1, "text": 1}
    assert result["material_counts"]["videos"] == 1
    assert result["material_counts"]["texts"] == 2
    assert result["text_count"] == 2


def test_get_texts_with_timings(adapter: CapCutAdapter):
    result = adapter.get_texts(PROJECT_NAME)
    assert result["ok"] is True
    assert result["count"] == 2
    first, second = result["texts"]
    assert first == {
        "material_id": "txt-1",
        "text": "Hello world",
        "start_s": pytest.approx(0.5),
        "duration_s": pytest.approx(1.0),
    }
    assert second["material_id"] == "txt-2"
    assert second["text"] == "Second caption"
    assert second["start_s"] == pytest.approx(2.0)
    assert second["duration_s"] == pytest.approx(1.5)


def test_export_srt_exact_format(adapter: CapCutAdapter):
    result = adapter.export_srt(PROJECT_NAME)
    assert result["ok"] is True
    expected = (
        "1\n"
        "00:00:00,500 --> 00:00:01,500\n"
        "Hello world\n"
        "\n"
        "2\n"
        "00:00:02,000 --> 00:00:03,500\n"
        "Second caption\n"
    )
    assert result["srt"] == expected


@pytest.mark.parametrize("us,expected", [
    (0, "00:00:00,000"),
    (1_500_000, "00:00:01,500"),
    (3_661_500_000, "01:01:01,500"),
    (59_999_000, "00:00:59,999"),
    (3_600_000_000, "01:00:00,000"),
])
def test_us_to_srt_time(us, expected):
    assert us_to_srt_time(us) == expected


def test_replace_text_backs_up_and_preserves_json(
    adapter: CapCutAdapter, projects_root: Path, monkeypatch, tmp_path: Path
):
    monkeypatch.setenv("DESKTOP_MCP_HOME", str(tmp_path / "mcp-home"))
    result = adapter.replace_text(PROJECT_NAME, "Hello", "Xin chào")
    assert result["ok"] is True
    assert result["replacements"] == 1

    # A timestamped backup of the original draft exists and holds old text.
    backups = list((projects_root / PROJECT_NAME).glob("draft_content.json.bak-*"))
    assert len(backups) == 1
    assert result["backup"] == str(backups[0])
    backup_data = json.loads(backups[0].read_text(encoding="utf-8"))
    old_content = json.loads(backup_data["materials"]["texts"][0]["content"])
    assert old_content["text"] == "Hello world"

    # The rewritten draft is still valid JSON and content stayed stringified.
    draft_path = projects_root / PROJECT_NAME / "draft_content.json"
    data = json.loads(draft_path.read_text(encoding="utf-8"))
    content = data["materials"]["texts"][0]["content"]
    assert isinstance(content, str)
    assert json.loads(content)["text"] == "Xin chào world"
    # Untouched text is unchanged.
    assert json.loads(data["materials"]["texts"][1]["content"])["text"] == "Second caption"

    # The audit log recorded the mutation.
    audit_file = tmp_path / "mcp-home" / "audit.jsonl"
    assert audit_file.is_file()
    record = json.loads(audit_file.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert record["tool"] == "capcut_replace_text"
    assert record["replacements"] == 1


def test_replace_text_no_match_leaves_draft_untouched(
    adapter: CapCutAdapter, projects_root: Path
):
    result = adapter.replace_text(PROJECT_NAME, "not present", "whatever")
    assert result["ok"] is True
    assert result["replacements"] == 0
    assert not list((projects_root / PROJECT_NAME).glob("*.bak-*"))


def test_list_materials(adapter: CapCutAdapter):
    result = adapter.list_materials(PROJECT_NAME)
    assert result["ok"] is True
    assert result["materials"] == [{"id": "vid-1", "path": "C:\\Videos\\clip.mp4"}]
    bad = adapter.list_materials(PROJECT_NAME, kind="bogus-kind")
    assert bad["ok"] is False


def test_missing_project_returns_err(adapter: CapCutAdapter):
    for result in (
        adapter.summary("Does Not Exist"),
        adapter.get_texts("Does Not Exist"),
        adapter.export_srt("Does Not Exist"),
        adapter.load_draft("Does Not Exist"),
    ):
        assert result["ok"] is False
        assert "not found" in result["error"]


def test_unparseable_draft_returns_clear_err(tmp_path: Path):
    root = tmp_path / "drafts"
    broken = root / "Broken Project"
    broken.mkdir(parents=True)
    (broken / "draft_content.json").write_text("{not json at all", encoding="utf-8")
    result = CapCutAdapter(projects_root=root).load_draft("Broken Project")
    assert result["ok"] is False
    assert "parse" in result["error"]
    assert "obfuscate" in result["error"]


def test_draft_info_json_fallback(tmp_path: Path):
    root = tmp_path / "drafts"
    project_dir = root / "Old Build Project"
    project_dir.mkdir(parents=True)
    (project_dir / "draft_info.json").write_text(json.dumps(_draft()), encoding="utf-8")
    adapter = CapCutAdapter(projects_root=root)
    listed = adapter.list_projects()
    assert listed["count"] == 1
    assert listed["projects"][0]["draft_file"].endswith("draft_info.json")
    assert adapter.summary("Old Build Project")["text_count"] == 2
