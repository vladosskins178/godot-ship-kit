<h1 align="center">godot-ship-kit</h1>

<p align="center">
  <b>Headless phone screenshots and Android release checks for Godot 4, built for coding agents.</b><br>
  One CLI (<code>gsk</code>), an agent skill and an MCP server: see what a change looks like on a phone,<br>
  check the export setup, shrink the APK, then build and verify a Play-ready AAB. No GPU or editor needed.
</p>

<p align="center">
  <a href="https://github.com/vladosskins178/godot-ship-kit/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/vladosskins178/godot-ship-kit/actions/workflows/ci.yml/badge.svg"></a>
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-blue.svg"></a>
  <img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-3776AB.svg">
  <img alt="Godot 4.2 to 4.6" src="https://img.shields.io/badge/Godot-4.2%E2%80%934.6-478CBF.svg">
  <img alt="Agent skill and MCP" src="https://img.shields.io/badge/agents-skill%20%7C%20MCP-8A63D2.svg">
</p>

<p align="center">
  <img src="docs/img/eyes-agent-loop.gif" width="800" alt="Illustration of the loop with real gsk output: a restyled button, gsk eyes reports changed, the region crop shows the label became invisible, the fix, then match">
</p>

---

AI agents can write GDScript all day. Then the game has to reach a phone, and that is
where it breaks: Android SDK and JDK paths, export templates, keystores, an AAB instead
of an APK, a target API level Google raised again, a 600 MB build, and a UI that looks
fine in the editor but is cut off on a 20:9 screen. The agent can't see any of it.

`gsk` turns those into commands with clear answers:

| Command | What it does |
| --- | --- |
| `gsk doctor` | Checks the project, export preset, Android SDK / JDK / templates, signing, Play's target API level, renderer choice, textures and size. Every finding comes with a fix. |
| `gsk slim` | Shows what actually ships (Godot's imported files, not your sources), proposes `.import` changes, applies them with a backup, re-imports and reports the new size. |
| `gsk build` | Headless APK/AAB export with signing from environment variables, then verification: zip integrity, signature, 16 KB alignment, target SDK, permissions, size breakdown. |
| `gsk eyes` | Renders scenes at phone resolutions with software OpenGL, compares them with reference images, flags black frames, missing materials and idle rigs, and writes an HTML report plus a Markdown one for agents. |
| `gsk play-checklist` | A Play Console launch checklist filled in from your project, with links to the official pages. |
| `gsk verify` | The build verification on its own, for any APK or AAB. |
| `gsk mcp` | All of the above as MCP tools; `gsk_eyes` returns the changed-region crops as images. |
| `gsk configure`, `gsk skill` | Write Android SDK / JDK paths into Godot's editor settings; install or print the agent skill. |

It grew out of shipping a 3D Godot 4 game to Android. Fixing texture import settings took
that APK from **651 MB to 273 MB**, and the habit that saved the most time was making the
agent render and look at a change before calling it done. Pointed at the same game,
`gsk eyes` immediately showed what the editor never did: on a 20:9 phone the 9:16 menu
sits between black bars and the bottom button label is cut off.

## Quick start

```bash
pipx install "godot-ship-kit[eyes] @ git+https://github.com/vladosskins178/godot-ship-kit"
export GODOT=/path/to/godot              # if godot is not on PATH

cd my-game
gsk doctor                               # what blocks an Android release?
gsk slim                                 # what makes the build big?
gsk eyes --scene res://ui/menu.tscn      # what does it look like on a phone?
gsk build --debug                        # export and verify (release builds need a keystore, see below)
```

Try it on the sample project in this repo:

```bash
git clone https://github.com/vladosskins178/godot-ship-kit && cd godot-ship-kit
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[eyes]"
export GODOT=/path/to/godot
$GODOT --headless --path examples/hello-ship --import
gsk eyes examples/hello-ship             # 3 shots, all match the committed references
```

`doctor`, `slim`, `build` and `play-checklist` need only the Python standard library.
`gsk eyes` adds Pillow and numpy (the `[eyes]` extra) and, on a headless Linux box,
`xvfb`, Mesa and the X11 client libraries Godot loads:

```bash
sudo apt install xvfb libgl1-mesa-dri libx11-6 libxcursor1 libxinerama1 libxrandr2 libxi6 libxkbcommon0
```

## gsk eyes: let the agent see

```bash
gsk eyes                                         # shots from gsk.toml, or the main scene
gsk eyes --scene res://scenes/shop.tscn --device phone --device tablet
gsk eyes --update                                # save the current captures as references
```

`gsk eyes` starts Godot under a virtual display, loads each scene at a real phone
resolution, waits, optionally presses buttons or calls methods, and saves a screenshot.
It uses the renderer your game uses **on the phone** (`rendering_method.mobile`), so a
project that runs Forward+ on desktop is checked the way Android will draw it.

Each capture is then compared with its reference:

- **Similarity** (SSIM over a downscaled copy, so it judges structure, not dithering),
  **changed pixels** (per-pixel difference above a tolerance) and the **largest connected
  change**, so a single wrong digit in a price fails the shot even though it barely moves
  the frame-wide numbers.
- **UI off screen**: any Control that sticks out past the screen edge on that device is
  named, with the overflow in pixels.
- **Changed regions** are boxed in a diff image, listed as frame fractions, and saved as
  full-resolution *reference | current* crops.
- **Alerts** catch the classic silent failures: a black frame, a flat frame, a mesh with
  no material (renders plain white), a 3D scene without a current camera, nothing in
  view, a rigged model whose animations are not playing (T-pose), shot actions that
  point at missing nodes, and engine or script errors raised while the scene ran.
  A capture with an error alert is never saved as a reference.

<p align="center">
  <img src="docs/img/eyes-report.png" width="560" alt="gsk eyes HTML report with a reference/current slider, metrics and zoom crops">
  <img src="docs/img/eyes-demo.gif" width="250" alt="The report's slider sweeping between the reference and the current capture, then the diff view">
</p>

The HTML report has a reference/current slider, a side-by-side view and a diff view.
Next to it, `report.md` and `report.json` give an agent the same facts as text, with
paths to the **zoom crops**. That matters more than it sounds: when an agent opens a
1080x2400 screenshot, it gets downscaled until small text is unreadable. Crops at full
resolution are how it notices a clipped label or a price that runs out of its box.

Configure shots in `gsk.toml` at the project root:

```toml
[eyes]
devices = ["phone", "phone-small"]   # or "tablet", "phone-landscape", "1440x3200", ...
references = "tests/eyes"            # committed; gsk adds a .gdignore so they never ship
threshold = 0.99                     # minimum similarity
max_changed = 0.0005                 # at most 0.05% of pixels may change

[[eyes.shot]]
name = "menu"
scene = "res://scenes/menu.tscn"
wait = 1.0
zoom = [{ name = "title", rect = [0.1, 0.25, 0.8, 0.25] }]

[[eyes.shot]]
name = "shop-open"
scene = "res://scenes/menu.tscn"
actions = [{ tap = [0.5, 0.62] }, { wait = 0.5 }, { node = "Shop", call = "select_tab", args = [2] }]
```

Full reference: [docs/eyes.md](docs/eyes.md).

Software rendering is deterministic, which is what makes reference images work: the
sample project renders identical captures run after run, on the author's machine and in
a fresh Ubuntu 24.04 container with the official Godot download. References belong to a Godot
version (text rasterisation changes between releases): gsk records the version in
`godot-version.txt` next to them and says so when it differs, so regenerate them when you
upgrade. Software rendering shows layout, colour, composition and missing assets; it
does not show real-device performance. CI runs the capture script on Godot 4.2, 4.4, 4.5
and 4.6, and against a project that treats untyped or unsafe GDScript as errors.

## gsk doctor

<p align="center"><img src="docs/img/doctor.png" width="820" alt="gsk doctor output"></p>

Around 40 checks, grouped as:

- **Toolchain**: Godot binary and version match, export templates for that exact version
  (including .NET builds), Android SDK (platform-tools, build-tools, cmdline-tools,
  platform), JDK 17, Gradle build template version.
- **Preset**: package name (no `com.example`), version code, `arm64-v8a`, APK or AAB,
  target and min SDK (read from the build template you actually use), sensitive
  permissions, INTERNET when ads, analytics or HTTP are used, launcher icons.
- **Signing**: release keystore from environment variables, the preset or export
  credentials; password and alias present (values are never printed); keystore,
  `export_credentials.cfg` or a plain-text password in `export_presets.cfg` committed to
  git; `.godot/` in git.
- **Rendering**: ETC2/ASTC import, mobile renderer choice for low-end Mali and Adreno,
  OpenGL fallback, MSAA, Forward+-only environment effects the phone will ignore,
  stretch mode, orientation.
- **Assets and size**: uncompressed textures, oversized textures, normal maps, big WAVs,
  total imported size against Play's limits.

The Play numbers (API 36 since 31 Aug 2026, 16 KB page size, 500 MB base module, 200 MB
mobile-data warning) live in one file, [`playreq.py`](src/godot_ship_kit/playreq.py), each
with its source and the date it was checked. Full list: [docs/checks.md](docs/checks.md).

## gsk slim

<p align="center"><img src="docs/img/slim.png" width="820" alt="gsk slim output"></p>

On the tiny sample the safe tier makes the download slightly bigger and the GPU memory
much smaller: one smooth JPEG compresses better as Lossless than as ETC2. On real games
with many detailed model textures it usually shrinks both, see the numbers below.

The size of your PNG does not matter. What ships is the copy Godot makes in
`.godot/imported`, and its size comes from the `.import` settings: a 600 KB JPEG imported
*Lossless* at 2048 px can cost several MB in the APK and 16 MB of GPU memory on the phone.

`gsk slim` measures every imported file, maps it back to its source, and proposes changes:

- **safe**: VRAM compression (ETC2/ASTC) for textures used in 3D, which is what Godot does
  itself when it detects 3D use, plus normal-map compression. Big GPU memory savings;
  the export can get smaller or larger, and the report says which.
- **review** (`--aggressive`): size caps for 3D and 2D textures, Lossy WebP for big 2D art,
  QOA for long WAVs. These trade quality for size, so look at the result with `gsk eyes`.

It also lists **possibly unused** assets (no `res://` reference found, counting paths built
with `%s` or string concatenation) and **heavy models** (triangle counts read straight from
GLB files). `--apply` backs up every `.import` it touches, `--undo` restores them, and
`--reimport` runs Godot and prints the measured result.

On a real 340 MB project that had already been slimmed once, the safe tier predicted
6.3 MB less in the export and 169 MB less GPU memory; after re-importing, the export was
5.0 MB smaller.

## gsk build

```bash
export GODOT_ANDROID_KEYSTORE_RELEASE_PATH=/secure/upload.keystore
export GODOT_ANDROID_KEYSTORE_RELEASE_USER=upload
export GODOT_ANDROID_KEYSTORE_RELEASE_PASSWORD=...      # from your CI secret store
gsk build                       # release, path from the preset, verified
gsk build --debug -o build/test.apk
gsk verify build/game.aab       # verify an APK/AAB you already have
```

A release build stops before exporting when no keystore is configured. Godot's exit code
is not a reliable signal after a failed export, so `gsk build` treats a missing or stale
output file as a failure, keeps the log in `.gsk/logs/` (escape codes stripped, signing
values from the environment masked), and matches it against known export errors to print
a `hint:`. `--install-template` installs the Gradle build template first, which AAB
exports need.

Verification checks zip integrity, the signature (`apksigner`), that every 64-bit native
library is built for 16 KB pages (read straight from the ELF headers), `zipalign`, the
manifest (`aapt2` or `bundletool`: package, version, target SDK, permissions), and the
size against Play's limits. It breaks the archive down into native libraries, imported
assets, dex and resources, with the largest entries traced back to their source files.

## gsk play-checklist

`gsk play-checklist -o PLAY_CHECKLIST.md` writes a Markdown checklist for the Play Console:
build requirements, store listing text limits and graphic specs, Data safety (pre-filled
with the ad, analytics and billing SDKs it finds in `addons/` and the preset), content
rating, target audience, app access, and the 12-testers-for-14-days rule for new personal
accounts. It is guidance with links to Google's pages, not policy advice.

## Use it from an agent

**Claude Code** (plugin with the skill):

```text
/plugin marketplace add vladosskins178/godot-ship-kit
/plugin install godot-ship-kit@godot-ship-kit
```

Or copy only the skill: `gsk skill install` writes `~/.claude/skills/godot-ship-kit/SKILL.md`.

**Codex, Cursor, Gemini CLI, Aider and others**: append the instructions to your game's
`AGENTS.md` (or rules file):

```bash
gsk skill print >> AGENTS.md
```

The skill teaches the loop (doctor, slim, build, eyes, checklist), when to use `--json`,
to read the zoom crops before judging text, never to save a regression as a reference,
and to keep signing secrets in environment variables.

**MCP** (any client that speaks the Model Context Protocol): `gsk mcp` serves the commands
as tools over stdio. `gsk_eyes` returns a one-line verdict, the full JSON, and the
changed-region crops and diffs as images, so the model sees the change without opening files.

```bash
claude mcp add gsk -e GODOT=/path/to/godot -- gsk mcp      # Claude Code
```

```json
{ "mcpServers": { "gsk": { "command": "/absolute/path/to/gsk", "args": ["mcp"], "env": { "GODOT": "/path/to/godot" } } } }
```

Desktop clients often start servers without your shell's `PATH`, so use the absolute path
from `which gsk` there.

## How it fits with other Godot agent tools

Editor bridges such as godot-mcp launch or drive Godot to create nodes, run the project
and read its output. That is the right kind of tool while building a game. gsk covers the
part after it and needs neither an editor nor a GPU: checking a project against Android
and Play requirements, cutting its size, producing a signed and verified AAB, and
comparing what scenes look like on phone screens against approved references, in CI or
on a headless server. The two work fine side by side.

## CI

The repository is also a GitHub Action (Linux runners). It downloads Godot (checksum
verified), installs gsk and the software-rendering packages, and runs a gsk command:

```yaml
- uses: vladosskins178/godot-ship-kit@v0
  with:
    args: eyes --display xvfb --strict   # or: doctor --strict, slim, build --debug
    godot-version: "4.6.3"               # export-templates: "true" for gsk build
- uses: actions/upload-artifact@v4
  if: always()
  with:
    name: eyes-report
    path: .gsk/eyes
```

`gsk eyes` imports a fresh checkout by itself; `--strict` makes a missing reference fail
the job. This repository's own [CI](.github/workflows/ci.yml) uses the action on the sample
project, including on Godot 4.2, 4.4 and 4.5. [docs/ci.md](docs/ci.md) has a workflow that builds a signed AAB from repository
secrets.

## Requirements and limits

- Python 3.11+ and Godot 4.x (developed and tested with 4.6.3). Linux is the main
  target; `doctor`, `slim`, `build` and `verify` also run on macOS and Windows, where
  `eyes` uses the native display.
- `gsk eyes` captures what the scene renders under Mesa's llvmpipe. GPU-specific bugs
  and frame rate still need a real phone.
- Scenes that animate over time need a fixed `wait` and a looser per-shot `threshold`,
  or a paused state.
- `slim` estimates assume ETC2. Use `--reimport` for measured numbers.
- `eyes`, `build` and `slim --reimport` start Godot on the project, which runs the
  project's scripts. Use them on projects you trust, as you would the editor.

## Contributing

Issues and pull requests are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md). Each doctor
check needs a test, a fix text a person can act on and, where one exists, a link to
official docs.

## License

[MIT](LICENSE)
