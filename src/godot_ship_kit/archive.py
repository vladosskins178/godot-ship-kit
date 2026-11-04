"""Inspect and verify an exported APK or AAB."""

from __future__ import annotations

import re
import shutil
import subprocess
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import playreq
from .env import AndroidToolchain
from .project import GodotProject
from .report import Finding, Severity, human_bytes

F = Finding
S = Severity


@dataclass
class ArchiveReport:
    path: Path
    kind: str  # "apk" | "aab"
    size: int
    findings: list[Finding] = field(default_factory=list)
    categories: dict[str, int] = field(default_factory=dict)
    biggest: list[tuple[str, int, str]] = field(default_factory=list)  # (entry, compressed, source)
    manifest: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not any(f.severity == S.ERROR for f in self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "kind": self.kind,
            "size_bytes": self.size,
            "ok": self.ok,
            "manifest": self.manifest,
            "categories": self.categories,
            "biggest": [{"entry": e, "compressed_bytes": b, "source": s} for e, b, s in self.biggest],
            "findings": [f.to_dict() for f in self.findings],
        }


def _category(name: str) -> str:
    n = name.removeprefix("base/")
    if n.startswith("lib/") or n.startswith("root/lib/"):
        return "native libraries"
    if "/.godot/imported/" in n or n.startswith(".godot/imported/"):
        return "imported assets"
    if n.endswith(".dex") or n.startswith("dex/"):
        return "dex (Java/Kotlin)"
    if n.startswith("assets/"):
        return "other assets (scripts, pck data)"
    if n.startswith("res/") or n.endswith(".arsc") or n.startswith("resources.pb"):
        return "Android resources"
    if n.startswith("META-INF/") or n.startswith("BUNDLE-METADATA/"):
        return "signatures and metadata"
    return "other"


def _import_sources(project: GodotProject | None) -> dict[str, str]:
    """Map ``.godot/imported/<file>`` back to the asset it came from."""
    if not project:
        return {}
    out: dict[str, str] = {}
    for e in project.imports:
        for d in e.dest_files:
            out[d.removeprefix("res://")] = e.res_path
    return out


def inspect(
    path: Path,
    toolchain: AndroidToolchain | None = None,
    project: GodotProject | None = None,
    expect_release: bool | None = None,
) -> ArchiveReport:
    kind = "aab" if path.suffix.lower() == ".aab" else "apk"
    rep = ArchiveReport(path, kind, path.stat().st_size if path.exists() else 0)
    if not path.is_file():
        rep.findings.append(F("archive.exists", S.ERROR, f"{path} was not produced"))
        return rep

    try:
        with zipfile.ZipFile(path) as zf:
            bad = zf.testzip()
            infos = zf.infolist()
    except zipfile.BadZipFile as exc:
        rep.findings.append(F("archive.zip", S.ERROR, f"Not a valid zip archive: {exc}"))
        return rep
    if bad:
        rep.findings.append(F("archive.zip", S.ERROR, f"Corrupt entry in archive: {bad}"))
    else:
        rep.findings.append(F("archive.zip", S.OK, f"Archive integrity OK ({len(infos)} entries, CRC checked)"))

    sources = _import_sources(project)
    for info in infos:
        cat = _category(info.filename)
        rep.categories[cat] = rep.categories.get(cat, 0) + info.compress_size
    for info in sorted(infos, key=lambda i: -i.compress_size)[:15]:
        key = re.sub(r"^(base/)?(assets/)?", "", info.filename)
        rep.biggest.append((info.filename, info.compress_size, sources.get(key, "")))

    names = {i.filename for i in infos}
    prefix = "base/" if kind == "aab" else ""
    if f"{prefix}lib/arm64-v8a/libgodot_android.so" not in names:
        arm64 = any(n.startswith(f"{prefix}lib/arm64-v8a/") for n in names)
        rep.findings.append(
            F(
                "archive.arm64",
                S.OK if arm64 else S.ERROR,
                "Contains arm64-v8a native code" if arm64 else "No arm64-v8a native code in the archive",
                fix="" if arm64 else "Enable architectures/arm64-v8a in the preset; Play requires 64-bit.",
            )
        )
    else:
        rep.findings.append(F("archive.arm64", S.OK, "Contains arm64-v8a engine library"))

    mb = rep.size / 1_048_576
    if kind == "aab" and mb > playreq.AAB_BASE_MODULE_LIMIT_MB:
        rep.findings.append(
            F(
                "archive.size",
                S.ERROR,
                f"AAB is {human_bytes(rep.size)}; Play's base module limit is {playreq.AAB_BASE_MODULE_LIMIT_MB} MB",
                fix="Run `gsk slim` or move content into Play Asset Delivery packs.",
                docs=playreq.SIZE_LIMITS_URL,
            )
        )
    elif mb > playreq.MOBILE_DATA_WARNING_MB:
        rep.findings.append(
            F(
                "archive.size",
                S.WARN,
                f"{kind.upper()} is {human_bytes(rep.size)}",
                f"Above {playreq.MOBILE_DATA_WARNING_MB} MB, Play warns users on mobile data before downloading.",
                "Run `gsk slim`.",
                playreq.SIZE_LIMITS_URL,
            )
        )
    else:
        rep.findings.append(F("archive.size", S.OK, f"{kind.upper()} size {human_bytes(rep.size)}"))

    if kind == "apk":
        _verify_apk(rep, toolchain, expect_release)
    else:
        _verify_aab(rep, toolchain)
    return rep


def _run(cmd: list[str], timeout: int = 300) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None


def _verify_apk(rep: ArchiveReport, tc: AndroidToolchain | None, expect_release: bool | None) -> None:
    apksigner = tc.latest_build_tool("apksigner") if tc else (Path(p) if (p := shutil.which("apksigner")) else None)
    zipalign = tc.latest_build_tool("zipalign") if tc else (Path(p) if (p := shutil.which("zipalign")) else None)
    aapt2 = tc.latest_build_tool("aapt2") if tc else (Path(p) if (p := shutil.which("aapt2")) else None)
    path = str(rep.path)

    if apksigner:
        res = _run([str(apksigner), "verify", "--print-certs", "-v", path])
        if res and res.returncode == 0:
            subject = re.search(r"certificate DN: (.+)", res.stdout)
            digest = re.search(r"certificate SHA-256 digest: ([0-9a-f]+)", res.stdout)
            schemes = re.findall(r"Verified using (v\d(?:\.\d)?) scheme .*: true", res.stdout)
            dn = subject.group(1).strip() if subject else "?"
            debug_cert = "Android Debug" in dn
            sev = S.OK
            note = ""
            if expect_release and debug_cert:
                sev, note = S.ERROR, "Release build is signed with the debug certificate."
            elif debug_cert:
                sev, note = S.INFO, "Signed with the debug certificate (fine for testing, not for Play)."
            rep.findings.append(
                F(
                    "archive.signature",
                    sev,
                    "Signature verified" + (f" ({', '.join(schemes)})" if schemes else ""),
                    f"Signer: {dn}\nSHA-256: {digest.group(1) if digest else '?'}" + (f"\n{note}" if note else ""),
                )
            )
            rep.manifest["signer"] = dn
        else:
            out = (res.stderr or res.stdout).strip().splitlines()[-3:] if res else ["apksigner did not run"]
            rep.findings.append(F("archive.signature", S.ERROR, "APK signature does not verify", "\n".join(out)))
    else:
        rep.findings.append(
            F("archive.signature", S.INFO, "apksigner not found; signature not checked", fix="Install Android build-tools.")
        )

    if zipalign:
        res = _run([str(zipalign), "-c", "-P", "16", "-v", "4", path])
        if res is None:
            rep.findings.append(F("archive.align", S.INFO, "zipalign did not run"))
        elif res.returncode == 0:
            rep.findings.append(F("archive.align", S.OK, "zipalign OK, including 16 KB page alignment"))
        else:
            unaligned = [ln for ln in res.stdout.splitlines() if "BAD" in ln][:5]
            rep.findings.append(
                F(
                    "archive.align",
                    S.WARN,
                    "Native libraries are not 16 KB page aligned",
                    "\n".join(unaligned)
                    + "\nPlay requires 16 KB page support for apps targeting Android 15+ "
                    + f"(release blocker from {playreq.PAGE_16K_DEADLINE}).",
                    "Use Godot 4.5+ templates (built with NDK r28+). Plugins with old .so files need updating too.",
                    playreq.PAGE_16K_URL,
                )
            )
    if aapt2:
        res = _run([str(aapt2), "dump", "badging", path])
        if res and res.returncode == 0:
            m = re.search(r"package: name='([^']+)' versionCode='(\d+)' versionName='([^']*)'", res.stdout)
            t = re.search(r"targetSdkVersion:'(\d+)'", res.stdout)
            s = re.search(r"(?:minSdkVersion|sdkVersion):'(\d+)'", res.stdout)
            if m:
                rep.manifest.update(package=m.group(1), version_code=int(m.group(2)), version_name=m.group(3))
            if t:
                rep.manifest["target_sdk"] = int(t.group(1))
            if s:
                rep.manifest["min_sdk"] = int(s.group(1))
            perms = sorted(set(re.findall(r"uses-permission: name='([^']+)'", res.stdout)))
            if perms:
                rep.manifest["permissions"] = [p.removeprefix("android.permission.") for p in perms]
            if "application-debuggable" in res.stdout:
                rep.manifest["debuggable"] = True
                if expect_release:
                    rep.findings.append(
                        F("archive.debuggable", S.ERROR, "Release build is marked debuggable", fix="Export with --export-release.")
                    )
            _target_finding(rep)


def _target_finding(rep: ArchiveReport) -> None:
    target = rep.manifest.get("target_sdk")
    if target is None:
        return
    if target < playreq.PLAY_TARGET_SDK:
        rep.findings.append(
            F(
                "archive.target_sdk",
                S.ERROR,
                f"targetSdkVersion {target} is below Play's API {playreq.PLAY_TARGET_SDK}",
                f"Requirement since {playreq.PLAY_TARGET_SDK_SINCE}, checked {playreq.CHECKED}.",
                "See `gsk doctor` for how to raise it.",
                playreq.PLAY_TARGET_SDK_URL,
            )
        )
    else:
        rep.findings.append(F("archive.target_sdk", S.OK, f"targetSdkVersion {target}"))


def _bundletool() -> list[str] | None:
    exe = shutil.which("bundletool")
    if exe:
        return [exe]
    for cand in (Path.home() / "bundletool.jar", Path("/usr/share/java/bundletool.jar")):
        if cand.is_file() and shutil.which("java"):
            return ["java", "-jar", str(cand)]
    return None


def _verify_aab(rep: ArchiveReport, tc: AndroidToolchain | None) -> None:
    bt = _bundletool()
    if not bt:
        rep.findings.append(
            F(
                "archive.bundletool",
                S.INFO,
                "bundletool not found; AAB structure not validated",
                fix="Install bundletool (https://github.com/google/bundletool/releases) or put bundletool.jar in your home folder.",
            )
        )
        return
    res = _run([*bt, "validate", f"--bundle={rep.path}"])
    if res and res.returncode == 0:
        rep.findings.append(F("archive.bundletool", S.OK, "bundletool validate passed"))
    else:
        tail = "\n".join(((res.stderr or res.stdout) if res else "did not run").strip().splitlines()[-3:])
        rep.findings.append(F("archive.bundletool", S.ERROR, "bundletool validate failed", tail))
    res = _run([*bt, "dump", "manifest", f"--bundle={rep.path}"])
    if res and res.returncode == 0:
        m = re.search(r'package="([^"]+)"', res.stdout)
        vc = re.search(r'android:versionCode="(\d+)"', res.stdout)
        t = re.search(r'android:targetSdkVersion="(\d+)"', res.stdout)
        if m:
            rep.manifest["package"] = m.group(1)
        if vc:
            rep.manifest["version_code"] = int(vc.group(1))
        if t:
            rep.manifest["target_sdk"] = int(t.group(1))
        _target_finding(rep)
    res = _run([*bt, "dump", "config", f"--bundle={rep.path}"])
    if res and res.returncode == 0 and "PAGE_ALIGNMENT_16K" not in res.stdout:
        rep.findings.append(
            F(
                "archive.align",
                S.WARN,
                "AAB does not declare 16 KB page alignment",
                f"Release blocker from {playreq.PAGE_16K_DEADLINE} for apps targeting Android 15+.",
                "Build with Godot 4.5+ templates.",
                playreq.PAGE_16K_URL,
            )
        )
