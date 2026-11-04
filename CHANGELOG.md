# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-11-04

First public release.

### Added

- `gsk eyes`: headless capture at phone resolutions under xvfb + llvmpipe with the
  project's mobile renderer; actions (tap, press, call, set, wait); comparison with
  references by SSIM, changed-pixel share and largest connected change; diff images,
  changed-region and zoom crops; scene alerts (black or flat frame, meshes without
  materials, no camera, nothing in view, idle rigs, engine errors); HTML, Markdown and
  JSON reports; `--update` to save references (tagged with the Godot version), `--strict`
  for CI, automatic import of a fresh checkout. The capture script is generated as typed
  GDScript and works on Godot 4.2 to 4.6 and in projects with strict warning settings.
  Alerts also cover UI that runs off the screen and failed shot actions.
- `gsk doctor`: around 40 checks across toolchain (Godot, export templates including .NET,
  Android SDK, JDK 17, Gradle build template), export preset (package name, version,
  architectures, APK/AAB, target and min SDK from the template in use, permissions,
  icons), signing (keystore and credentials, git hygiene, plain-text passwords in presets;
  secrets are never printed), rendering (ETC2/ASTC, mobile renderer, OpenGL fallback,
  Forward+-only effects, stretch mode) and assets/size. Text, `--json` and `--markdown`.
- `gsk slim`: per-asset size of Godot's imported files, `.import` changes in two tiers
  (safe, review) with export and GPU memory estimates, apply with backup, `--undo`,
  `--reimport`; possibly unused assets and heavy GLB models.
- `gsk build`: headless APK/AAB export, signing from `GODOT_ANDROID_KEYSTORE_*`, early stop
  without a release keystore, `--install-template`, cleaned and masked logs, failure hints,
  automatic verification.
- `gsk verify`: zip integrity, `apksigner`, 16 KB page support read from ELF headers,
  `zipalign`, `aapt2`/`bundletool` manifest facts, size against Play limits, entries
  mapped back to source assets.
- `gsk play-checklist`, `gsk configure`, `gsk skill install|print`.
- `gsk mcp`: MCP server over stdio; `gsk_eyes` returns change crops as images.
- Agent skill (`skills/godot-ship-kit/SKILL.md`), Claude Code plugin and marketplace
  manifests, a composite GitHub Action (`action.yml`), a sample Godot 4.6 project with
  reference screenshots, and CI that runs the tools against it.

[0.1.0]: https://github.com/vladosskins178/godot-ship-kit/releases/tag/v0.1.0
