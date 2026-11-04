"""Google Play and Godot facts the checks rely on.

Every number here has a source and the date it was last checked. When Google
moves a deadline, update this file (and the date) and nothing else.
"""

from __future__ import annotations

CHECKED = "2026-11-04"

# Target API level for new apps and app updates.
PLAY_TARGET_SDK = 36
PLAY_TARGET_SDK_SINCE = "2026-08-31"
PLAY_TARGET_SDK_EXISTING = 35  # existing apps stay visible to new users on newer Android
PLAY_TARGET_SDK_URL = "https://developer.android.com/google/play/requirements/target-sdk"

# 16 KB memory page size support (apps targeting API 35+ on 64-bit devices).
PAGE_16K_DEADLINE = "2027-02-01"
PAGE_16K_URL = "https://developer.android.com/guide/practices/page-sizes"

# App Bundle size limits (compressed download size).
AAB_BASE_MODULE_LIMIT_MB = 500
APK_LEGACY_UPLOAD_LIMIT_MB = 100
MOBILE_DATA_WARNING_MB = 200
SIZE_LIMITS_URL = "https://support.google.com/googleplay/android-developer/answer/9859372"
AAB_REQUIRED_URL = "https://developer.android.com/guide/app-bundle"

# Default SDK levels baked into Godot's Android build template, by release.
# Source: platform/android/java/app/config.gradle at each *-stable tag.
GODOT_TEMPLATE_SDKS: dict[str, tuple[int, int]] = {
    # version prefix: (minSdk, targetSdk)
    "4.0": (21, 33),
    "4.1": (21, 33),
    "4.2": (21, 34),
    "4.3": (21, 34),
    "4.4": (21, 34),
    "4.5": (24, 35),
    "4.6": (24, 35),
    "4.6.3": (24, 36),
    "4.7": (24, 36),
}
GODOT_ANDROID_DOCS = "https://docs.godotengine.org/en/stable/tutorials/export/exporting_for_android.html"
GODOT_JDK = 17

STORE_LISTING_ASSETS_URL = "https://support.google.com/googleplay/android-developer/answer/9866151"
STORE_LISTING_TEXT_URL = "https://support.google.com/googleplay/android-developer/answer/9859152"
DATA_SAFETY_URL = "https://support.google.com/googleplay/android-developer/answer/10787469"
CONTENT_RATING_URL = "https://support.google.com/googleplay/android-developer/answer/9898843"
TARGET_AUDIENCE_URL = "https://support.google.com/googleplay/android-developer/answer/9867159"
APP_REVIEW_PREP_URL = "https://support.google.com/googleplay/android-developer/answer/9859455"
PRIVACY_POLICY_URL = "https://support.google.com/googleplay/android-developer/answer/10144311"
TESTING_REQUIREMENT_URL = "https://support.google.com/googleplay/android-developer/answer/14151465"

# Permissions that trigger a Play policy declaration or a prominent Data safety entry.
SENSITIVE_PERMISSIONS: dict[str, str] = {
    "access_background_location": "background location needs a Play declaration and review",
    "access_fine_location": "precise location must be declared in Data safety",
    "access_coarse_location": "approximate location must be declared in Data safety",
    "camera": "camera access must be justified and declared",
    "record_audio": "microphone access must be justified and declared",
    "read_contacts": "contacts are personal data; declare in Data safety",
    "read_phone_state": "rarely needed by games; Play reviewers ask why",
    "read_external_storage": "replaced by scoped storage; usually unnecessary",
    "write_external_storage": "replaced by scoped storage; usually unnecessary",
    "manage_external_storage": "All files access needs a Play declaration and is rarely approved for games",
    "read_media_images": "photo access falls under the Photo and Video Permissions policy",
    "read_media_video": "video access falls under the Photo and Video Permissions policy",
    "read_sms": "SMS permissions are restricted to default SMS apps",
    "send_sms": "SMS permissions are restricted to default SMS apps",
    "call_phone": "phone permissions are restricted",
    "request_install_packages": "needs a Play declaration",
    "query_all_packages": "needs a Play declaration",
    "system_alert_window": "draw-over-apps is restricted",
}


def godot_template_sdks(version: str) -> tuple[int, int] | None:
    """Best known (minSdk, targetSdk) for a Godot version like ``4.6.3``."""
    parts = version.split(".")
    for n in (3, 2):
        key = ".".join(parts[:n])
        if key in GODOT_TEMPLATE_SDKS:
            return GODOT_TEMPLATE_SDKS[key]
    return None
