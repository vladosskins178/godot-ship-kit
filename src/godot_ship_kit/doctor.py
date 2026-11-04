"""``gsk doctor``: everything that stands between a Godot project and a Play upload."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import playreq
from .build import export_format
from .env import AndroidToolchain, GodotBinary, android_toolchain, find_godot, java_major, templates_root
from .godot_cfg import as_list
from .project import GodotProject, Preset, image_size
from .report import Finding, Severity, human_bytes
from .slim import is_normal_map_name

F = Finding
S = Severity

PACKAGE_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]*(\.[a-zA-Z][a-zA-Z0-9_]*)+$")
HEAVY_ENV_KEYS = {
    "sdfgi_enabled": "SDFGI",
    "ssr_enabled": "screen-space reflections",
    "ssil_enabled": "SSIL",
    "ssao_enabled": "SSAO",
    "volumetric_fog_enabled": "volumetric fog",
}


@dataclass
class DoctorContext:
    project: GodotProject
    godot: GodotBinary | None
    preset: Preset | None
    toolchain: AndroidToolchain
    template_sdks: dict[str, Any] = field(default_factory=dict)
    tracked_files: set[str] | None = None


def run_doctor(
    project: GodotProject,
    godot_path: str | None = None,
    preset_name: str | None = None,
) -> tuple[list[Finding], DoctorContext]:
    godot = find_godot(godot_path)
    preset = project.find_preset(preset_name)
    ctx = DoctorContext(project, godot, preset, android_toolchain(godot))
    ctx.template_sdks = _template_sdks(ctx)
    ctx.tracked_files = _git_tracked(project.root)

    findings: list[Finding] = []
    for check in (
        _check_project,
        _check_godot,
        _check_preset,
        _check_target_sdk,
        _check_permissions,
        _check_icons,
        _check_signing,
        _check_git_hygiene,
        _check_plaintext_secrets,
        _check_sdk,
        _check_jdk,
        _check_renderer,
        _check_display,
        _check_textures,
        _check_audio,
        _check_size,
    ):
        findings += check(ctx, preset_name)
    return findings, ctx


# ---------------------------------------------------------------- project


def _check_project(ctx: DoctorContext, _: str | None) -> list[Finding]:
    p = ctx.project
    out = [
        F(
            "project",
            S.OK,
            f"Project '{p.name}' (Godot {p.godot_version or '?'}, {', '.join(p.features[1:]) or 'no features'})",
            data={"root": str(p.root)},
        )
    ]
    if p.config.get("", "config_version") not in (None, 5) or (p.godot_version or "4").startswith("3"):
        return [
            F(
                "project.version",
                S.ERROR,
                "This looks like a Godot 3 project",
                "gsk supports Godot 4 projects (config_version=5 in project.godot).",
                "Open the project in Godot 4 to convert it (Project > Tools > Upgrade Project Files).",
            )
        ]
    if not p.main_scene:
        out.append(
            F(
                "project.main_scene",
                S.ERROR,
                "No main scene set",
                "The exported app would start on an empty screen.",
                "Project Settings > Application > Run > Main Scene.",
            )
        )
    else:
        resolved = p.main_scene_res or p.main_scene
        if resolved.startswith("uid://") or not p.res_to_path(resolved).exists():
            out.append(F("project.main_scene", S.ERROR, f"Main scene {p.main_scene} does not exist"))
    return out


def _check_godot(ctx: DoctorContext, _: str | None) -> list[Finding]:
    g = ctx.godot
    if not g:
        return [
            F(
                "godot.binary",
                S.ERROR,
                "Godot binary not found",
                "Looked at --godot, $GODOT and godot/godot4 on PATH.",
                "Install Godot 4 and set GODOT=/path/to/godot (headless builds work fine).",
                "https://godotengine.org/download",
            )
        ]
    out = [F("godot.binary", S.OK, f"Godot {g.version} at {g.path}")]
    pv = ctx.project.godot_version
    if pv and pv != g.minor:
        out.append(
            F(
                "godot.version",
                S.WARN,
                f"Project was saved with Godot {pv}, but the binary is {g.minor}",
                "Exporting with a different minor version re-imports everything and may change behaviour.",
                f"Use a Godot {pv}.x binary, or open and save the project in {g.minor} on purpose.",
            )
        )
    tdir = templates_root(g) / g.template_dir_name
    missing = [n for n in ("android_source.zip", "android_release.apk", "android_debug.apk") if not (tdir / n).is_file()]
    if missing:
        out.append(
            F(
                "godot.templates",
                S.ERROR,
                f"Android export templates for {g.template_dir_name} are missing",
                f"Expected in {tdir}: {', '.join(missing)}",
                "Editor > Manage Export Templates > Download, or unpack "
                f"Godot_v{g.short}-stable{'_mono' if g.is_mono else ''}_export_templates.tpz into that folder.",
                "https://godotengine.org/download/archive/",
            )
        )
    else:
        out.append(F("godot.templates", S.OK, f"Export templates {g.template_dir_name} installed"))
    uses_csharp = any(ctx.project.root.glob("*.csproj"))
    if uses_csharp and not g.is_mono:
        out.append(
            F(
                "godot.dotnet",
                S.ERROR,
                "The project uses C# but this Godot binary is not the .NET build",
                fix="Use the Godot .NET (mono) binary and its export templates.",
            )
        )
    elif g.is_mono or uses_csharp:
        ok = shutil.which("dotnet") is not None
        out.append(
            F(
                "godot.dotnet",
                S.OK if ok else S.ERROR,
                ".NET SDK found" if ok else ".NET SDK (dotnet) not found",
                fix="" if ok else "Install the .NET SDK version your Godot release requires (see the Godot C# docs).",
                docs="" if ok else "https://docs.godotengine.org/en/stable/tutorials/scripting/c_sharp/c_sharp_basics.html",
            )
        )
    return out


# ---------------------------------------------------------------- preset


def _check_preset(ctx: DoctorContext, preset_name: str | None) -> list[Finding]:
    p = ctx.preset
    if not p:
        where = f"named '{preset_name}'" if preset_name else "for Android"
        return [
            F(
                "preset.android",
                S.ERROR,
                f"No export preset {where} in export_presets.cfg",
                fix="Project > Export > Add... > Android, then save. Commit export_presets.cfg.",
                docs=playreq.GODOT_ANDROID_DOCS,
            )
        ]
    out = [F("preset.android", S.OK, f"Android preset '{p.name}'")]

    pkg = str(p.opt("package/unique_name", ""))
    resolved = pkg.replace("$genname", re.sub(r"[^a-zA-Z0-9_]", "", ctx.project.name) or "game")
    if not pkg or "com.example" in pkg or "$genname" in pkg or not PACKAGE_RE.match(resolved):
        out.append(
            F(
                "preset.package",
                S.ERROR,
                f"Package name '{pkg or '(empty)'}' cannot go to Play",
                "Play rejects com.example.* and the name can never change after the first upload.",
                "Set package/unique_name to a reverse-domain id you own, e.g. com.yourstudio.yourgame.",
            )
        )
    else:
        out.append(F("preset.package", S.OK, f"Package name {pkg}"))

    code = p.opt("version/code", 1)
    name = str(p.opt("version/name", "") or "")
    if not isinstance(code, int) or code < 1:
        out.append(F("preset.version", S.ERROR, f"version/code '{code}' must be a positive integer"))
    else:
        out.append(
            F(
                "preset.version",
                S.OK if name else S.WARN,
                f"Version code {code}, name '{name or '(empty)'}'",
                fix="" if name else "Set version/name (shown on the store page), e.g. 1.0.0.",
            )
        )
        out[-1].data["note"] = "Every Play upload needs a higher version/code than the last one."

    archs = {a: bool(p.opt(f"architectures/{a}", a == "arm64-v8a")) for a in ("armeabi-v7a", "arm64-v8a", "x86", "x86_64")}
    if not archs["arm64-v8a"]:
        out.append(
            F(
                "preset.arch",
                S.ERROR,
                "arm64-v8a is disabled",
                "Play requires 64-bit native code.",
                "Enable architectures/arm64-v8a in the preset.",
            )
        )
    else:
        extra = [a for a, on in archs.items() if on and a != "arm64-v8a"]
        if extra:
            out.append(
                F(
                    "preset.arch",
                    S.INFO,
                    f"Extra architectures enabled: {', '.join(extra)}",
                    "Each one adds a full copy of the engine library (roughly 10-25 MB) to an APK. "
                    "An AAB splits them per device, so this matters mostly for APK builds.",
                    "Keep only arm64-v8a unless you target old 32-bit phones or emulators.",
                )
            )
        else:
            out.append(F("preset.arch", S.OK, "Architectures: arm64-v8a only"))

    gradle = bool(p.opt("gradle_build/use_gradle_build", False))
    fmt = export_format(p)
    if fmt == 1 and not gradle:
        out.append(
            F(
                "preset.format",
                S.ERROR,
                "AAB export is selected but Gradle build is off",
                fix="Enable gradle_build/use_gradle_build (AAB needs the Gradle build template).",
            )
        )
    elif fmt == 0:
        out.append(
            F(
                "preset.format",
                S.INFO,
                "Preset exports an APK",
                "New Play apps must be uploaded as an Android App Bundle (AAB). APKs are fine for testing.",
                "For the store: enable Gradle build and set gradle_build/export_format to AAB.",
                playreq.AAB_REQUIRED_URL,
            )
        )
    else:
        out.append(F("preset.format", S.OK, "Preset exports an AAB"))

    if gradle:
        build_dir = ctx.project.root / "android" / "build"
        version_file = ctx.project.root / "android" / ".build_version"
        if not build_dir.is_dir():
            out.append(
                F(
                    "preset.gradle_template",
                    S.ERROR,
                    "Gradle build is on but the Android build template is not installed",
                    "android/build/ is missing.",
                    "Run `gsk build --install-template` (passes --install-android-build-template to Godot), "
                    "or Project > Install Android Build Template in the editor.",
                )
            )
        elif ctx.godot and version_file.is_file():
            installed = version_file.read_text(encoding="utf-8").strip()
            if installed and installed != ctx.godot.template_dir_name:
                out.append(
                    F(
                        "preset.gradle_template",
                        S.WARN,
                        f"Android build template is {installed}, Godot is {ctx.godot.template_dir_name}",
                        "A stale template can fail the Gradle build or ship old engine code.",
                        "Delete android/ and reinstall the build template from the editor.",
                    )
                )
            else:
                out.append(F("preset.gradle_template", S.OK, f"Android build template {installed}"))
    return out


def _template_sdks(ctx: DoctorContext) -> dict[str, Any]:
    """Default min/target/compile SDK from the build template actually in use."""
    sources: list[tuple[str, str]] = []
    cfg = ctx.project.root / "android" / "build" / "config.gradle"
    if ctx.preset and ctx.preset.opt("gradle_build/use_gradle_build") and cfg.is_file():
        sources.append((cfg.read_text(encoding="utf-8", errors="replace"), str(cfg)))
    if ctx.godot:
        zpath = templates_root(ctx.godot) / ctx.godot.template_dir_name / "android_source.zip"
        if zpath.is_file():
            try:
                with zipfile.ZipFile(zpath) as zf:
                    sources.append((zf.read("config.gradle").decode("utf-8", "replace"), f"{zpath}:config.gradle"))
            except (KeyError, zipfile.BadZipFile, OSError):
                pass
    for text, src in sources:
        vals: dict[str, Any] = {
            k: int(m.group(1)) for k in ("minSdk", "targetSdk", "compileSdk") if (m := re.search(rf"\b{k}\s*:\s*(\d+)", text))
        }
        if "targetSdk" in vals:
            vals["source"] = src
            return vals
    if ctx.godot and (known := playreq.godot_template_sdks(ctx.godot.short)):
        return {"minSdk": known[0], "targetSdk": known[1], "source": f"built-in table for Godot {ctx.godot.short}"}
    return {}


def _check_target_sdk(ctx: DoctorContext, _: str | None) -> list[Finding]:
    p = ctx.preset
    if not p:
        return []
    gradle = bool(p.opt("gradle_build/use_gradle_build", False))
    override = str(p.opt("gradle_build/target_sdk", "") or "").strip()
    default = ctx.template_sdks.get("targetSdk")
    if gradle and override.isdigit():
        target, origin = int(override), "preset gradle_build/target_sdk"
    elif default:
        target, origin = int(default), f"Godot template default ({ctx.template_sdks.get('source')})"
    else:
        return [
            F(
                "android.target_sdk",
                S.WARN,
                "Could not determine the target SDK",
                "Install the export templates so gsk can read the template defaults.",
            )
        ]
    req = playreq.PLAY_TARGET_SDK
    detail = f"Source: {origin}. Play requirement checked {playreq.CHECKED}."
    if target < req:
        fix = (
            f"Set gradle_build/target_sdk to {req} (Gradle build), or update Godot to a release whose template "
            f"targets API {req} (Godot 4.6.3+)."
        )
        if not gradle:
            fix = f"Enable Gradle build and set gradle_build/target_sdk={req}, or update to Godot 4.6.3+."
        return [
            F(
                "android.target_sdk",
                S.ERROR,
                f"Target SDK {target} is below Play's requirement (API {req} since {playreq.PLAY_TARGET_SDK_SINCE})",
                detail,
                fix,
                playreq.PLAY_TARGET_SDK_URL,
            )
        ]
    out = [F("android.target_sdk", S.OK, f"Target SDK {target} meets Play's API {req} requirement", detail)]
    min_override = str(p.opt("gradle_build/min_sdk", "") or "").strip()
    min_sdk = int(min_override) if gradle and min_override.isdigit() else ctx.template_sdks.get("minSdk")
    if min_sdk:
        out.append(F("android.min_sdk", S.OK, f"Min SDK {min_sdk} (Android {_android_name(int(min_sdk))}+)"))
    return out


def _android_name(api: int) -> str:
    names = {
        21: "5.0",
        22: "5.1",
        23: "6",
        24: "7.0",
        25: "7.1",
        26: "8.0",
        27: "8.1",
        28: "9",
        29: "10",
        30: "11",
        31: "12",
        32: "12L",
        33: "13",
        34: "14",
        35: "15",
        36: "16",
    }
    return names.get(api, f"API {api}")


def _check_permissions(ctx: DoctorContext, _: str | None) -> list[Finding]:
    p = ctx.preset
    if not p:
        return []
    enabled = sorted(k.split("/", 1)[1] for k, v in p.options.items() if k.startswith("permissions/") and v is True)
    custom = [str(x) for x in as_list(p.opt("permissions/custom_permissions")) if x]
    out: list[Finding] = []
    sensitive = [(perm, playreq.SENSITIVE_PERMISSIONS[perm]) for perm in enabled if perm in playreq.SENSITIVE_PERMISSIONS]
    if sensitive:
        out.append(
            F(
                "preset.permissions",
                S.WARN,
                f"{len(sensitive)} permission(s) need a justification on Play",
                "\n".join(f"{perm}: {why}" for perm, why in sensitive),
                "Turn off what the game does not use. Declare the rest in Play Console > App content.",
                playreq.DATA_SAFETY_URL,
            )
        )
    needs_net = _uses_network(ctx.project)
    if needs_net and "internet" not in enabled:
        # Android plugins (AARs) usually declare INTERNET in their own manifest, which
        # Gradle merges in. Only plain engine networking is a sure miss.
        from_plugin = needs_net.startswith("addons/") and bool(p.opt("gradle_build/use_gradle_build", False))
        out.append(
            F(
                "preset.internet",
                S.INFO if from_plugin else S.WARN,
                "The project seems to use the network but the INTERNET permission is off in the preset",
                f"Found: {needs_net}. Ads, analytics, leaderboards and HTTPRequest fail silently without it."
                + (" The plugin's own manifest may already add it." if from_plugin else ""),
                "Enable permissions/internet, or confirm with `gsk verify` that the built APK requests it.",
            )
        )
    if not out:
        listed = ", ".join(enabled + custom) or "none"
        out.append(F("preset.permissions", S.OK, f"Permissions: {listed}"))
    return out


def _uses_network(project: GodotProject) -> str:
    addons = project.root / "addons"
    if addons.is_dir():
        for d in addons.iterdir():
            if re.search(r"admob|ads|firebase|analytics|playgames|play_games|billing|gameanalytics|appodeal|applovin", d.name, re.I):
                return f"addons/{d.name}"
    for path in project.walk():
        if path.suffix in (".gd", ".tscn") and path.stat().st_size < 2_000_000:
            text = path.read_text(encoding="utf-8", errors="replace")
            m = re.search(r"\b(HTTPRequest|HTTPClient|WebSocketPeer|ENetMultiplayerPeer|StreamPeerTCP)\b", text)
            if m:
                return f"{m.group(1)} in {path.relative_to(project.root)}"
    return ""


def _check_icons(ctx: DoctorContext, _: str | None) -> list[Finding]:
    p = ctx.preset
    if not p:
        return []
    main = str(p.opt("launcher_icons/main_192x192", "") or "")
    fg = str(p.opt("launcher_icons/adaptive_foreground_432x432", "") or "")
    project_icon = str(ctx.project.setting("application/config/icon", "") or "")
    if not main and not fg:
        if not project_icon or project_icon.endswith("icon.svg"):
            return [
                F(
                    "preset.icons",
                    S.WARN,
                    "Launcher icon is the default Godot logo",
                    fix="Set launcher_icons/main_192x192 and the adaptive foreground/background (432x432) in the preset.",
                )
            ]
        return [
            F(
                "preset.icons",
                S.INFO,
                f"No dedicated launcher icons; Godot will scale {project_icon}",
                fix="Provide launcher_icons/adaptive_foreground_432x432 so Android 8+ masks it cleanly.",
            )
        ]
    return [F("preset.icons", S.OK, "Launcher icons set")]


# ---------------------------------------------------------------- signing


def _secret_state(ctx: DoctorContext, kind: str) -> tuple[str, str, bool, str]:
    """Return (path, source, has_password, user_state) without revealing secrets."""
    p = ctx.preset
    up = kind.upper()
    path = os.environ.get(f"GODOT_ANDROID_KEYSTORE_{up}_PATH", "")
    source = f"$GODOT_ANDROID_KEYSTORE_{up}_PATH" if path else ""
    creds = ctx.project.credentials.section(f"preset.{p.index}.options") if p else {}
    if not path and p:
        path = str(p.opt(f"keystore/{kind}", "") or "")
        source = f"preset keystore/{kind}" if path else ""
    if not path and creds.get(f"keystore/{kind}"):
        path, source = str(creds[f"keystore/{kind}"]), ".godot/export_credentials.cfg"
    if not path and kind == "debug" and ctx.toolchain.debug_keystore:
        path, source = str(ctx.toolchain.debug_keystore), "editor settings"
    has_pw = bool(
        os.environ.get(f"GODOT_ANDROID_KEYSTORE_{up}_PASSWORD")
        or creds.get(f"keystore/{kind}_password")
        or (p.opt(f"keystore/{kind}_password") if p else "")
    )
    if kind == "debug" and not has_pw and source == "editor settings":
        has_pw = True  # editor settings carry the debug password
    user = (
        os.environ.get(f"GODOT_ANDROID_KEYSTORE_{up}_USER")
        or creds.get(f"keystore/{kind}_user")
        or (p.opt(f"keystore/{kind}_user") if p else "")
    )
    return path, source, has_pw, "set" if user else "not set"


def _check_signing(ctx: DoctorContext, _: str | None) -> list[Finding]:
    if not ctx.preset:
        return []
    out: list[Finding] = []
    path, source, has_pw, user = _secret_state(ctx, "release")
    if not path:
        out.append(
            F(
                "signing.release",
                S.WARN,
                "No release keystore configured",
                "Release builds cannot be signed. (Debug builds still work.)",
                "Export GODOT_ANDROID_KEYSTORE_RELEASE_PATH, _USER and _PASSWORD, or fill keystore/release in the preset.\n"
                "Create one: keytool -genkeypair -v -keystore release.keystore -alias upload -keyalg RSA -keysize 2048 -validity 10000",
                playreq.GODOT_ANDROID_DOCS,
            )
        )
    else:
        kpath = Path(path).expanduser()
        if not kpath.is_absolute():
            kpath = ctx.project.root / kpath
        if not kpath.is_file():
            out.append(F("signing.release", S.ERROR, f"Release keystore not found ({source})", f"Path: {kpath}"))
        else:
            missing = [what for what, ok in (("password", has_pw), ("alias", user == "set")) if not ok]
            sev = S.WARN if missing else S.OK
            out.append(
                F(
                    "signing.release",
                    sev,
                    "Release keystore found" + (f", but no {' or '.join(missing)} configured" if missing else ""),
                    f"From {source}. Password: {'set' if has_pw else 'missing'}. Alias: {user}. (Values are never printed.)",
                    "Provide GODOT_ANDROID_KEYSTORE_RELEASE_USER / _PASSWORD as CI secrets." if missing else "",
                )
            )
            try:
                inside = kpath.resolve().is_relative_to(ctx.project.root.resolve())
            except OSError:
                inside = False
            if inside:
                rel = kpath.resolve().relative_to(ctx.project.root.resolve()).as_posix()
                tracked = ctx.tracked_files is not None and rel in ctx.tracked_files
                out.append(
                    F(
                        "signing.location",
                        S.ERROR if tracked else S.WARN,
                        "Release keystore is " + ("committed to git" if tracked else "inside the project folder"),
                        f"{rel} "
                        + ("is in the git index." if tracked else "will be visible to anyone with the repo if it gets committed."),
                        "Move it outside the repo (and out of the export) and back it up offline. If it was pushed, "
                        "treat it as leaked: rotate the upload key in Play Console.",
                    )
                )
    dpath, dsource, _has_pw, _user = _secret_state(ctx, "debug")
    if dpath and Path(dpath).expanduser().is_file():
        out.append(F("signing.debug", S.OK, f"Debug keystore found ({dsource})"))
    else:
        out.append(
            F(
                "signing.debug",
                S.WARN,
                "No debug keystore found",
                "Debug exports need one.",
                "keytool -keyalg RSA -genkeypair -alias androiddebugkey -keypass android -keystore debug.keystore "
                '-storepass android -dname "CN=Android Debug,O=Android,C=US" -validity 9999 -deststoretype pkcs12\n'
                "then set export/android/debug_keystore in editor settings or GODOT_ANDROID_KEYSTORE_DEBUG_PATH.",
            )
        )
    return out


def _git_tracked(root: Path) -> set[str] | None:
    try:
        top = subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "-C", str(root), "rev-parse", "--show-toplevel"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=20,
        )
        if top.returncode != 0:
            return None
        files = subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "-C", str(root), "ls-files", "--full-name", "-z"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    prefix = root.resolve().relative_to(Path(top.stdout.strip()).resolve()).as_posix()
    out: set[str] = set()
    for f in files.stdout.split("\0"):
        if not f:
            continue
        if prefix in ("", "."):
            out.add(f)
        elif f.startswith(prefix + "/"):
            out.add(f[len(prefix) + 1 :])
    return out


def _check_git_hygiene(ctx: DoctorContext, _: str | None) -> list[Finding]:
    tracked = ctx.tracked_files
    if tracked is None:
        return []
    out: list[Finding] = []
    leaked = sorted(f for f in tracked if f.endswith((".keystore", ".jks", "export_credentials.cfg")) and Path(f).name != "debug.keystore")
    if leaked:
        out.append(
            F(
                "git.secrets",
                S.ERROR,
                "Signing secrets are committed to git",
                "\n".join(leaked),
                "git rm --cached <file>, add it to .gitignore, and rotate the key if the repo was ever shared.",
            )
        )
    if any(f.startswith(".godot/") for f in tracked):
        out.append(
            F(
                "git.godot_cache",
                S.WARN,
                ".godot/ is committed",
                "It is a machine-local cache and contains export_credentials.cfg (keystore passwords).",
                "git rm -r --cached .godot && echo .godot/ >> .gitignore",
            )
        )
    if not out:
        out.append(F("git.secrets", S.OK, "No keystores or export credentials tracked by git"))
    return out


SECRET_OPTION = re.compile(r"(release_password|release_user|script_encryption_key)$")


def _check_plaintext_secrets(ctx: DoctorContext, _: str | None) -> list[Finding]:
    """Passwords belong in .godot/export_credentials.cfg or env vars, never in export_presets.cfg."""
    found = sorted(
        f"preset '{p.name}': {k}"
        for p in ctx.project.presets
        for k, v in {**p.values, **p.options}.items()
        if SECRET_OPTION.search(k) and str(v or "").strip()
    )
    if not found:
        return []
    tracked = ctx.tracked_files is not None and "export_presets.cfg" in ctx.tracked_files
    return [
        F(
            "git.preset_secrets",
            S.ERROR if tracked else S.WARN,
            "export_presets.cfg contains secrets in plain text" + (" and is committed to git" if tracked else ""),
            "\n".join(found) + "\n(values not shown)",
            "Remove them from export_presets.cfg and use GODOT_ANDROID_KEYSTORE_* environment variables. "
            "If the file was ever pushed, rotate the key.",
        )
    ]


# ---------------------------------------------------------------- toolchain


def _check_sdk(ctx: DoctorContext, _: str | None) -> list[Finding]:
    tc = ctx.toolchain
    if not tc.sdk:
        return [
            F(
                "android.sdk",
                S.ERROR,
                "Android SDK path is not configured",
                f"Editor settings: {tc.settings_file or 'not found'}; $ANDROID_HOME is not set either.",
                "gsk configure --android-sdk /path/to/sdk --java-sdk /path/to/jdk17",
                playreq.GODOT_ANDROID_DOCS,
            )
        ]
    if not tc.sdk.is_dir():
        return [F("android.sdk", S.ERROR, f"Android SDK path does not exist: {tc.sdk}", f"Configured in {tc.sdk_source}.")]
    out = [F("android.sdk", S.OK, f"Android SDK at {tc.sdk}", f"From {tc.sdk_source}.")]
    adb = tc.sdk / "platform-tools" / ("adb.exe" if os.name == "nt" else "adb")
    if not adb.is_file():
        out.append(
            F(
                "android.platform_tools",
                S.ERROR,
                "platform-tools (adb) missing from the SDK",
                fix='sdkmanager "platform-tools"',
            )
        )
    bts = tc.build_tools()
    apksigner = tc.latest_build_tool("apksigner")
    if not bts or not apksigner:
        out.append(F("android.build_tools", S.ERROR, "No build-tools with apksigner in the SDK", fix='sdkmanager "build-tools;36.1.0"'))
    else:
        out.append(F("android.build_tools", S.OK, f"build-tools {bts[-1].name}"))
    cmdline = tc.sdk / "cmdline-tools"
    if not cmdline.is_dir():
        out.append(
            F(
                "android.cmdline_tools",
                S.WARN,
                "cmdline-tools missing from the SDK",
                "Gradle builds use sdkmanager to fetch missing platforms.",
                'Install "Android SDK Command-line Tools (latest)".',
            )
        )
    compile_sdk = ctx.template_sdks.get("compileSdk")
    if compile_sdk and not (tc.sdk / "platforms" / f"android-{compile_sdk}").is_dir():
        licensed = (tc.sdk / "licenses").is_dir()
        out.append(
            F(
                "android.platform",
                S.INFO if licensed else S.WARN,
                f"SDK platform android-{compile_sdk} is not installed",
                "Gradle downloads it on the first build"
                + (" (licenses are accepted)." if licensed else ", but SDK licenses are not accepted yet."),
                f'sdkmanager "platforms;android-{compile_sdk}"' + ("" if licensed else " && yes | sdkmanager --licenses"),
            )
        )
    return out


def _check_jdk(ctx: DoctorContext, _: str | None) -> list[Finding]:
    tc = ctx.toolchain
    java = tc.java()
    if not java:
        return [
            F(
                "android.jdk",
                S.ERROR,
                "Java SDK not configured" if not tc.jdk else f"No bin/java under {tc.jdk}",
                f"Godot {ctx.godot.minor if ctx.godot else '4'} Android builds need JDK {playreq.GODOT_JDK}.",
                "Install OpenJDK 17 and run: gsk configure --java-sdk /usr/lib/jvm/java-17-openjdk-amd64",
                playreq.GODOT_ANDROID_DOCS,
            )
        ]
    major = java_major(java)
    if major != playreq.GODOT_JDK:
        return [
            F(
                "android.jdk",
                S.WARN,
                f"JDK {major} found; Godot recommends JDK {playreq.GODOT_JDK}",
                f"{java} (from {tc.jdk_source}). Other versions often break the Gradle build.",
                "Point java_sdk_path at a JDK 17 install.",
            )
        ]
    return [F("android.jdk", S.OK, f"JDK {major} at {tc.jdk}")]


# ---------------------------------------------------------------- rendering


def _check_renderer(ctx: DoctorContext, _: str | None) -> list[Finding]:
    p = ctx.project
    out: list[Finding] = []
    if p.setting("rendering/textures/vram_compression/import_etc2_astc") is not True:
        out.append(
            F(
                "rendering.etc2",
                S.ERROR,
                "ETC2/ASTC texture import is off",
                "Android export refuses to run without it.",
                "Project Settings > Rendering > Textures > VRAM Compression > Import ETC2 ASTC = on.",
            )
        )
    else:
        out.append(F("rendering.etc2", S.OK, "ETC2/ASTC texture import enabled"))

    r = p.renderer_mobile
    if r == "gl_compatibility":
        out.append(
            F(
                "rendering.renderer",
                S.OK,
                "Mobile renderer: Compatibility (OpenGL ES 3)",
                "Widest device support; best default for low-end Mali / Adreno phones.",
            )
        )
    elif r == "mobile":
        out.append(
            F(
                "rendering.renderer",
                S.INFO,
                "Mobile renderer: Mobile (Vulkan)",
                "Looks closer to desktop, but low-end Mali and Adreno GPUs often run Compatibility much faster "
                "and some older drivers misrender Vulkan.",
                "If you target budget phones, profile both: set rendering/renderer/rendering_method.mobile to gl_compatibility.",
                "https://docs.godotengine.org/en/stable/tutorials/rendering/renderers.html",
            )
        )
        if p.setting("rendering/rendering_device/fallback_to_opengl3") is False:
            out.append(
                F(
                    "rendering.fallback",
                    S.WARN,
                    "Vulkan-to-OpenGL fallback is disabled",
                    "Phones without usable Vulkan will refuse to start the game.",
                    "Set rendering/rendering_device/fallback_to_opengl3 = true.",
                )
            )
    else:
        out.append(
            F(
                "rendering.renderer",
                S.WARN,
                f"Mobile renderer is '{r}'",
                "Forward+ is designed for desktop GPUs.",
                "Set rendering/renderer/rendering_method.mobile to mobile or gl_compatibility.",
            )
        )

    msaa = p.setting("rendering/anti_aliasing/quality/msaa_3d", 0)
    if isinstance(msaa, int) and msaa >= 2:
        out.append(
            F(
                "rendering.msaa",
                S.INFO,
                f"3D MSAA is {2**msaa}x",
                "On tile-based mobile GPUs 4x is usually affordable; 8x rarely is.",
                "Consider a per-device quality setting.",
            )
        )

    if r in ("gl_compatibility", "mobile"):
        heavy: dict[str, list[str]] = {}
        for path in p.walk():
            if path.suffix not in (".tscn", ".tres") or path.stat().st_size > 5_000_000:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for key, label in HEAVY_ENV_KEYS.items():
                if re.search(rf"^{key}\s*=\s*true", text, re.M):
                    heavy.setdefault(label, []).append(path.relative_to(p.root).as_posix())
        if heavy:
            detail = "\n".join(f"{label}: {', '.join(files[:3])}{' ...' if len(files) > 3 else ''}" for label, files in heavy.items())
            out.append(
                F(
                    "rendering.environment",
                    S.INFO,
                    "Environment uses Forward+-only effects",
                    detail + "\nThe Mobile and Compatibility renderers ignore these, so the phone build will look different.",
                    "Check the look on device with `gsk eyes --rendering-method " + r + "`.",
                )
            )
    return out


def _check_display(ctx: DoctorContext, _: str | None) -> list[Finding]:
    p = ctx.project
    mode = p.setting("display/window/stretch/mode", "disabled")
    out: list[Finding] = []
    if mode in (None, "disabled"):
        out.append(
            F(
                "display.stretch",
                S.WARN,
                "Stretch mode is 'disabled'",
                "UI keeps its pixel size, so it looks tiny on 1080p+ phones and gets cut off on small ones.",
                "Project Settings > Display > Window > Stretch > Mode = canvas_items, Aspect = expand.",
                "https://docs.godotengine.org/en/stable/tutorials/rendering/multiple_resolutions.html",
            )
        )
    else:
        aspect = p.setting("display/window/stretch/aspect", "keep")
        out.append(F("display.stretch", S.OK, f"Stretch mode {mode}, aspect {aspect}"))
    orient = p.setting("display/window/handheld/orientation", 0)
    names = {
        0: "landscape",
        1: "portrait",
        2: "reverse landscape",
        3: "reverse portrait",
        4: "sensor landscape",
        5: "sensor portrait",
        6: "sensor",
    }
    out.append(F("display.orientation", S.OK, f"Orientation: {names.get(orient, orient)}"))
    return out


# ---------------------------------------------------------------- assets


def _check_textures(ctx: DoctorContext, _: str | None) -> list[Finding]:
    p = ctx.project
    textures = [e for e in p.imports if e.is_texture]
    if not textures:
        return []
    out: list[Finding] = []
    heavy = sorted((e for e in textures if e.compress_mode in (0, 1) and e.shipped_bytes > 1_000_000), key=lambda e: -e.shipped_bytes)
    if heavy:
        total = sum(e.shipped_bytes for e in heavy)
        out.append(
            F(
                "textures.uncompressed",
                S.WARN,
                f"{len(heavy)} texture(s) over 1 MB are not VRAM-compressed ({human_bytes(total)} in the export)",
                "\n".join(f"{e.res_path}  {human_bytes(e.shipped_bytes)}" for e in heavy[:5]) + ("\n..." if len(heavy) > 5 else ""),
                "Run `gsk slim`. VRAM compression (safe tier) cuts GPU memory and load time; the size caps "
                "and Lossy WebP in the review tier (`--aggressive`) are what shrink the download.",
            )
        )
    big = []
    for e in textures:
        limit = int(e.params.get("process/size_limit", 0) or 0)
        if limit:
            continue
        dims = image_size(e.source_path)
        if dims and max(dims) > 2048:
            big.append((e, dims))
    if big:
        out.append(
            F(
                "textures.dimensions",
                S.INFO,
                f"{len(big)} texture(s) larger than 2048 px with no size limit",
                "\n".join(f"{e.res_path}  {w}x{h}" for e, (w, h) in big[:5]),
                "Phones rarely benefit beyond 2048 px. Set process/size_limit in the import dock (or `gsk slim --aggressive`).",
            )
        )
    normals = [
        e
        for e in textures
        if is_normal_map_name(e.source_path) and e.compress_mode == 2 and int(e.params.get("compress/normal_map", 0) or 0) == 0
    ]
    if normals:
        out.append(
            F(
                "textures.normal_maps",
                S.INFO,
                f"{len(normals)} normal map(s) use auto-detection for normal compression",
                "Godot only switches to normal-map compression after seeing the texture in a material.",
                "If they look faceted on device, set compress/normal_map = Enable.",
            )
        )
    if not out:
        out.append(F("textures", S.OK, f"{len(textures)} textures, none flagged"))
    return out


def _check_audio(ctx: DoctorContext, _: str | None) -> list[Finding]:
    wavs = [e for e in ctx.project.imports if e.importer == "wav" and e.compress_mode == 0 and e.shipped_bytes > 1_000_000]
    if not wavs:
        return []
    total = sum(e.shipped_bytes for e in wavs)
    return [
        F(
            "audio.wav",
            S.WARN,
            f"{len(wavs)} uncompressed WAV file(s) over 1 MB ({human_bytes(total)})",
            "\n".join(f"{e.res_path}  {human_bytes(e.shipped_bytes)}" for e in sorted(wavs, key=lambda e: -e.shipped_bytes)[:5]),
            "Use Ogg Vorbis for music and long ambience; for short effects set the WAV import to QOA (compress/mode=2, Godot 4.3+).",
        )
    ]


def _check_size(ctx: DoctorContext, _: str | None) -> list[Finding]:
    p = ctx.project
    imported = p.root / ".godot" / "imported"
    if not imported.is_dir():
        return [
            F(
                "size.estimate",
                S.INFO,
                "No import cache yet, so sizes are unknown",
                "Open the project in the editor once, or run `godot --headless --path . --import`.",
            )
        ]
    total = sum(e.shipped_bytes for e in p.imports)
    mb = total / 1_048_576
    detail = (
        "Sum of imported resources that ship on Android (uncompressed). The engine library adds about 25 MB "
        f"compressed per architecture. Limits checked {playreq.CHECKED}."
    )
    if mb > playreq.AAB_ASSET_PACK_LIMIT_MB:
        return [
            F(
                "size.estimate",
                S.ERROR,
                f"Imported assets alone are {human_bytes(total)}; Play allows at most 1.5 GB per asset pack",
                detail,
                "Run `gsk slim`, and split content into on-demand Play Asset Delivery packs.",
                playreq.SIZE_LIMITS_URL,
            )
        ]
    if mb > playreq.AAB_BASE_MODULE_LIMIT_MB:
        return [
            F(
                "size.estimate",
                S.WARN,
                f"Imported assets are {human_bytes(total)}, above Play's {playreq.AAB_BASE_MODULE_LIMIT_MB} MB base module limit",
                detail + "\nRecent Godot versions put assets in an install-time asset pack (up to 1.5 GB), older ones in "
                "the base module. Build the AAB and check it with `gsk verify`.",
                "Run `gsk slim` to find the biggest wins.",
                playreq.SIZE_LIMITS_URL,
            )
        ]
    if mb > playreq.MOBILE_DATA_WARNING_MB:
        return [
            F(
                "size.estimate",
                S.WARN,
                f"Imported assets are {human_bytes(total)}",
                detail + f"\nAbove {playreq.MOBILE_DATA_WARNING_MB} MB, Play shows a size warning to users on mobile data.",
                "Run `gsk slim` to find the biggest wins.",
                playreq.SIZE_LIMITS_URL,
            )
        ]
    return [F("size.estimate", S.OK, f"Imported assets: {human_bytes(total)}", detail)]
