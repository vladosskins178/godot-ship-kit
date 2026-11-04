# gsk doctor checks

Severity guide: **error** means the export or the Play upload will fail. **warn** means it
will probably hurt you (rejection, crash on some phones, a much bigger download).
**info** is a note worth reading. Every finding in the output carries a `fix:` line.

| Id | Looks at | Severity when it fires |
| --- | --- | --- |
| `project` | project name, Godot version and features | - |
| `project.main_scene` | a main scene is set and exists | error |
| `godot.binary` | Godot 4 binary from `--godot`, `$GODOT` or PATH | error |
| `godot.version` | binary minor version matches `config/features` | warn |
| `godot.templates` | `android_source.zip`, `android_release.apk`, `android_debug.apk` for the exact version | error |
| `preset.android` | an Android export preset exists | error |
| `preset.package` | `package/unique_name` is reverse-domain and not `com.example.*` | error |
| `preset.version` | `version/code` positive, `version/name` set | error / warn |
| `preset.arch` | `arm64-v8a` enabled; extra architectures noted | error / info |
| `preset.format` | AAB needs Gradle; APK noted as not uploadable for new apps | error / info |
| `preset.gradle_template` | `android/build` installed and `.build_version` matches Godot | error / warn |
| `android.target_sdk` | effective target SDK (preset override, project template, or export template) vs Play's requirement | error |
| `android.min_sdk` | effective min SDK | - |
| `preset.permissions` | permissions that need a Play declaration or Data safety entry | warn |
| `preset.internet` | network use (HTTPRequest, ad / analytics / online addons) without INTERNET | warn / info |
| `preset.icons` | launcher icons set, not the default Godot logo | warn / info |
| `signing.release` | release keystore from env or preset exists; password and alias present | warn / error |
| `signing.location` | release keystore inside the project, or tracked by git | warn / error |
| `signing.debug` | a debug keystore is available | warn |
| `git.secrets` | `*.keystore`, `*.jks`, `export_credentials.cfg` tracked by git | error |
| `git.godot_cache` | `.godot/` tracked by git | warn |
| `android.sdk` | SDK path from editor settings or `$ANDROID_HOME` exists | error |
| `android.platform_tools` | `adb` present | error |
| `android.build_tools` | build-tools with `apksigner` | error |
| `android.cmdline_tools` | `cmdline-tools` present | warn |
| `android.platform` | `platforms/android-<compileSdk>` present (Gradle can fetch it) | info / warn |
| `android.jdk` | `java` under the configured JDK, version 17 | error / warn |
| `rendering.etc2` | `import_etc2_astc` enabled | error |
| `rendering.renderer` | mobile renderer (Compatibility, Mobile, Forward+) | info / warn |
| `rendering.fallback` | Vulkan to OpenGL fallback not disabled | warn |
| `rendering.msaa` | 3D MSAA level | info |
| `rendering.environment` | SDFGI, SSR, SSIL, SSAO, volumetric fog in scenes or resources | info |
| `display.stretch` | stretch mode not `disabled` | warn |
| `display.orientation` | handheld orientation | - |
| `textures.uncompressed` | Lossless / Lossy textures over 1 MB in the export | warn |
| `textures.dimensions` | textures over 2048 px with no size limit | info |
| `textures.normal_maps` | VRAM normal maps relying on auto-detected normal compression | info |
| `audio.wav` | uncompressed WAV over 1 MB | warn |
| `size.estimate` | imported size vs Play's 500 MB base module and 200 MB mobile-data warning | error / warn |

## Where the numbers come from

All Play requirements are in [`src/godot_ship_kit/playreq.py`](../src/godot_ship_kit/playreq.py)
with source URLs and the date they were last checked:

- Target API level: <https://developer.android.com/google/play/requirements/target-sdk>
- 16 KB page sizes: <https://developer.android.com/guide/practices/page-sizes>
- App size limits: <https://support.google.com/googleplay/android-developer/answer/9859372>
- Godot Android export: <https://docs.godotengine.org/en/stable/tutorials/export/exporting_for_android.html>
