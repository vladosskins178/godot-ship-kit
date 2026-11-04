"""End-to-end runs against a real Godot binary. Skipped unless $GODOT is set."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from godot_ship_kit.cli import main

from .conftest import EXAMPLE

pytestmark = pytest.mark.godot

needs_godot = pytest.mark.skipif(
    not os.environ.get("GODOT") or not (shutil.which("xvfb-run") or os.environ.get("DISPLAY")),
    reason="set GODOT=/path/to/godot and install xvfb to run end-to-end tests",
)


@pytest.fixture
def real_example(tmp_path: Path) -> Path:
    dst = tmp_path / "hello-ship"
    shutil.copytree(EXAMPLE, dst, ignore=shutil.ignore_patterns(".gsk", "build"))
    return dst


@needs_godot
def test_eyes_matches_committed_references(real_example: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    code = main(["eyes", str(real_example), "--json", "--display", "xvfb"])
    data = json.loads(capsys.readouterr().out)
    statuses = {r["shot"]: r["status"] for r in data["results"]}
    assert statuses == {"menu": "match", "diorama": "match", "menu-to-game": "match"}, data
    assert code == 0
    assert Path(data["reports"]["html"]).is_file()


@needs_godot
def test_eyes_catches_a_regression(real_example: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    scene = real_example / "scenes" / "diorama.tscn"
    text = scene.read_text()
    scene.write_text(
        text.replace(
            '[sub_resource type="CylinderMesh" id="tower_mesh"]\nmaterial = SubResource("stone")',
            '[sub_resource type="CylinderMesh" id="tower_mesh"]',
        )
    )
    code = main(["eyes", str(real_example), "--json", "--display", "xvfb", "--scene", "res://scenes/diorama.tscn"])
    data = json.loads(capsys.readouterr().out)
    res = data["results"][0]
    assert code == 1 and res["status"] == "changed"
    assert any("no material" in a["message"] for a in res["alerts"])
    assert res["diff"]["regions"]


@needs_godot
def test_capture_script_parses_under_strict_warnings(real_example: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    strict = "\n[debug]\n\n" + "".join(
        f"gdscript/warnings/{w}=2\n"
        for w in (
            "untyped_declaration",
            "inferred_declaration",
            "unsafe_property_access",
            "unsafe_method_access",
            "unsafe_call_argument",
            "unsafe_cast",
            "return_value_discarded",
            "integer_division",
        )
    )
    with (real_example / "project.godot").open("a", encoding="utf-8") as fh:
        fh.write(strict)
    code = main(["eyes", str(real_example), "--json", "--display", "xvfb", "--scene", "res://scenes/diorama.tscn"])
    data = json.loads(capsys.readouterr().out)
    assert data["results"][0]["status"] == "match", data
    assert code == 0


@needs_godot
def test_failed_actions_are_errors_and_not_saved(real_example: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    (real_example / "gsk.toml").write_text(
        '[eyes]\ndevices = ["phone-small"]\nreferences = "refs"\n'
        '[[eyes.shot]]\nname = "bad"\nscene = "res://scenes/menu.tscn"\n'
        'actions = [{ press = "ui_acept" }, { node = "Nope", call = "x" }]\n'
    )
    code = main(["eyes", str(real_example), "--json", "--display", "xvfb", "--update"])
    res = json.loads(capsys.readouterr().out)["results"][0]
    assert code == 1 and res["status"] == "error"
    assert sum("Capture step failed" in a["message"] for a in res["alerts"]) == 2
    assert not (real_example / "refs" / "phone-small" / "bad.png").exists()


@needs_godot
def test_overflowing_ui_is_reported(real_example: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    menu = real_example / "scenes" / "menu.tscn"
    menu.write_text(menu.read_text().replace("font_size = 84", "font_size = 120"))
    main(["eyes", str(real_example), "--json", "--display", "xvfb", "--scene", "res://scenes/menu.tscn"])
    res = json.loads(capsys.readouterr().out)["results"][0]
    assert any("runs off the screen" in a["message"] for a in res["alerts"]), res["alerts"]
