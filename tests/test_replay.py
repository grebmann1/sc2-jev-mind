import json

from sc2mind.replay import annotations, write_export


def test_optional_annotations_and_partial_lines(tmp_path):
    assert annotations(tmp_path)["plans"] == []
    (tmp_path / "result.json").write_text(json.dumps({"result": "Victory", "memory_saved": True}))
    (tmp_path / "trace.jsonl").write_text('{"kind":"s1","t":2}\n{"kind":"s2","t":5}\n{"kind":')
    evidence = annotations(tmp_path)
    assert evidence["result"] == "Victory"
    assert evidence["memorySaved"] is True
    assert evidence["decisions"] == [{"kind": "s1", "t": 2}]
    assert evidence["plans"] == [{"kind": "s2", "t": 5}]


def test_export_publishes_small_index_and_complete_data(tmp_path):
    data = {
        "map": "Test",
        "duration": 10,
        "frames": [{"t": 0}, {"t": 10}],
        "terrain": {"data": [1]},
        "plans": [],
        "decisions": [{"t": 2}],
        "types": {},
    }
    write_export(tmp_path / "viewer", data)
    index = json.loads((tmp_path / "viewer/metadata.json").read_text())
    assert index == {"map": "Test", "duration": 10, "frames": 2, "plans": 0, "decisions": 1}
    assert json.loads((tmp_path / "viewer/data.json").read_text()) == data
    assert not list((tmp_path / "viewer").glob("*.tmp"))
