from __future__ import annotations

import os
import shutil
import stat
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "hello-ship"

FAKE_GODOT = """#!/bin/sh
# Stand-in for the Godot binary used by unit tests.
if [ "$1" = "--headless" ] && [ "$2" = "--version" ]; then
  echo "4.6.3.stable.official.0123456"
  exit 0
fi
out=""
prev=""
for a in "$@"; do
  case "$prev" in
    --export-release|--export-debug) preset="$a" ;;
  esac
  if [ -n "$preset" ] && [ "$prev" = "$preset" ]; then out="$a"; fi
  prev="$a"
done
echo "fake godot export to $out"
if [ -n "$FAKE_GODOT_ECHO" ]; then printf '\033[1;31mERROR:\033[0m keystore pass:%s\n' "$FAKE_GODOT_ECHO"; fi
if [ -n "$FAKE_GODOT_FAIL" ]; then
  echo "ERROR: No export template found at the expected path"
  exit 1
fi
python3 - "$out" <<'PY'
import sys, zipfile
with zipfile.ZipFile(sys.argv[1], "w") as z:
    z.writestr("lib/arm64-v8a/libgodot_android.so", b"\\0" * 4096)
    z.writestr("assets/.godot/imported/backdrop.jpg-abc.ctex", b"x" * 2048)
    z.writestr("classes.dex", b"dex")
PY
"""

FAKE_APKSIGNER = """#!/bin/sh
if [ -n "$FAKE_APKSIGNER_FAIL" ]; then echo "DOES NOT VERIFY" >&2; exit 1; fi
echo "Verified using v2 scheme (APK Signature Scheme v2): true"
echo "Signer #1 certificate DN: CN=Android Debug, O=Android, C=US"
echo "Signer #1 certificate SHA-256 digest: 00ff"
"""

FAKE_JAVA = """#!/bin/sh
echo 'openjdk version "17.0.12" 2024-07-16' >&2
"""

CONFIG_GRADLE = """ext.versions = [
    compileSdk         : 36,
    minSdk             : 24,
    targetSdk          : {target},
    buildTools         : '36.1.0',
]
"""


def _exe(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


@pytest.fixture
def example(tmp_path: Path) -> Path:
    """A throwaway copy of the sample project."""
    dst = tmp_path / "hello-ship"
    shutil.copytree(EXAMPLE, dst, ignore=shutil.ignore_patterns(".godot", ".gsk", "build"))
    return dst


@pytest.fixture
def toolchain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Hermetic Godot + Android SDK + JDK + templates, all fake."""

    def make(target_sdk: int = 36, with_templates: bool = True) -> dict[str, Path]:
        home = tmp_path / "home"
        data = home / ".local" / "share"
        config = home / ".config"
        godot = _exe(tmp_path / "bin" / "godot", FAKE_GODOT)
        sdk = tmp_path / "android-sdk"
        for d in ("platform-tools", "build-tools/36.1.0", "cmdline-tools/latest/bin", "platforms/android-36", "licenses"):
            (sdk / d).mkdir(parents=True, exist_ok=True)
        _exe(sdk / "platform-tools" / "adb", "#!/bin/sh\n")
        _exe(sdk / "build-tools" / "36.1.0" / "apksigner", FAKE_APKSIGNER)
        jdk = tmp_path / "jdk17"
        _exe(jdk / "bin" / "java", FAKE_JAVA)
        debug_ks = tmp_path / "debug.keystore"
        debug_ks.write_bytes(b"not a real keystore")
        if with_templates:
            tdir = data / "godot" / "export_templates" / "4.6.3.stable"
            tdir.mkdir(parents=True)
            with zipfile.ZipFile(tdir / "android_source.zip", "w") as z:
                z.writestr("config.gradle", CONFIG_GRADLE.format(target=target_sdk))
            (tdir / "android_release.apk").write_bytes(b"apk")
            (tdir / "android_debug.apk").write_bytes(b"apk")
        settings = config / "godot" / "editor_settings-4.6.tres"
        settings.parent.mkdir(parents=True)
        settings.write_text(
            '[gd_resource type="EditorSettings" format=3]\n\n[resource]\n'
            f'export/android/android_sdk_path = "{sdk}"\n'
            f'export/android/java_sdk_path = "{jdk}"\n'
            f'export/android/debug_keystore = "{debug_ks}"\n'
            'export/android/debug_keystore_pass = "android"\n',
            encoding="utf-8",
        )
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("XDG_DATA_HOME", str(data))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
        monkeypatch.setenv("GODOT", str(godot))
        for var in list(os.environ):
            if var.startswith("GODOT_ANDROID_KEYSTORE_") or var in ("ANDROID_HOME", "ANDROID_SDK_ROOT", "JAVA_HOME"):
                monkeypatch.delenv(var, raising=False)
        from godot_ship_kit import env

        env._godot_version.cache_clear()
        return {"godot": godot, "sdk": sdk, "jdk": jdk, "settings": settings, "home": home}

    return make


def add_fake_imports(project: Path, sizes: dict[str, int]) -> None:
    """Create .godot/imported files named in each .import so sizes are measurable."""
    from godot_ship_kit import godot_cfg

    for imp in project.rglob("*.import"):
        if ".godot" in imp.parts:
            continue
        cfg = godot_cfg.load(imp)
        for dest in godot_cfg.as_list(cfg.get("deps", "dest_files")):
            target = project / str(dest).removeprefix("res://")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"\0" * sizes.get(imp.with_suffix("").name, 1000))
