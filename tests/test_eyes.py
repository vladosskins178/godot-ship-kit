from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from godot_ship_kit.env import GodotBinary
from godot_ship_kit.eyes import compare as cmp
from godot_ship_kit.eyes.config import EyesConfigError, Shot, default_devices, device_size, load_config
from godot_ship_kit.eyes.report import render_html, render_markdown, write_reports
from godot_ship_kit.eyes.runner import EyesRun, ShotResult, analyse_shot, display_usable, split_log
from godot_ship_kit.project import GodotProject


def _scene(path: Path, color=(40, 90, 160), box=None, size=(200, 400)) -> Path:  # type: ignore[no-untyped-def]
    arr = np.zeros((size[1], size[0], 3), dtype=np.uint8)
    arr[:] = color
    arr[:, :, 0] = np.linspace(0, 255, size[0], dtype=np.uint8)[None, :]
    if box:
        x, y, w, h = box
        arr[y : y + h, x : x + w] = (250, 250, 20)
    Image.fromarray(arr).save(path)
    return path


def test_identical_images_match(tmp_path: Path) -> None:
    a = _scene(tmp_path / "a.png")
    d = cmp.compare(a, a, tmp_path / "d.png")
    assert d.similarity == pytest.approx(1.0)
    assert d.changed_ratio == 0 and d.regions == []


def test_changed_region_is_located(tmp_path: Path) -> None:
    a = _scene(tmp_path / "a.png")
    b = _scene(tmp_path / "b.png", box=(120, 300, 40, 40))
    d = cmp.compare(a, b, tmp_path / "d.png")
    assert d.changed_ratio == pytest.approx(40 * 40 / (200 * 400), rel=0.01)
    assert d.similarity < 1
    x, y, w, h = d.regions[0]
    assert 0.5 <= x <= 0.6 and 0.68 <= y <= 0.75 and w >= 0.2 and h >= 0.1
    assert (tmp_path / "d.png").is_file()


def test_size_mismatch(tmp_path: Path) -> None:
    d = cmp.compare(_scene(tmp_path / "a.png"), _scene(tmp_path / "b.png", size=(100, 100)))
    assert d.size_mismatch and d.changed_ratio == 1.0


def test_tolerance_ignores_rendering_noise(tmp_path: Path) -> None:
    a = _scene(tmp_path / "a.png")
    arr = np.asarray(Image.open(a)).astype(np.int16)
    noisy = np.clip(arr + np.random.default_rng(1).integers(-6, 7, arr.shape), 0, 255).astype(np.uint8)
    Image.fromarray(noisy).save(tmp_path / "n.png")
    d = cmp.compare(a, tmp_path / "n.png", tolerance=24)
    assert d.changed_ratio == 0 and d.similarity > 0.98


def test_frame_stats_and_crop(tmp_path: Path) -> None:
    black = tmp_path / "k.png"
    Image.new("RGB", (64, 64)).save(black)
    st = cmp.frame_stats(black)
    assert st.mean_luma == 0 and st.black_ratio == 1
    out = cmp.crop(_scene(tmp_path / "a.png"), (0.5, 0.5, 0.5, 0.25), tmp_path / "c.png")
    assert Image.open(out).size == (100, 100)
    pair = cmp.side_by_side(tmp_path / "a.png", tmp_path / "a.png", (0, 0, 0.5, 0.5), tmp_path / "p.png", pad=0)
    assert Image.open(pair).size == (100 + 12 + 100, 200)


def test_devices() -> None:
    assert device_size("phone") == (1080, 2400)
    assert device_size("800x600") == (800, 600)
    with pytest.raises(EyesConfigError):
        device_size("toaster")


def test_load_example_config(example: Path) -> None:
    project = GodotProject.open(example)
    cfg = load_config(project)
    assert [s.name for s in cfg.shots] == ["menu", "diorama", "menu-to-game"]
    assert cfg.devices == ["phone-small"]
    assert cfg.references == example / "tests" / "eyes"
    assert cfg.shots[0].zoom[0].rect == (0.1, 0.25, 0.8, 0.25)
    assert cfg.shots[2].actions[0] == {"press": "ui_accept"}
    adhoc = load_config(project, scenes=["res://scenes/diorama.tscn"], devices=["tablet"])
    assert [s.name for s in adhoc.shots] == ["diorama"] and adhoc.devices == ["tablet"]


def test_config_errors_and_defaults(example: Path) -> None:
    (example / "gsk.toml").write_text(
        '[[eyes.shot]]\nname="a"\nscene="res://scenes/menu.tscn"\n[[eyes.shot]]\nname="a"\nscene="res://scenes/diorama.tscn"\n'
    )
    with pytest.raises(EyesConfigError, match="unique"):
        load_config(GodotProject.open(example))
    with pytest.raises(EyesConfigError, match="not found"):
        load_config(GodotProject.open(example), scenes=["res://nope.tscn"])
    with pytest.raises(EyesConfigError, match="not found"):
        load_config(GodotProject.open(example), scenes=["nope/missing.tscn"])
    assert load_config(GodotProject.open(example), scenes=["scenes/menu.tscn"]).shots[0].scene == "res://scenes/menu.tscn"
    (example / "gsk.toml").unlink()
    cfg = load_config(GodotProject.open(example))
    assert [s.scene for s in cfg.shots] == ["res://scenes/menu.tscn"]  # falls back to the main scene
    assert default_devices(GodotProject.open(example)) == ["phone"]  # portrait project


def test_split_log() -> None:
    log = "\n".join(
        [
            "Godot Engine v4.6",
            "ERROR: boot problem",
            "   at: somewhere (x.cpp:1)",
            "[gsk-eyes] BEGIN menu",
            "SCRIPT ERROR: Invalid call",
            "          at: _ready (res://menu.gd:3)",
            "[gsk-eyes] END menu",
            "[gsk-eyes] BEGIN game",
            "[gsk-eyes] END game",
        ]
    )
    boot, per = split_log(log)
    assert boot == ["ERROR: boot problem  at: somewhere (x.cpp:1)"]
    assert per["menu"] == ["SCRIPT ERROR: Invalid call  at: _ready (res://menu.gd:3)"]
    assert per["game"] == []


def test_display_usable(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    assert not display_usable(None)
    assert not display_usable("garbage")
    assert not display_usable("127.0.0.1:59")  # nothing listens on 6059


def _run(tmp_path: Path, example: Path, results: list[ShotResult]) -> EyesRun:
    project = GodotProject.open(example)
    cfg = load_config(project)
    cfg.output = tmp_path / "out"
    return EyesRun(project, cfg, GodotBinary("/bin/godot", "4.6.3.stable.official.x"), results, "gl_compatibility", "now", 3.0)


def test_analyse_shot_alerts_and_statuses(tmp_path: Path, example: Path) -> None:
    project = GodotProject.open(example)
    cfg = load_config(project)
    cfg.output = tmp_path / "out"
    cfg.references = tmp_path / "refs"
    shot = Shot("menu", "res://scenes/menu.tscn")
    img = _scene(tmp_path / "menu.png")
    facts = {
        "has_3d": True,
        "camera_3d": "",
        "meshes": 2,
        "meshes_on_screen": 0,
        "no_material": ["Tower"],
        "idle_skeletons": ["Hero/AnimationPlayer"],
    }

    res = ShotResult("menu", "phone", shot.scene, "error", image=img, facts=dict(facts))
    analyse_shot(res, cfg, shot, update=False)
    assert res.status == "new"
    text = " ".join(a.message for a in res.alerts)
    assert "no current Camera3D" in text and "no material" in text and "T-pose" in text

    res = ShotResult("menu", "phone", shot.scene, "error", image=img)
    analyse_shot(res, cfg, shot, update=True)
    assert res.status == "updated" and (cfg.references / ".gdignore").exists()
    assert (cfg.references / "phone" / "menu.png").is_file()

    res = ShotResult("menu", "phone", shot.scene, "error", image=img)
    analyse_shot(res, cfg, shot, update=False)
    assert res.status == "pass" and not res.failed

    changed = _scene(tmp_path / "menu2.png", box=(20, 20, 120, 120))
    res = ShotResult("menu", "phone", shot.scene, "error", image=changed)
    analyse_shot(res, cfg, shot, update=False)
    assert res.status == "changed" and res.failed and res.region_crops

    black = tmp_path / "black.png"
    Image.new("RGB", (200, 400)).save(black)
    res = ShotResult("menu", "phone", shot.scene, "error", image=black)
    analyse_shot(res, cfg, shot, update=False)
    assert any(a.level == "error" and "black" in a.message for a in res.alerts) and res.failed


def test_reports(tmp_path: Path, example: Path) -> None:
    img = _scene(tmp_path / "a.png")
    ok = ShotResult("menu", "phone", "res://scenes/menu.tscn", "pass", image=img, reference=img, diff=cmp.Diff(1.0, 0.0, 0))
    new = ShotResult("game<script>", "phone", "res://g.tscn", "new", image=img)
    run = _run(tmp_path, example, [ok, new])
    html = render_html(run)
    assert "<script>alert" not in html and "game&lt;script&gt;" in html
    assert "1 match" in html and "1 new" in html
    md = render_markdown(run)
    assert "| menu | phone | match |" in md
    paths = write_reports(run)
    assert all(p.is_file() for p in paths.values())


def test_config_paths_must_stay_inside_project(example: Path) -> None:
    (example / "gsk.toml").write_text('[eyes]\noutput = "../../elsewhere"\n')
    with pytest.raises(EyesConfigError, match="outside the project"):
        load_config(GodotProject.open(example))


def test_render_capture_script(tmp_path: Path) -> None:
    from godot_ship_kit.eyes.runner import EyesError, gd_literal, render_capture_script

    assert gd_literal({"a": [1, 2.5, True, None, 'q"uote']}) == '{"a": [1, 2.5, true, null, "q\\"uote"]}'
    shots = [
        Shot(
            "menu",
            "res://scenes/menu.tscn",
            wait=1.5,
            actions=[
                {"tap": [0.5, 0.6]},
                {"press": "ui_accept", "then_wait": 1},
                {"wait": 0.2},
                {"node": "Player", "call": "jump", "args": [2]},
                {"node": "/root/Hud", "set": "visible", "value": False},
            ],
        )
    ]
    text = render_capture_script(720, 1600, tmp_path, 60, shots)
    assert "const WIDTH: int = 720" in text and "const BUDGET_MS: int = 60000" in text
    assert 'await _open_scene("menu", "res://scenes/menu.tscn", 1.5)' in text
    assert "await _tap(0.5, 0.6, 0.3)" in text
    assert 'await _press("ui_accept", 1.0)' in text
    assert 'await _call_node("Player", "jump", [2])' in text
    assert 'await _set_node("/root/Hud", "visible", false)' in text
    assert "@@" not in text
    with pytest.raises(EyesError):
        render_capture_script(720, 1600, tmp_path, 60, [Shot("x", "res://x.tscn", actions=[{"jump": 1}])])
