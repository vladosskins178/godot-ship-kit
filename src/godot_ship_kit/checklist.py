"""``gsk play-checklist``: a Play Console launch checklist filled in from the project.

This is guidance, not legal or policy advice. Play Console is the source of
truth; every section links to the official page it summarises.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import playreq
from .project import GodotProject


def _addons(project: GodotProject) -> list[str]:
    addons = project.root / "addons"
    return sorted(d.name for d in addons.iterdir() if d.is_dir()) if addons.is_dir() else []


def _detect(project: GodotProject) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {"ads": [], "analytics": [], "billing": [], "network": []}
    for key, pattern in {
        "ads": r"admob|appodeal|applovin|unityads|ironsource|\bads\b",
        "analytics": r"firebase|analytics|gameanalytics|crashlytics|sentry",
        "billing": r"billing|iap|purchase",
        "network": r"playgames|play_games|nakama|steam|multiplayer",
    }.items():
        found[key] = [a for a in _addons(project) if re.search(pattern, a.lower())]
    preset = project.find_preset(None)
    if preset:
        for k, v in preset.options.items():
            if v is True and re.search(r"firebase|admob|billing|play_games", k, re.I):
                bucket = "ads" if "admob" in k else "billing" if "billing" in k else "analytics"
                found[bucket].append(f"preset option {k}")
    return found


def build_checklist(project: GodotProject) -> str:
    preset = project.find_preset(None)
    opt = preset.opt if preset else (lambda _k, d=None: d)
    pkg = opt("package/unique_name", "") or "(set package/unique_name)"
    app_name = opt("package/name", "") or project.name
    version = f"{opt('version/name', '?')} (code {opt('version/code', '?')})"
    perms = sorted(k.split("/", 1)[1] for k, v in (preset.options.items() if preset else []) if k.startswith("permissions/") and v is True)
    det = _detect(project)
    sensitive = [p for p in perms if p in playreq.SENSITIVE_PERMISSIONS]
    fmt = "AAB" if preset and opt("gradle_build/export_format", 0) == 1 else "APK"

    def box(text: str) -> str:
        return f"- [ ] {text}"

    lines = [
        f"# Google Play launch checklist: {app_name}",
        "",
        f"> Guidance from `gsk play-checklist` from `{project.root.name}`. Play policies change; "
        f"facts below were checked on {playreq.CHECKED} and every section links to the official source. "
        "Play Console is always the final word.",
        "",
        f"Package `{pkg}` · version {version} · preset exports {fmt}",
        "",
        "## 1. Build",
        "",
        box(
            f"Upload an **Android App Bundle (.aab)**; new apps cannot use APKs. [source]({playreq.AAB_REQUIRED_URL})"
            + (" Your preset currently exports an APK." if fmt == "APK" else "")
        ),
        box(
            f"Target **API {playreq.PLAY_TARGET_SDK}** or higher (required for new apps and updates since "
            f"{playreq.PLAY_TARGET_SDK_SINCE}). `gsk doctor` checks this. [source]({playreq.PLAY_TARGET_SDK_URL})"
        ),
        box(
            f"Support **16 KB memory pages** (release blocker for updates from {playreq.PAGE_16K_DEADLINE}); "
            f"Godot 4.5+ templates do. `gsk build` checks with zipalign. [source]({playreq.PAGE_16K_URL})"
        ),
        box(
            f"Base module download size under **{playreq.AAB_BASE_MODULE_LIMIT_MB} MB**; users on mobile data see a "
            f"warning above {playreq.MOBILE_DATA_WARNING_MB} MB. `gsk slim` helps. [source]({playreq.SIZE_LIMITS_URL})"
        ),
        box(
            "Sign with your **upload key** and enrol in Play App Signing. Back the keystore up offline; losing it "
            "means asking Google for an upload-key reset."
        ),
        box("Bump `version/code` for every upload."),
        "",
        "## 2. Store listing",
        "",
        f"Text limits ([source]({playreq.STORE_LISTING_TEXT_URL})):",
        "",
        box(f'App name, max **30** characters (currently "{app_name}", {len(str(app_name))} characters)'),
        box("Short description, max **80** characters"),
        box("Full description, max **4000** characters"),
        "",
        f"Graphics ([source]({playreq.STORE_LISTING_ASSETS_URL})):",
        "",
        "| Asset | Spec |",
        "| --- | --- |",
        "| App icon | 512 x 512, 32-bit PNG with alpha, up to 1024 KB |",
        "| Feature graphic | 1024 x 500, JPEG or 24-bit PNG, no alpha |",
        "| Phone screenshots | 2 to 8; JPEG or 24-bit PNG, no alpha; each side 320 to 3840 px; long side at most 2x the "
        "short side. For featuring: at least 4, at least 1080 px (1080x1920 / 1920x1080) |",
        '| 7" and 10" tablet screenshots | at least 4 each if you list tablets; 1080 to 7680 px; 16:9 or 9:16 |',
        "",
        box(
            "Icon, feature graphic, phone screenshots ready (`gsk eyes --device phone` renders clean 1080x2400 "
            "captures; crop or pad to the 2:1 limit)."
        ),
        box("Category, contact email, and optional promo video set."),
        "",
        "## 3. App content (Policy > App content)",
        "",
        box(f"**Privacy policy** URL. Required for every app, even ones that collect nothing. [source]({playreq.PRIVACY_POLICY_URL})"),
        box(f"**Data safety** form. Required for every app. [source]({playreq.DATA_SAFETY_URL})"),
    ]
    sdk_lines = []
    if det["ads"]:
        sdk_lines.append(f"ads SDK ({', '.join(det['ads'])}): device identifiers / advertising ID, app interactions, diagnostics")
    if det["analytics"]:
        sdk_lines.append(f"analytics / crash SDK ({', '.join(det['analytics'])}): app interactions, crash logs, diagnostics, device IDs")
    if det["billing"]:
        sdk_lines.append(f"billing ({', '.join(det['billing'])}): purchase history")
    if det["network"]:
        sdk_lines.append(f"online services ({', '.join(det['network'])}): user IDs, possibly gameplay content")
    for perm in sensitive:
        sdk_lines.append(f"permission `{perm}`: {playreq.SENSITIVE_PERMISSIONS[perm]}")
    if sdk_lines:
        lines.append("  - Detected in this project, likely to need declaring:")
        lines += [f"    - {s}" for s in sdk_lines]
    else:
        lines.append(
            '  - Nothing detected that collects data. If that is true, you can declare "no data collected" (third-party SDKs count too).'
        )
    lines += [
        box(
            "**Ads** declaration"
            + (f": yes, detected {', '.join(det['ads'])}" if det["ads"] else ": answer No only if no ad SDK ships")
            + f". [source]({playreq.APP_REVIEW_PREP_URL})"
        ),
        box(
            f"**Content rating** (IARC questionnaire): violence, fear, gambling-like mechanics, user interaction, "
            f"in-app purchases. [source]({playreq.CONTENT_RATING_URL})"
        ),
        box(
            f"**Target audience and content**: choosing ages under 13 brings in the Families policy (stricter ad and "
            f"data rules). [source]({playreq.TARGET_AUDIENCE_URL})"
        ),
        box(f"**App access**: if anything sits behind a login, give reviewers credentials. [source]({playreq.APP_REVIEW_PREP_URL})"),
        box('Government, financial, health and news declarations: answer "no" for a typical game.'),
        "",
        "## 4. Testing and release",
        "",
        box(
            f"New **personal** developer accounts (created after 13 Nov 2023): run a closed test with at least **12 testers "
            f"opted in for 14 days** before you can apply for production. [source]({playreq.TESTING_REQUIREMENT_URL})"
        ),
        box("Internal testing track first: install from Play on a low-end phone and a recent one."),
        box("Pre-launch report (Play Console > Testing) reviewed for crashes and accessibility."),
        box("Staged rollout (for example 10% then 50% then 100%) for the production release."),
        "",
        "---",
        f"Permissions in the Android preset: {', '.join(f'`{p}`' for p in perms) or 'none'}.",
        "",
    ]
    return "\n".join(lines)


def write_checklist(project: GodotProject, out: Path) -> Path:
    out.write_text(build_checklist(project), encoding="utf-8")
    return out
