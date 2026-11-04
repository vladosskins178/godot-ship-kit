# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-11-04

First public release.

### Added

- `gsk doctor`: 40 checks across toolchain (Godot, export templates, Android SDK, JDK 17,
  Gradle build template), export preset (package name, version, architectures, APK/AAB,
  target and min SDK from the template in use, permissions, icons), signing (keystore and
  credentials, git hygiene; secrets are never printed), rendering (ETC2/ASTC, mobile
  renderer, OpenGL fallback, Forward+-only effects, stretch mode) and assets/size.
  Text, `--json` and `--markdown` output; `--strict` fails on warnings.
- `gsk slim`: measures Godot's imported files per asset, proposes `.import` changes in two
  tiers (safe, review), estimates export and GPU memory impact, applies with backup,
  `--undo`, `--reimport`. Lists possibly unused assets and heavy GLB models.
- `gsk build`: headless APK/AAB export with signing from `GODOT_ANDROID_KEYSTORE_*`
  environment variables, failure hints from the export log, and automatic verification.
- `gsk verify`: zip integrity, `apksigner`, 16 KB `zipalign` check, `aapt2`/`bundletool`
  manifest facts (package, version, target SDK, permissions), size breakdown with entries
  mapped back to source assets.
- `gsk eyes`: headless capture at phone resolutions under xvfb + llvmpipe using the
  project's mobile renderer, actions (tap, press, call, set, wait), reference comparison
  (SSIM + changed pixels), changed-region crops, zoom crops, scene alerts (black frame,
  missing materials, no camera, nothing in view, idle rigs, engine errors), HTML, Markdown
  and JSON reports, `--update` to save references.
- `gsk play-checklist`: Play Console launch checklist filled in from the project.
- `gsk configure`: writes Android SDK / JDK / debug keystore paths into Godot's editor settings.
- `gsk skill install|print`: the agent skill for Claude Code, and for any agent that reads
  `AGENTS.md`.
- Claude Code plugin manifest and marketplace entry.
- Sample Godot 4.6 project with committed reference screenshots, and a CI workflow that
  runs the tools against it with a downloaded Godot.

[0.1.0]: https://github.com/vladosskins178/godot-ship-kit/releases/tag/v0.1.0
