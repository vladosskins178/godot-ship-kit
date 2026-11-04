"""Find the Godot binary, export templates, editor settings, Android SDK and JDK."""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from . import godot_cfg

GODOT_CANDIDATES = ("godot", "godot4", "Godot", "godot-headless")


class ToolError(Exception):
    pass


@dataclass(frozen=True)
class GodotBinary:
    path: str
    version: str  # e.g. "4.6.3.stable.official.7d41c59c4"

    @property
    def short(self) -> str:
        """``4.6.3`` style version."""
        m = re.match(r"(\d+\.\d+(?:\.\d+)?)", self.version)
        return m.group(1) if m else self.version

    @property
    def minor(self) -> str:
        return ".".join(self.short.split(".")[:2])

    @property
    def template_dir_name(self) -> str:
        """Export templates live in a folder named like ``4.6.3.stable`` (``4.6.3.stable.mono`` for .NET)."""
        m = re.match(r"(\d+\.\d+(?:\.\d+)?\.[a-z0-9]+)", self.version)
        name = m.group(1) if m else self.version
        return name + ".mono" if self.is_mono else name

    @property
    def settings_file_name(self) -> str:
        """Godot 4.0-4.2 share ``editor_settings-4.tres``; 4.3+ use one file per minor version."""
        major, minor = (int(x) for x in self.minor.split("."))
        return f"editor_settings-{major}.{minor}.tres" if (major, minor) >= (4, 3) else f"editor_settings-{major}.tres"

    @property
    def is_mono(self) -> bool:
        return ".mono" in self.version


def find_godot(explicit: str | None = None) -> GodotBinary | None:
    """Locate a Godot 4 binary: ``--godot``, then ``$GODOT``, then ``PATH``."""
    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)
    for var in ("GODOT", "GODOT_BIN"):
        if os.environ.get(var):
            candidates.append(os.environ[var])
    candidates += [c for c in (shutil.which(n) for n in GODOT_CANDIDATES) if c]
    for cand in candidates:
        resolved = shutil.which(cand) or (cand if Path(cand).is_file() else None)
        if not resolved:
            continue
        version = _godot_version(resolved)
        if version:
            return GodotBinary(resolved, version)
    return None


@lru_cache(maxsize=8)
def _godot_version(path: str) -> str | None:
    try:
        out = subprocess.run(
            [path, "--headless", "--version"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in reversed(out.stdout.strip().splitlines()):
        if re.match(r"\d+\.\d+", line.strip()):
            return line.strip()
    return None


def _user_data_dir() -> Path:
    system = platform.system()
    home = Path.home()
    if system == "Windows":
        return Path(os.environ.get("APPDATA", home / "AppData" / "Roaming")) / "Godot"
    if system == "Darwin":
        return home / "Library" / "Application Support" / "Godot"
    return Path(os.environ.get("XDG_DATA_HOME", home / ".local" / "share")) / "godot"


def _user_config_dir() -> Path:
    system = platform.system()
    if system in ("Windows", "Darwin"):
        return _user_data_dir()
    home = Path.home()
    return Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")) / "godot"


def templates_root(godot: GodotBinary | None = None) -> Path:
    """Export templates folder. Self-contained installs keep it next to the binary."""
    if godot:
        sc = Path(godot.path).parent
        if (sc / "._sc_").exists() or (sc / "_sc_").exists():
            return sc / "editor_data" / "export_templates"
    return _user_data_dir() / "export_templates"


def editor_settings_path(godot: GodotBinary | None = None) -> Path | None:
    """The ``editor_settings-4.x.tres`` file the given Godot reads."""
    base = _user_config_dir()
    if godot:
        sc = Path(godot.path).parent
        if (sc / "._sc_").exists() or (sc / "_sc_").exists():
            base = sc / "editor_data"
    if not base.is_dir():
        return None
    if godot:
        exact = base / godot.settings_file_name
        return exact if exact.is_file() else None
    found = sorted(base.glob("editor_settings-*.tres"), key=lambda p: p.stat().st_mtime, reverse=True)
    return found[0] if found else None


@dataclass
class AndroidToolchain:
    settings_file: Path | None
    sdk: Path | None
    sdk_source: str
    jdk: Path | None
    jdk_source: str
    debug_keystore: Path | None

    def build_tools(self) -> list[Path]:
        if not self.sdk:
            return []
        bt = self.sdk / "build-tools"
        if not bt.is_dir():
            return []
        return sorted((p for p in bt.iterdir() if p.is_dir()), key=lambda p: _version_key(p.name))

    def latest_build_tool(self, name: str) -> Path | None:
        suffix = ".bat" if platform.system() == "Windows" and name in ("apksigner",) else ""
        exe = ".exe" if platform.system() == "Windows" and name in ("zipalign", "aapt2") else ""
        for folder in reversed(self.build_tools()):
            cand = folder / f"{name}{suffix}{exe}"
            if cand.is_file():
                return cand
        found = shutil.which(name)
        return Path(found) if found else None

    def java(self) -> Path | None:
        if self.jdk:
            exe = self.jdk / "bin" / ("java.exe" if platform.system() == "Windows" else "java")
            if exe.is_file():
                return exe
        return None


def _version_key(name: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", name)) or (0,)


def android_toolchain(godot: GodotBinary | None = None) -> AndroidToolchain:
    """Resolve SDK/JDK the way the Godot editor would.

    Godot reads ``export/android/android_sdk_path`` and ``java_sdk_path`` from
    the editor settings. When those are empty it seeds them from
    ``ANDROID_HOME`` / ``ANDROID_SDK_ROOT`` and ``JAVA_HOME``.
    """
    path = editor_settings_path(godot)
    res: dict[str, object] = {}
    if path:
        try:
            res = godot_cfg.load(path).section("resource")
        except OSError:
            res = {}

    def pick(key: str, envs: tuple[str, ...]) -> tuple[Path | None, str]:
        val = str(res.get(key) or "")
        if val:
            return Path(val).expanduser(), f"editor settings ({key})"
        for env in envs:
            if os.environ.get(env):
                return Path(os.environ[env]).expanduser(), f"${env}"
        return None, "not set"

    sdk, sdk_src = pick("export/android/android_sdk_path", ("ANDROID_HOME", "ANDROID_SDK_ROOT"))
    jdk, jdk_src = pick("export/android/java_sdk_path", ("JAVA_HOME",))
    dks = str(res.get("export/android/debug_keystore") or "")
    return AndroidToolchain(path, sdk, sdk_src, jdk, jdk_src, Path(dks).expanduser() if dks else None)


def java_major(java: Path) -> int | None:
    try:
        out = subprocess.run([str(java), "-version"], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r'version "(\d+)(?:\.(\d+))?', out.stderr + out.stdout)
    if not m:
        return None
    major = int(m.group(1))
    return int(m.group(2) or 0) if major == 1 else major


def write_editor_settings(path: Path, values: dict[str, str]) -> None:
    """Set Android-related editor settings, creating the file if needed."""
    if path.is_file():
        with path.open(encoding="utf-8", newline="") as fh:
            text = fh.read()
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = '[gd_resource type="EditorSettings" format=3]\n\n[resource]\n'
    with path.open("w", encoding="utf-8", newline="") as fh:
        fh.write(godot_cfg.set_values(text, "resource", dict(values)))
