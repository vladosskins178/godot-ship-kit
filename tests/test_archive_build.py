from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from godot_ship_kit import archive
from godot_ship_kit.build import BuildError, default_output, export, find_hints, preset_format
from godot_ship_kit.cli import main
from godot_ship_kit.env import find_godot
from godot_ship_kit.godot_cfg import set_values
from godot_ship_kit.project import GodotProject
from godot_ship_kit.report import Severity

from .conftest import add_fake_imports


def _apk(path: Path, arm64: bool = True) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        if arm64:
            z.writestr("lib/arm64-v8a/libgodot_android.so", b"\0" * 10000)
        z.writestr("assets/.godot/imported/ground_albedo.jpg-1.ctex", b"\1" * 5000)
        z.writestr("classes.dex", b"dex")
        z.writestr("META-INF/CERT.RSA", b"sig")
    return path


def _checks(rep: archive.ArchiveReport) -> dict[str, Severity]:
    return {f.check: f.severity for f in rep.findings}


def test_inspect_apk_categories_and_sources(example: Path, tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("PATH", "/nonexistent")
    imp = example / "art" / "ground_albedo.jpg.import"
    imp.write_text(imp.read_text().replace("ground_albedo.jpg-f82b4fb43d4d9a89ba02e7ddd8c985dd.ctex", "ground_albedo.jpg-1.ctex"))
    add_fake_imports(example, {})
    rep = archive.inspect(_apk(tmp_path / "a.apk"), None, GodotProject.open(example))
    c = _checks(rep)
    assert c["archive.zip"] == Severity.OK
    assert c["archive.arm64"] == Severity.OK
    assert c["archive.signature"] == Severity.INFO  # apksigner not available
    assert "native libraries" in rep.categories and "imported assets" in rep.categories
    sources = {e: s for e, _, s in rep.biggest}
    assert sources["assets/.godot/imported/ground_albedo.jpg-1.ctex"] == "res://art/ground_albedo.jpg"
    assert rep.ok


def test_inspect_flags_missing_arm64_and_corruption(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("PATH", "/nonexistent")
    rep = archive.inspect(_apk(tmp_path / "b.apk", arm64=False))
    assert _checks(rep)["archive.arm64"] == Severity.ERROR and not rep.ok
    bad = tmp_path / "c.apk"
    bad.write_bytes(b"PK\3\4 garbage")
    assert _checks(archive.inspect(bad))["archive.zip"] == Severity.ERROR
    assert _checks(archive.inspect(tmp_path / "missing.apk"))["archive.exists"] == Severity.ERROR


def test_preset_format_and_default_output(example: Path) -> None:
    project = GodotProject.open(example)
    preset = project.find_preset(None)
    assert preset is not None
    assert preset_format(preset) == "apk"
    assert default_output(project, preset, True) == example / "build" / "hello-ship.apk"
    preset.values["export_path"] = ""
    assert default_output(project, preset, False).name == "hello-ship-1.0.0-debug.apk"
    preset.options.update({"gradle_build/use_gradle_build": True, "gradle_build/export_format": 1})
    assert preset_format(preset) == "aab"


def test_find_hints() -> None:
    log = (
        'ERROR: Cannot export project with preset "Android" due to configuration errors:\n'
        "A valid Java SDK path is required in Editor Settings."
    )
    hints = find_hints(log)
    assert any("java-sdk" in h for h in hints)
    assert find_hints("all good") == []


def test_export_with_fake_godot(example: Path, toolchain, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    toolchain()
    godot = find_godot()
    assert godot is not None and godot.short == "4.6.3"
    project = GodotProject.open(example)
    preset = project.find_preset(None)
    assert preset is not None
    res = export(godot, project, preset, example / "build" / "x.apk", release=False)
    assert res.output.is_file() and res.log.is_file()
    with pytest.raises(BuildError, match="APK"):
        export(godot, project, preset, example / "build" / "x.aab")
    monkeypatch.setenv("FAKE_GODOT_FAIL", "1")
    with pytest.raises(BuildError) as exc:
        export(godot, project, preset, example / "build" / "y.apk")
    assert any("templates" in h for h in exc.value.hints)


def test_cli_build_and_verify(example: Path, toolchain, capsys, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    toolchain()
    assert main(["build", str(example), "--debug"]) == 0
    out = capsys.readouterr().out
    assert "Archive integrity OK" in out and "native libraries" in out
    apk = example / "build" / "hello-ship.apk"
    assert main(["verify", str(apk), "--json"]) == 0
    capsys.readouterr()
    assert main(["verify", str(apk), "--release"]) == 1  # debug certificate on a release check
    assert "debug certificate" in capsys.readouterr().out
    monkeypatch.setenv("FAKE_APKSIGNER_FAIL", "1")
    assert main(["verify", str(apk)]) == 1
    assert main(["build", str(example), "--preset", "Nope"]) == 2


def test_cli_build_reports_failure(example: Path, toolchain, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    toolchain()
    monkeypatch.setenv("FAKE_GODOT_FAIL", "1")
    assert main(["build", str(example)]) == 1
    out = capsys.readouterr().out
    assert "Export failed" in out and "hint:" in out


def test_aab_requires_gradle_in_cli(example: Path, toolchain, capsys) -> None:  # type: ignore[no-untyped-def]
    toolchain()
    cfg = example / "export_presets.cfg"
    cfg.write_text(
        set_values(cfg.read_text(), "preset.0.options", {"gradle_build/use_gradle_build": True, "gradle_build/export_format": 1})
    )
    assert main(["build", str(example), "-o", str(example / "out.apk")]) == 1
    assert "exports AAB" in capsys.readouterr().out
