from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from godot_ship_kit.project import GodotProject, ProjectError, find_project, image_size


def test_open_example(example: Path) -> None:
    p = GodotProject.open(example / "scenes")
    assert p.root == example.resolve()
    assert p.name == "Hello Ship"
    assert p.godot_version == "4.6"
    assert p.renderer_mobile == "gl_compatibility"
    assert p.main_scene == "res://scenes/menu.tscn"
    preset = p.find_preset(None)
    assert preset is not None and preset.is_android
    assert preset.opt("package/unique_name") == "dev.godotshipkit.helloship"


def test_find_project_fails_cleanly(tmp_path: Path) -> None:
    with pytest.raises(ProjectError):
        find_project(tmp_path)


def test_renderer_defaults(tmp_path: Path) -> None:
    (tmp_path / "project.godot").write_text("config_version=5\n", encoding="utf-8")
    p = GodotProject.open(tmp_path)
    assert p.renderer_desktop == "forward_plus"
    assert p.renderer_mobile == "mobile"


def test_imports_and_walk_rules(example: Path) -> None:
    (example / "android" / "build").mkdir(parents=True)
    (example / "android" / "build" / "x.png.import").write_text('[remap]\nimporter="texture"\n', encoding="utf-8")
    (example / "art" / "android").mkdir()
    (example / "ignored").mkdir()
    (example / "ignored" / ".gdignore").write_text("", encoding="utf-8")
    (example / "ignored" / "y.png.import").write_text('[remap]\nimporter="texture"\n', encoding="utf-8")
    p = GodotProject.open(example)
    names = sorted(e.source_path.name for e in p.imports)
    assert names == ["backdrop.jpg", "ground_albedo.jpg", "icon.svg"]
    walked = {f.relative_to(example).as_posix() for f in p.walk()}
    assert not any(w.startswith(("android/", "ignored/")) for w in walked)


def test_shipped_bytes_prefers_mobile_variant(example: Path) -> None:
    imp = example / "art" / "ground_albedo.jpg.import"
    imp.write_text(
        '[remap]\nimporter="texture"\ntype="CompressedTexture2D"\n\n[deps]\n\n'
        'dest_files=["res://.godot/imported/g.s3tc.ctex", "res://.godot/imported/g.etc2.ctex"]\n\n[params]\n\ncompress/mode=2\n',
        encoding="utf-8",
    )
    d = example / ".godot" / "imported"
    d.mkdir(parents=True)
    (d / "g.s3tc.ctex").write_bytes(b"0" * 500)
    (d / "g.etc2.ctex").write_bytes(b"0" * 300)
    entry = next(e for e in GodotProject.open(example).imports if e.source_path.name == "ground_albedo.jpg")
    assert entry.shipped_bytes == 300
    assert entry.all_imported_bytes == 800


@pytest.mark.parametrize("fmt", ["PNG", "JPEG", "WEBP"])
def test_image_size(tmp_path: Path, fmt: str) -> None:
    path = tmp_path / f"img.{fmt.lower()}"
    Image.new("RGB", (123, 45), (10, 20, 30)).save(path, fmt)
    assert image_size(path) == (123, 45)


def test_image_size_rejects_garbage(tmp_path: Path) -> None:
    bad = tmp_path / "x.png"
    bad.write_bytes(b"nope")
    assert image_size(bad) is None


def test_reference_index_handles_runtime_paths(example: Path) -> None:
    (example / "music").mkdir()
    (example / "music" / "theme.ogg").write_bytes(b"x")
    (example / "voices").mkdir()
    (example / "voices" / "hi.ogg").write_bytes(b"x")
    (example / "scenes" / "audio.gd").write_text(
        'extends Node\nfunc a(t):\n\treturn load("res://music/%s.ogg" % t)\nfunc b(i):\n\treturn load("res://voices/" + i)\n',
        encoding="utf-8",
    )
    idx = GodotProject.open(example).reference_index
    assert idx.is_referenced("res://music/theme.ogg")
    assert idx.is_referenced("res://voices/hi.ogg")
    assert idx.is_referenced("res://art/ground_albedo.jpg")
    assert not idx.is_referenced("res://art/never_used.png")
    assert "res://art/ground_albedo.jpg" in idx.texture_3d_hint


def test_standalone_tres_material_and_sprite3d() -> None:
    from godot_ship_kit.project import _material_textures

    tres = (
        '[gd_resource type="StandardMaterial3D" load_steps=2 format=3]\n\n'
        '[ext_resource type="Texture2D" path="res://rock/rock_albedo.png" id="1"]\n\n'
        '[resource]\nalbedo_texture = ExtResource("1")\n'
    )
    assert _material_textures(tres) == {"res://rock/rock_albedo.png"}
    tscn = (
        '[gd_scene format=3]\n\n[ext_resource type="Texture2D" path="res://fx/smoke.png" id="1"]\n\n'
        '[node name="Smoke" type="Sprite3D"]\ntexture = ExtResource("1")\n'
    )
    assert _material_textures(tscn) == {"res://fx/smoke.png"}


def test_uid_main_scene(example: Path) -> None:
    menu = example / "scenes" / "menu.tscn"
    menu.write_text(menu.read_text().replace("[gd_scene load_steps=8 format=3]", '[gd_scene load_steps=8 format=3 uid="uid://abc123"]'))
    pg = example / "project.godot"
    pg.write_text(pg.read_text().replace('run/main_scene="res://scenes/menu.tscn"', 'run/main_scene="uid://abc123"'))
    p = GodotProject.open(example)
    assert p.main_scene == "uid://abc123"
    assert p.main_scene_res == "res://scenes/menu.tscn"
    (example / "scenes" / "menu.gd.uid").write_text("uid://scr1\n")
    assert p.resolve_uid("uid://scr1") == "res://scenes/menu.gd"
    assert p.resolve_uid("uid://missing") is None
