from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from godot_ship_kit.cli import main
from godot_ship_kit.doctor import run_doctor
from godot_ship_kit.godot_cfg import set_values
from godot_ship_kit.project import GodotProject
from godot_ship_kit.report import Severity

from .conftest import add_fake_imports


def _by_check(findings):  # type: ignore[no-untyped-def]
    out: dict[str, list] = {}
    for f in findings:
        out.setdefault(f.check, []).append(f)
    return out


def _edit(path: Path, section: str, values: dict) -> None:  # type: ignore[type-arg]
    path.write_text(set_values(path.read_text(encoding="utf-8"), section, values), encoding="utf-8")


def test_healthy_example(example: Path, toolchain) -> None:  # type: ignore[no-untyped-def]
    toolchain()
    findings, ctx = run_doctor(GodotProject.open(example))
    checks = _by_check(findings)
    errors = [f.title for f in findings if f.severity == Severity.ERROR]
    assert errors == []
    assert checks["android.target_sdk"][0].severity == Severity.OK
    assert ctx.template_sdks["targetSdk"] == 36
    assert checks["android.jdk"][0].severity == Severity.OK
    assert checks["preset.package"][0].severity == Severity.OK
    assert checks["signing.release"][0].severity == Severity.WARN  # no release keystore in the sample


def test_low_target_sdk_is_an_error(example: Path, toolchain) -> None:  # type: ignore[no-untyped-def]
    toolchain(target_sdk=34)
    checks = _by_check(run_doctor(GodotProject.open(example))[0])
    f = checks["android.target_sdk"][0]
    assert f.severity == Severity.ERROR
    assert "34" in f.title and "developer.android.com" in f.docs


def test_gradle_override_raises_target(example: Path, toolchain) -> None:  # type: ignore[no-untyped-def]
    toolchain(target_sdk=34)
    presets = example / "export_presets.cfg"
    _edit(presets, "preset.0.options", {"gradle_build/use_gradle_build": True, "gradle_build/target_sdk": "36"})
    (example / "android" / "build").mkdir(parents=True)
    (example / "android" / ".build_version").write_text("4.6.3.stable", encoding="utf-8")
    checks = _by_check(run_doctor(GodotProject.open(example))[0])
    assert checks["android.target_sdk"][0].severity == Severity.OK


def test_missing_templates_and_godot(example: Path, toolchain, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    toolchain(with_templates=False)
    checks = _by_check(run_doctor(GodotProject.open(example))[0])
    assert checks["godot.templates"][0].severity == Severity.ERROR
    monkeypatch.setenv("GODOT", "/nonexistent/godot")
    monkeypatch.setenv("PATH", "/nonexistent")
    checks = _by_check(run_doctor(GodotProject.open(example))[0])
    assert checks["godot.binary"][0].severity == Severity.ERROR


def test_bad_preset_values(example: Path, toolchain) -> None:  # type: ignore[no-untyped-def]
    toolchain()
    _edit(
        example / "export_presets.cfg",
        "preset.0.options",
        {
            "package/unique_name": "com.example.$genname",
            "architectures/arm64-v8a": False,
            "gradle_build/export_format": 1,
            "permissions/camera": True,
            "permissions/manage_external_storage": True,
        },
    )
    checks = _by_check(run_doctor(GodotProject.open(example))[0])
    assert checks["preset.package"][0].severity == Severity.ERROR
    assert checks["preset.arch"][0].severity == Severity.ERROR
    assert checks["preset.format"][0].severity == Severity.ERROR  # AAB without gradle
    perms = checks["preset.permissions"][0]
    assert perms.severity == Severity.WARN and "camera" in perms.detail


def test_project_settings_checks(example: Path, toolchain) -> None:  # type: ignore[no-untyped-def]
    toolchain()
    pg = example / "project.godot"
    _edit(pg, "rendering", {"textures/vram_compression/import_etc2_astc": False, "renderer/rendering_method.mobile": "forward_plus"})
    _edit(pg, "display", {"window/stretch/mode": "disabled"})
    checks = _by_check(run_doctor(GodotProject.open(example))[0])
    assert checks["rendering.etc2"][0].severity == Severity.ERROR
    assert checks["rendering.renderer"][0].severity == Severity.WARN
    assert checks["display.stretch"][0].severity == Severity.WARN


def test_network_without_internet_permission(example: Path, toolchain) -> None:  # type: ignore[no-untyped-def]
    toolchain()
    (example / "scenes" / "net.gd").write_text("extends Node\nvar r := HTTPRequest.new()\n", encoding="utf-8")
    checks = _by_check(run_doctor(GodotProject.open(example))[0])
    assert checks["preset.internet"][0].severity == Severity.WARN


def test_big_uncompressed_textures(example: Path, toolchain) -> None:  # type: ignore[no-untyped-def]
    toolchain()
    add_fake_imports(example, {"ground_albedo.jpg": 3_000_000})
    checks = _by_check(run_doctor(GodotProject.open(example))[0])
    tex = checks["textures.uncompressed"][0]
    assert tex.severity == Severity.WARN and "ground_albedo" in tex.detail
    assert checks["size.estimate"][0].severity == Severity.OK


def test_keystore_tracked_in_git_is_an_error(example: Path, toolchain, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    toolchain()
    ks = example / "release.keystore"
    ks.write_bytes(b"secret bytes")
    subprocess.run(["git", "init", "-q", str(example)], check=True)
    subprocess.run(["git", "-C", str(example), "add", "release.keystore"], check=True)
    monkeypatch.setenv("GODOT_ANDROID_KEYSTORE_RELEASE_PATH", str(ks))
    monkeypatch.setenv("GODOT_ANDROID_KEYSTORE_RELEASE_USER", "upload")
    monkeypatch.setenv("GODOT_ANDROID_KEYSTORE_RELEASE_PASSWORD", "hunter2-very-secret")
    checks = _by_check(run_doctor(GodotProject.open(example))[0])
    assert checks["signing.release"][0].severity == Severity.OK
    assert checks["signing.location"][0].severity == Severity.ERROR
    assert checks["git.secrets"][0].severity == Severity.ERROR


def test_secrets_never_printed(example: Path, toolchain, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    toolchain()
    secret_dir = example.parent / "vault"
    secret_dir.mkdir()
    ks = secret_dir / "upload.keystore"
    ks.write_bytes(b"x")
    monkeypatch.setenv("GODOT_ANDROID_KEYSTORE_RELEASE_PATH", str(ks))
    monkeypatch.setenv("GODOT_ANDROID_KEYSTORE_RELEASE_USER", "alias-name-xyz")
    monkeypatch.setenv("GODOT_ANDROID_KEYSTORE_RELEASE_PASSWORD", "hunter2-very-secret")
    (example / ".godot").mkdir()
    (example / ".godot" / "export_credentials.cfg").write_text(
        '[preset.0.options]\n\nkeystore/release_password="cred-file-secret"\n', encoding="utf-8"
    )
    for args in (["doctor", str(example), "--json"], ["doctor", str(example), "-v"], ["doctor", str(example), "--markdown"]):
        main(args)
        out = capsys.readouterr().out
        for secret in ("hunter2-very-secret", "alias-name-xyz", "cred-file-secret"):
            assert secret not in out
    main(["doctor", str(example), "--json"])
    data = json.loads(capsys.readouterr().out)
    rel = next(f for f in data["findings"] if f["check"] == "signing.release")
    assert rel["severity"] == "ok"


@pytest.mark.parametrize("flag", ["--json", "--markdown"])
def test_cli_exit_codes(example: Path, toolchain, flag: str) -> None:  # type: ignore[no-untyped-def]
    toolchain()
    assert main(["doctor", str(example), flag]) == 0
    assert main(["doctor", str(example), flag, "--strict"]) == 1  # release keystore warning


def test_plaintext_password_in_presets(example: Path, toolchain) -> None:  # type: ignore[no-untyped-def]
    toolchain()
    _edit(example / "export_presets.cfg", "preset.0.options", {"keystore/release_password": "oops-plain"})
    subprocess.run(["git", "init", "-q", str(example)], check=True)
    subprocess.run(["git", "-C", str(example), "add", "export_presets.cfg"], check=True)
    findings = run_doctor(GodotProject.open(example))[0]
    f = _by_check(findings)["git.preset_secrets"][0]
    assert f.severity == Severity.ERROR and "oops-plain" not in f.detail + f.title


def test_keystore_path_from_export_credentials(example: Path, toolchain) -> None:  # type: ignore[no-untyped-def]
    toolchain()
    ks = example.parent / "vault.keystore"
    ks.write_bytes(b"x")
    (example / ".godot").mkdir()
    (example / ".godot" / "export_credentials.cfg").write_text(
        f'[preset.0.options]\n\nkeystore/release="{ks}"\nkeystore/release_user="u"\nkeystore/release_password="p"\n'
    )
    f = _by_check(run_doctor(GodotProject.open(example))[0])["signing.release"][0]
    assert f.severity == Severity.OK and "export_credentials" in f.detail
