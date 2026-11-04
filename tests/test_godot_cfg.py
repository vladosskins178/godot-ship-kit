from __future__ import annotations

import time

from godot_ship_kit import godot_cfg
from godot_ship_kit.godot_cfg import GodotCall, as_list, format_value, parse, set_values

PROJECT = """; Engine configuration file.
config_version=5

[application]

config/name="Hello \\"Ship\\""
config/features=PackedStringArray("4.6", "GL Compatibility")
run/main_scene="res://main.tscn"

[display]

window/size/viewport_width=720
window/stretch/scale=1.5
"""

IMPORT = """[remap]

importer="texture"
path.s3tc="res://.godot/imported/a.png-1.s3tc.ctex"
metadata={
"imported_formats": ["s3tc_bptc", "etc2_astc"],
"vram_texture": true
}

[deps]

source_file="res://a.png"
dest_files=["res://.godot/imported/a.png-1.s3tc.ctex", "res://.godot/imported/a.png-1.etc2.ctex"]

[params]

compress/mode=0
process/size_limit=0
roughness/src_normal=""
"""


def test_parse_basic_types() -> None:
    cfg = parse(PROJECT)
    assert cfg.get("", "config_version") == 5
    assert cfg.get("application", "config/name") == 'Hello "Ship"'
    feats = cfg.get("application", "config/features")
    assert isinstance(feats, GodotCall)
    assert as_list(feats) == ["4.6", "GL Compatibility"]
    assert cfg.get("display", "window/size/viewport_width") == 720
    assert cfg.get("display", "window/stretch/scale") == 1.5


def test_parse_multiline_dict_and_arrays() -> None:
    cfg = parse(IMPORT)
    meta = cfg.get("remap", "metadata")
    assert meta == {"imported_formats": ["s3tc_bptc", "etc2_astc"], "vram_texture": True}
    assert len(cfg.get("deps", "dest_files")) == 2
    assert cfg.get("params", "roughness/src_normal") == ""


def test_parse_editor_settings_header() -> None:
    cfg = parse('[gd_resource type="EditorSettings" format=3]\n\n[resource]\nexport/android/java_sdk_path = "/jdk"\n')
    assert cfg.get("resource", "export/android/java_sdk_path") == "/jdk"


def test_unparseable_value_is_kept_raw() -> None:
    assert godot_cfg.parse_value("Vector2(1, 2") == "Vector2(1, 2"
    assert godot_cfg.parse_value("Color(1, 0.5, 0, 1)") == GodotCall("Color", (1, 0.5, 0, 1))


def test_set_values_replaces_in_place_and_preserves_rest() -> None:
    out = set_values(IMPORT, "params", {"compress/mode": 2, "compress/normal_map": 1})
    assert "compress/mode=2\n" in out
    assert "compress/mode=0" not in out
    # new key lands inside [params], after the last existing key
    params = out.split("[params]")[1]
    assert "compress/normal_map=1" in params
    # everything outside the edited keys is untouched
    assert out.replace("compress/mode=2", "compress/mode=0").replace("compress/normal_map=1\n", "") == IMPORT


def test_set_values_creates_section() -> None:
    out = set_values("[a]\n\nx=1\n", "b", {"y": True, "z": "p"})
    assert parse(out).section("b") == {"y": True, "z": "p"}


def test_format_value() -> None:
    assert format_value(True) == "true"
    assert format_value(0.8) == "0.8"
    assert format_value(2.0) == "2.0"
    assert format_value('a"b') == '"a\\"b"'


def test_huge_multiline_value_parses_quickly() -> None:
    # Scene .import files carry huge _subresources dicts; parsing must stay linear.
    body = ",\n".join(f'"k{i}": {{"a": [1, 2, 3], "b": "x"}}' for i in range(20000))
    text = "[params]\n\n_subresources={\n" + body + "\n}\nother=1\n"
    start = time.perf_counter()
    cfg = parse(text)
    assert time.perf_counter() - start < 5
    assert cfg.get("params", "other") == 1
    assert len(cfg.get("params", "_subresources")) == 20000
