from __future__ import annotations

import json
import struct
from pathlib import Path

from godot_ship_kit import godot_cfg, slim
from godot_ship_kit.cli import main
from godot_ship_kit.project import GodotProject

from .conftest import add_fake_imports


def _params(path: Path) -> dict:  # type: ignore[type-arg]
    return godot_cfg.load(path).section("params")


def test_analyze_example(example: Path) -> None:
    add_fake_imports(example, {"ground_albedo.jpg": 3_400_000, "backdrop.jpg": 1_800_000, "icon.svg": 5000})
    rep = slim.analyze(GodotProject.open(example))
    assert rep.total_shipped == 3_400_000 + 1_800_000 + 5000
    rules = {(p.rule, p.entry.source_path.name, p.risk) for p in rep.proposals}
    # the 3D ground texture gets the safe VRAM change plus an optional size cap
    assert ("vram-3d", "ground_albedo.jpg", slim.SAFE) in rules
    assert ("cap-3d", "ground_albedo.jpg", slim.REVIEW) in rules
    # the 2D backdrop is never forced to VRAM; only reviewable suggestions
    assert ("lossy-2d", "backdrop.jpg", slim.REVIEW) in rules
    assert ("cap-2d", "backdrop.jpg", slim.REVIEW) in rules
    assert not any(p.entry.source_path.name == "backdrop.jpg" and p.rule == "vram-3d" for p in rep.proposals)
    vram = next(p for p in rep.proposals if p.rule == "vram-3d")
    assert vram.estimated_bytes == 2048 * 2048 * 4 // 8 * 4 // 3  # ETC2 RGB with mipmaps
    disk_safe, gpu_safe = rep.estimate(False)
    disk_all, gpu_all = rep.estimate(True)
    assert gpu_safe > 0 and gpu_all >= gpu_safe
    assert disk_all < disk_safe  # size caps compound on top of VRAM compression
    assert rep.to_dict()["estimated_change_bytes"]["aggressive"] == disk_all


def test_apply_and_undo_roundtrip(example: Path) -> None:
    add_fake_imports(example, {"ground_albedo.jpg": 3_400_000, "backdrop.jpg": 1_800_000})
    project = GodotProject.open(example)
    imp = example / "art" / "ground_albedo.jpg.import"
    original = imp.read_bytes()
    rep = slim.analyze(project)
    safe = [p for p in rep.proposals if p.risk == slim.SAFE]
    backup = slim.apply(project, safe)
    assert backup is not None and (backup / "manifest.json").is_file()
    params = _params(imp)
    assert params["compress/mode"] == 2 and params["mipmaps/generate"] is True
    # untouched keys and sections survive
    assert godot_cfg.load(imp).get("deps", "source_file") == "res://art/ground_albedo.jpg"
    assert (example / ".gsk" / ".gitignore").read_text() == "*\n"
    restored = slim.undo(project)
    assert restored is not None
    assert imp.read_bytes() == original
    assert slim.undo(project) is None  # nothing left to undo


def test_normal_maps_get_normal_compression(example: Path) -> None:
    src = example / "art" / "rock_normal.png"
    src.write_bytes((example / "art" / "icon.svg").read_bytes())  # contents irrelevant
    (example / "art" / "rock_normal.png.import").write_text(
        (example / "art" / "ground_albedo.jpg.import").read_text().replace("ground_albedo.jpg", "rock_normal.png"),
        encoding="utf-8",
    )
    add_fake_imports(example, {"rock_normal.png": 2_000_000})
    rep = slim.analyze(GodotProject.open(example))
    prop = next(p for p in rep.proposals if p.entry.source_path.name == "rock_normal.png" and p.rule == "vram-3d")
    assert prop.changes["compress/normal_map"] == 1


def test_ui_textures_are_not_treated_as_3d(example: Path) -> None:
    ui = example / "ui"
    ui.mkdir()
    (ui / "button_albedo.png").write_bytes(b"x")
    project = GodotProject.open(example)
    entry = next(e for e in project.imports if e.source_path.name == "backdrop.jpg")
    entry.source_path = ui / "button_albedo.png"
    entry.res_path = "res://ui/button_albedo.png"
    assert not slim.is_3d_texture(project, entry)


def test_wav_rule(example: Path) -> None:
    (example / "sfx.wav").write_bytes(b"RIFF")
    (example / "sfx.wav.import").write_text(
        '[remap]\n\nimporter="wav"\ntype="AudioStreamWAV"\n\n[deps]\n\ndest_files=["res://.godot/imported/sfx.wav-1.sample"]\n\n[params]\n\ncompress/mode=0\n',
        encoding="utf-8",
    )
    add_fake_imports(example, {"sfx.wav": 2_000_000})
    rep = slim.analyze(GodotProject.open(example))
    assert any(p.rule == "wav-qoa" and p.changes == {"compress/mode": 2} for p in rep.proposals)


def _glb(tris: int) -> bytes:
    doc = {
        "asset": {"version": "2.0"},
        "accessors": [{"count": tris * 3}, {"count": 99}],
        "meshes": [{"primitives": [{"indices": 0, "attributes": {"POSITION": 1}}, {"mode": 1, "attributes": {"POSITION": 1}}]}],
    }
    js = json.dumps(doc).encode()
    js += b" " * (-len(js) % 4)
    body = struct.pack("<I4s", len(js), b"JSON") + js
    return b"glTF" + struct.pack("<II", 2, 12 + len(body)) + body


def test_glb_triangles(tmp_path: Path) -> None:
    p = tmp_path / "m.glb"
    p.write_bytes(_glb(1234))
    assert slim.glb_triangles(p) == 1234
    p.write_bytes(b"not a glb")
    assert slim.glb_triangles(p) is None


def test_cli_slim_report_and_apply(example: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    add_fake_imports(example, {"ground_albedo.jpg": 3_400_000, "backdrop.jpg": 1_800_000})
    assert main(["slim", str(example)]) == 0
    out = capsys.readouterr().out
    assert "VRAM-compress 3D textures" in out and "ground_albedo.jpg" in out
    assert main(["slim", str(example), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["proposals"]
    assert main(["slim", str(example), "--apply", "--aggressive"]) == 0
    capsys.readouterr()
    assert _params(example / "art" / "backdrop.jpg.import")["compress/mode"] == 1
    assert main(["slim", str(example), "--undo"]) == 0
    assert _params(example / "art" / "backdrop.jpg.import")["compress/mode"] == 0
