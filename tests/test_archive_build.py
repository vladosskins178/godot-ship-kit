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
    with pytest.raises(BuildError, match="No release keystore") as exc:
        export(godot, project, preset, example / "build" / "r.apk")  # release without a keystore
    assert any("--debug" in h for h in exc.value.hints)
    monkeypatch.setenv("FAKE_GODOT_FAIL", "1")
    with pytest.raises(BuildError) as exc:
        export(godot, project, preset, example / "build" / "y.apk", release=False)
    assert any("templates" in h for h in exc.value.hints)


def test_cli_build_and_verify(example: Path, toolchain, capsys, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    toolchain()
    assert main(["build", str(example), "--debug"]) == 0
    out = capsys.readouterr().out
    assert "Archive integrity OK" in out and "native libraries" in out
    apk = example / "build" / "hello-ship-debug.apk"
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
    assert main(["build", str(example), "--debug"]) == 1
    out = capsys.readouterr().out
    assert "Export failed" in out and "hint:" in out


def test_release_build_with_env_keystore_and_redacted_log(example: Path, toolchain, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    toolchain()
    ks = example.parent / "upload.keystore"
    ks.write_bytes(b"x")
    monkeypatch.setenv("GODOT_ANDROID_KEYSTORE_RELEASE_PATH", str(ks))
    monkeypatch.setenv("GODOT_ANDROID_KEYSTORE_RELEASE_USER", "upload")
    monkeypatch.setenv("GODOT_ANDROID_KEYSTORE_RELEASE_PASSWORD", "s3cret-pass")
    monkeypatch.setenv("FAKE_GODOT_ECHO", "s3cret-pass")
    assert main(["build", str(example), "--no-verify"]) == 0
    out = capsys.readouterr().out
    assert "release keystore from environment" in out
    log = next((example / ".gsk" / "logs").glob("build-*.log"))
    assert "s3cret-pass" not in log.read_text() and "<redacted>" in log.read_text()
    assert oct(log.stat().st_mode & 0o777) == "0o600"


def test_aab_requires_gradle_in_cli(example: Path, toolchain, capsys) -> None:  # type: ignore[no-untyped-def]
    toolchain()
    cfg = example / "export_presets.cfg"
    cfg.write_text(
        set_values(cfg.read_text(), "preset.0.options", {"gradle_build/use_gradle_build": True, "gradle_build/export_format": 1})
    )
    assert main(["build", str(example), "-o", str(example / "out.apk")]) == 1
    assert "exports AAB" in capsys.readouterr().out


def _elf64(p_align: int) -> bytes:
    import struct

    phoff, phentsize = 64, 56
    header = bytearray(64)
    header[:4] = b"\x7fELF"
    header[4], header[5] = 2, 1  # 64-bit, little endian
    struct.pack_into("<Q", header, 0x20, phoff)
    struct.pack_into("<HH", header, 0x36, phentsize, 2)
    ph = bytearray(phentsize * 2)
    struct.pack_into("<I", ph, 0, 1)  # PT_LOAD
    struct.pack_into("<Q", ph, 0x30, p_align)
    struct.pack_into("<I", ph, phentsize, 6)  # PT_PHDR, ignored
    return bytes(header + ph)


def test_elf_page_alignment(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("PATH", "/nonexistent")
    assert archive.elf_load_alignment(_elf64(0x4000)) == 0x4000
    assert archive.elf_load_alignment(b"not an elf" * 10) is None
    for align, expected in ((0x4000, Severity.OK), (0x1000, Severity.WARN)):
        apk = tmp_path / f"a{align}.apk"
        with zipfile.ZipFile(apk, "w") as z:
            z.writestr("lib/arm64-v8a/libgodot_android.so", _elf64(align))
        assert _checks(archive.inspect(apk))["archive.page_size"] == expected


def test_tool_output_parsing(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from .conftest import _exe

    bt = tmp_path / "sdk" / "build-tools" / "35.0.0"
    _exe(
        bt / "aapt2",
        "#!/bin/sh\necho \"package: name='com.x.game' versionCode='7' versionName='1.2'\"\n"
        "echo \"sdkVersion:'24'\"\necho \"targetSdkVersion:'34'\"\n"
        "echo \"uses-permission: name='android.permission.INTERNET'\"\necho 'application-debuggable'\n",
    )
    _exe(bt / "zipalign", "#!/bin/sh\necho '  lib/arm64-v8a/libx.so (BAD - 4096)'\nexit 1\n")
    _exe(bt / "apksigner", "#!/bin/sh\nexit 0\n")
    from godot_ship_kit.env import AndroidToolchain

    tc = AndroidToolchain(None, tmp_path / "sdk", "test", None, "test", None)
    rep = archive.inspect(_apk(tmp_path / "t.apk"), tc, expect_release=True)
    c = _checks(rep)
    assert rep.manifest["package"] == "com.x.game" and rep.manifest["version_code"] == 7
    assert rep.manifest["permissions"] == ["INTERNET"] and rep.manifest["debuggable"] is True
    assert c["archive.target_sdk"] == Severity.ERROR
    assert c["archive.debuggable"] == Severity.ERROR
    assert c["archive.align"] == Severity.WARN


def test_aab_with_bundletool(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from .conftest import _exe

    tool = _exe(
        tmp_path / "bundletool",
        '#!/bin/sh\ncase "$1" in\n  validate) exit 0 ;;\n'
        '  dump) echo \'<manifest package="com.x.game" android:versionCode="3"><uses-sdk android:targetSdkVersion="36"/></manifest>\' ;;\n'
        "esac\n",
    )
    monkeypatch.setenv("BUNDLETOOL", str(tool))
    aab = tmp_path / "g.aab"
    with zipfile.ZipFile(aab, "w") as z:
        z.writestr("base/lib/arm64-v8a/libgodot_android.so", _elf64(0x4000))
        z.writestr("base/assets/x.bin", b"\0" * 1000)
        z.writestr("BundleConfig.pb", b"")
    rep = archive.inspect(aab)
    c = _checks(rep)
    assert c["archive.bundletool"] == Severity.OK and c["archive.target_sdk"] == Severity.OK
    assert rep.manifest["package"] == "com.x.game"
    assert any(f.check == "archive.size" and "Base module" in f.title for f in rep.findings)
