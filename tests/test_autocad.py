"""Hermetic tests for the AutoCAD adapter — no real AutoCAD, no pyautocad.

A FakeAcad mimics exactly the pyautocad surface the adapter uses:
``app.Documents.Open/Add``, ``model.AddLine/AddCircle/AddText``,
``iter_objects``, ``doc.Layers`` (Add + iteration), ``doc.Save``,
``doc.SendCommand`` and ``doc.Name``. It is injected via ``acad_factory``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from desktop_mcp import core
from desktop_mcp.adapters.autocad import AutoCADAdapter


# ---------------------------------------------------------------- fakes

class FakeEntity:
    def __init__(self, object_name, handle, layer="0"):
        self.ObjectName = object_name
        self.Handle = handle
        self.Layer = layer


class FakeModel:
    """Model space: records every Add* call and returns a FakeEntity."""

    def __init__(self):
        self.calls = []  # list of (method, args tuple)

    def AddLine(self, start, end):
        self.calls.append(("AddLine", (start, end)))
        return FakeEntity("Line", "L1")

    def AddCircle(self, center, radius):
        self.calls.append(("AddCircle", (center, radius)))
        return FakeEntity("Circle", "C1")

    def AddText(self, text, insertion_point, height):
        self.calls.append(("AddText", (text, insertion_point, height)))
        return FakeEntity("Text", "T1")


class FakeLayer:
    def __init__(self, name):
        self.Name = name


class FakeLayers:
    def __init__(self, names=("0",)):
        self._layers = [FakeLayer(n) for n in names]
        self.added = []

    def Add(self, name):
        layer = FakeLayer(name)
        self._layers.append(layer)
        self.added.append(name)
        return layer

    def __iter__(self):
        return iter(list(self._layers))


class FakeDoc:
    def __init__(self, name="drawing1.dwg"):
        self.Name = name
        self.Layers = FakeLayers()
        self.saved = False
        self.sent_commands = []

    def Save(self):
        self.saved = True

    def SendCommand(self, command):
        self.sent_commands.append(command)


class FakeDocuments:
    def __init__(self):
        self.opened = []
        self.added_count = 0

    def Open(self, path):
        self.opened.append(path)
        return FakeDoc(name=Path(path).name)

    def Add(self):
        self.added_count += 1
        return FakeDoc(name=f"drawing{self.added_count}.dwg")


class FakeApp:
    def __init__(self):
        self.Documents = FakeDocuments()


class FakeAcad:
    """The pyautocad-shaped object the factory hands to the adapter."""

    def __init__(self):
        self.app = FakeApp()
        self.model = FakeModel()
        self._doc = FakeDoc()
        self.entities = [
            FakeEntity("Line", "10", layer="Walls"),
            FakeEntity("Line", "11", layer="Walls"),
            FakeEntity("Circle", "12", layer="Doors"),
            FakeEntity("Text", "13", layer="Notes"),
        ]

    @property
    def doc(self):
        return self._doc

    @property
    def docname(self):
        return self._doc.Name

    def iter_objects(self, type_filter=None):
        for entity in self.entities:
            if type_filter and entity.ObjectName != type_filter:
                continue
            yield entity


@pytest.fixture()
def fake():
    return FakeAcad()


@pytest.fixture()
def adapter(fake):
    return AutoCADAdapter(acad_factory=lambda: fake)


# ---------------------------------------------------------------- tests

def test_factory_is_lazy(fake):
    calls = []
    AutoCADAdapter(acad_factory=lambda: calls.append(1) or fake)
    assert calls == []  # nothing connects until a tool method runs


def test_status_connected_with_document(adapter):
    result = adapter.status()
    assert result["ok"] is True
    assert result["connected"] is True
    assert result["document"] == "drawing1.dwg"


def test_draw_line_records_correct_endpoints(adapter, fake):
    result = adapter.draw_line(1, 2, 30, 40)
    assert result["ok"] is True
    assert result["handle"] == "L1"
    assert fake.model.calls == [
        ("AddLine", ((1.0, 2.0, 0.0), (30.0, 40.0, 0.0)))
    ]


def test_draw_line_with_z(adapter, fake):
    result = adapter.draw_line(0, 0, 5, 5, z=3.0)
    assert result["ok"] is True
    assert fake.model.calls[0][1] == ((0.0, 0.0, 3.0), (5.0, 5.0, 3.0))


def test_draw_circle(adapter, fake):
    result = adapter.draw_circle(10, 20, 7.5)
    assert result["ok"] is True
    assert result["handle"] == "C1"
    assert fake.model.calls == [("AddCircle", ((10.0, 20.0, 0.0), 7.5))]


def test_add_text(adapter, fake):
    result = adapter.add_text(3, 4, "Kitchen", height=5.0)
    assert result["ok"] is True
    assert result["handle"] == "T1"
    assert fake.model.calls == [("AddText", ("Kitchen", (3.0, 4.0, 0.0), 5.0))]


def test_list_entities_all(adapter):
    result = adapter.list_entities()
    assert result["ok"] is True
    assert result["count"] == 4
    first = result["entities"][0]
    assert first == {
        "handle": "10",
        "object_name": "Line",
        "type": "Line",
        "layer": "Walls",
    }


def test_list_entities_type_filter(adapter):
    result = adapter.list_entities(type_filter="Line")
    assert result["ok"] is True
    assert result["count"] == 2
    assert {e["handle"] for e in result["entities"]} == {"10", "11"}
    assert all(e["type"] == "Line" for e in result["entities"])


def test_list_entities_limit(adapter):
    result = adapter.list_entities(limit=3)
    assert result["ok"] is True
    assert result["count"] == 3


def test_list_layers(adapter):
    result = adapter.list_layers()
    assert result == {"ok": True, "layers": ["0"], "count": 1}


def test_create_layer(adapter, fake):
    result = adapter.create_layer("Furniture")
    assert result["ok"] is True
    assert result["layer"] == "Furniture"
    assert fake.doc.Layers.added == ["Furniture"]
    assert adapter.list_layers()["layers"] == ["0", "Furniture"]


def test_open_drawing_allowed_path(adapter, fake):
    allowed = str(core.ALLOWED_ROOTS[0] / "plans" / "house.dwg")
    result = adapter.open_drawing(allowed)
    assert result["ok"] is True
    assert result["document"] == "house.dwg"
    assert fake.app.Documents.opened == [allowed]


def test_open_drawing_rejects_disallowed_path(adapter, fake):
    result = adapter.open_drawing("/etc/passwd.dwg")
    assert result["ok"] is False
    assert "not allowed" in result["error"].lower()
    assert fake.app.Documents.opened == []  # Open never reached AutoCAD


def test_new_drawing(adapter, fake):
    result = adapter.new_drawing()
    assert result["ok"] is True
    assert result["document"] == "drawing1.dwg"
    assert fake.app.Documents.added_count == 1


def test_save(adapter, fake):
    result = adapter.save()
    assert result["ok"] is True
    assert fake.doc.saved is True


def test_run_command_sends_with_newline_and_is_audited(adapter, fake, tmp_path, monkeypatch):
    monkeypatch.setenv("DESKTOP_MCP_HOME", str(tmp_path))
    result = adapter.run_command("._ZOOM _E")
    assert result["ok"] is True
    assert fake.doc.sent_commands == ["._ZOOM _E\n"]
    log = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").strip().splitlines()
    records = [json.loads(line) for line in log]
    run_records = [r for r in records if r["tool"] == "autocad_run_command"]
    assert len(run_records) == 1
    assert run_records[0]["command"] == "._ZOOM _E"


def test_missing_autocad_returns_err_not_raise(monkeypatch):
    # No factory: on this (non-Windows / no-pyautocad) test machine every
    # method must degrade to an err dict instead of raising.
    adapter = AutoCADAdapter()
    for result in (adapter.status(), adapter.draw_line(0, 0, 1, 1), adapter.save()):
        assert result["ok"] is False
        assert result["error"]


def test_register_binds_all_tools():
    registered = {}

    class FakeMCP:
        def tool(self):
            def deco(fn):
                registered[fn.__name__] = fn
                return fn

            return deco

    from desktop_mcp.adapters.autocad import register

    adapter = register(FakeMCP())
    assert isinstance(adapter, AutoCADAdapter)
    assert set(registered) == {
        "autocad_status",
        "autocad_open_drawing",
        "autocad_new_drawing",
        "autocad_list_entities",
        "autocad_draw_line",
        "autocad_draw_circle",
        "autocad_add_text",
        "autocad_list_layers",
        "autocad_create_layer",
        "autocad_save",
        "autocad_run_command",
    }
