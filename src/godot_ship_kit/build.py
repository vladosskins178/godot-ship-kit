"""``gsk build``: headless Android export with signing from the environment."""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from .env import GodotBinary
from .project import GodotProject, Preset

# Known export failures and what actually fixes them.
KNOWN_ERRORS: list[tuple[str, str]] = [
    (r"No export template found|export templates? .*not found", "Export templates are missing for this Godot version. Run `gsk doctor`."),
    (
        r"Android build template not installed|android/build.*(missing|not found)",
        "Install the Android build template: Project > Install Android Build Template.",
    ),
    (r"valid Java SDK path is required|Invalid Java SDK path", "Set the JDK: `gsk configure --java-sdk /path/to/jdk17`."),
    (
        r"valid Android SDK path is required|Invalid Android SDK path|Missing 'platform-tools'",
        "Set the SDK: `gsk configure --android-sdk /path/to/sdk`.",
    ),
    (r"Invalid package name|package name .*invalid", "Fix package/unique_name in the Android preset."),
    (
        r"requires 'ETC2/ASTC'|ETC2.*texture compression",
        "Enable Project Settings > Rendering > Textures > VRAM Compression > Import ETC2 ASTC.",
    ),
    (
        r"keystore.*(not found|missing|invalid)|Could not find (release |debug )?keystore|keystore incorrectly configured",
        "Check GODOT_ANDROID_KEYSTORE_RELEASE_PATH / _USER / _PASSWORD, or use --debug for a test build.",
    ),
    (
        r"Invalid filename! Android App Bundle requires the \*\.aab extension",
        "Use an .aab output path for AAB presets (or switch the preset to APK).",
    ),
    (r"Invalid filename! Android APK requires the \*\.apk extension", "Use an .apk output path for APK presets."),
    (r"Gradle build failed|BUILD FAILED", "Gradle failed. Scroll up in the log for the first 'error:' or 'What went wrong'."),
    (
        r"Trying to build from a gradle built template, but no version info",
        "Reinstall the Android build template (android/.build_version is missing).",
    ),
]


class BuildError(Exception):
    def __init__(self, message: str, log: Path | None = None, hints: list[str] | None = None) -> None:
        super().__init__(message)
        self.log = log
        self.hints = hints or []


@dataclass
class BuildResult:
    output: Path
    log: Path
    seconds: float
    command: list[str] = field(default_factory=list)


def preset_format(preset: Preset) -> str:
    gradle = bool(preset.opt("gradle_build/use_gradle_build", False))
    return "aab" if gradle and int(preset.opt("gradle_build/export_format", 0) or 0) == 1 else "apk"


def default_output(project: GodotProject, preset: Preset, release: bool) -> Path:
    raw = str(preset.values.get("export_path", "") or "")
    fmt = preset_format(preset)
    if raw:
        path = Path(raw)
        path = path if path.is_absolute() else (project.root / path)
        return path.with_suffix("." + fmt)
    version = str(preset.opt("version/name", "") or "").strip() or "dev"
    slug = re.sub(r"[^A-Za-z0-9]+", "-", project.name).strip("-").lower() or "game"
    return project.root / "build" / f"{slug}-{version}-{'release' if release else 'debug'}.{fmt}"


ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|[\x00-\x08\x0b-\x1f\x7f]")


def clean_log(text: str) -> str:
    """Strip terminal escapes and control characters, and mask signing values from the environment."""
    text = ANSI.sub("", text)
    for kind in ("RELEASE", "DEBUG"):
        for part in ("PASSWORD", "USER"):
            value = os.environ.get(f"GODOT_ANDROID_KEYSTORE_{kind}_{part}")
            if value and len(value) >= 3:
                text = text.replace(value, "<redacted>")
    return text


def release_keystore_source(project: GodotProject, preset: Preset) -> str | None:
    """Where the release keystore path comes from, or None if nothing is configured."""
    if os.environ.get("GODOT_ANDROID_KEYSTORE_RELEASE_PATH"):
        return "environment"
    if str(preset.opt("keystore/release", "") or "").strip():
        return "preset"
    creds = project.credentials.section(f"preset.{preset.index}.options")
    if str(creds.get("keystore/release", "") or "").strip():
        return "export credentials"
    return None


def signing_summary(release: bool, project: GodotProject | None = None, preset: Preset | None = None) -> str:
    kind = "RELEASE" if release else "DEBUG"
    have = [s for s in ("PATH", "USER", "PASSWORD") if os.environ.get(f"GODOT_ANDROID_KEYSTORE_{kind}_{s}")]
    if have:
        return f"{kind.lower()} keystore from environment ({', '.join(have)} set)"
    if not release:
        return "debug keystore from the preset or editor settings"
    source = release_keystore_source(project, preset) if project and preset else None
    return f"release keystore from the {source}" if source else "no release keystore configured"


def find_hints(log_text: str) -> list[str]:
    hints = []
    for pattern, hint in KNOWN_ERRORS:
        if re.search(pattern, log_text, re.I) and hint not in hints:
            hints.append(hint)
    return hints


def export(
    godot: GodotBinary,
    project: GodotProject,
    preset: Preset,
    output: Path,
    release: bool = True,
    timeout: int = 3600,
    install_template: bool = False,
) -> BuildResult:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    fmt = preset_format(preset)
    if output.suffix.lower() != "." + fmt:
        raise BuildError(
            f"Preset '{preset.name}' exports {fmt.upper()} but the output path ends in {output.suffix or '(nothing)'}.",
            hints=[f"Use an .{fmt} path, or change gradle_build/export_format in the preset."],
        )
    if release and preset.opt("package/signed", True) and not release_keystore_source(project, preset):
        raise BuildError(
            "No release keystore configured, so a release build cannot be signed.",
            hints=[
                "Set GODOT_ANDROID_KEYSTORE_RELEASE_PATH, _USER and _PASSWORD (see `gsk doctor`).",
                "Or build a test APK signed with the debug key: gsk build --debug",
            ],
        )
    log_dir = project.root / ".gsk" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    gi = project.root / ".gsk" / ".gitignore"
    if not gi.exists():
        gi.write_text("*\n", encoding="utf-8")
    log = log_dir / f"build-{time.strftime('%Y%m%d-%H%M%S')}.log"
    flag = "--export-release" if release else "--export-debug"
    cmd = [godot.path, "--headless", "--path", str(project.root)]
    if install_template:
        cmd.append("--install-android-build-template")
    cmd += [flag, preset.name, str(output)]

    before = output.stat().st_mtime if output.exists() else None
    start = time.time()
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout, text=True, errors="replace")
        code, raw = proc.returncode, proc.stdout or ""
    except subprocess.TimeoutExpired as exc:
        code, raw = -1, (exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else exc.stdout or "")
        raw += f"\n[gsk] export timed out after {timeout}s\n"
    elapsed = time.time() - start
    text = clean_log(raw)
    log.write_text("$ " + " ".join(cmd) + "\n\n" + text, encoding="utf-8")
    with contextlib.suppress(OSError):
        log.chmod(0o600)
    if code == -1:
        raise BuildError(f"Export timed out after {timeout}s", log)
    produced = output.exists() and (before is None or output.stat().st_mtime > before)
    # Godot sometimes exits 0 after a failed export, so the output file is the real signal.
    if code != 0 or not produced:
        raise BuildError(
            f"Export failed (exit code {code}{', no output file' if not produced else ''})",
            log,
            find_hints(text),
        )
    return BuildResult(output, log, elapsed, cmd)
