from __future__ import annotations

from pathlib import Path

import pytest

from godot_ship_kit import __version__, godot_cfg
from godot_ship_kit.checklist import build_checklist
from godot_ship_kit.cli import main
from godot_ship_kit.project import GodotProject


def test_version(capsys) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_no_command_prints_help(capsys) -> None:  # type: ignore[no-untyped-def]
    assert main([]) == 2
    assert "doctor" in capsys.readouterr().out


def test_not_a_project(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    assert main(["doctor", str(tmp_path)]) == 2
    assert "No project.godot" in capsys.readouterr().err


def test_play_checklist(example: Path, tmp_path: Path) -> None:
    text = build_checklist(GodotProject.open(example))
    for needle in ("Data safety", "Content rating", "Target audience", "1024 x 500", "API 36", "12 testers", "Hello Ship"):
        assert needle in text
    out = tmp_path / "PLAY.md"
    assert main(["play-checklist", str(example), "-o", str(out)]) == 0
    assert out.read_text().startswith("# Google Play launch checklist")


def test_checklist_detects_ads_sdk(example: Path) -> None:
    (example / "addons" / "AdmobPlugin").mkdir(parents=True)
    text = build_checklist(GodotProject.open(example))
    assert "ads SDK (AdmobPlugin)" in text


def test_configure_writes_editor_settings(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    settings = tmp_path / "editor_settings-4.6.tres"
    sdk = tmp_path / "sdk"
    sdk.mkdir()
    assert main(["configure", "--settings", str(settings), "--android-sdk", str(sdk)]) == 0
    assert godot_cfg.load(settings).get("resource", "export/android/android_sdk_path") == str(sdk.resolve())
    assert main(["configure", "--settings", str(settings), "--java-sdk", str(tmp_path / "missing")]) == 2


def test_skill_print_and_install(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    assert main(["skill", "print"]) == 0
    plain = capsys.readouterr().out
    assert plain.startswith("# godot-ship-kit") and "name: godot-ship-kit" not in plain
    assert main(["skill", "print", "--frontmatter"]) == 0
    assert capsys.readouterr().out.startswith("---\nname: godot-ship-kit")
    assert main(["skill", "install", "--dir", str(tmp_path)]) == 0
    assert (tmp_path / "godot-ship-kit" / "SKILL.md").is_file()


def test_json_outputs_are_pure_json(example: Path, toolchain, capsys, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import json

    from .conftest import add_fake_imports

    toolchain()
    add_fake_imports(example, {"ground_albedo.jpg": 3_000_000})
    for argv in (
        ["doctor", str(example), "--json"],
        ["slim", str(example), "--json"],
        ["slim", str(example), "--json", "--apply"],
        ["slim", str(example), "--json", "--undo"],
        ["build", str(example), "--json", "--debug", "--no-verify"],
        ["build", str(example), "--json", "--debug"],
    ):
        main(argv)
        out = capsys.readouterr().out
        json.loads(out)  # raises if anything but one JSON document was printed


def test_errors_go_to_stderr(example: Path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("GODOT", "/nonexistent")
    monkeypatch.setenv("PATH", "/nonexistent")
    assert main(["build", str(example), "--json"]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err.startswith("gsk: Godot not found")
