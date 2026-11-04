# gsk eyes reference

`gsk eyes` renders Godot scenes without a GPU, compares them with reference images and
reports what changed. This page covers configuration, actions, output and how the
comparison works.

## Running

```bash
gsk eyes [PROJECT] [--scene SCENE ...] [--device DEVICE ...] [--update]
         [--config FILE] [--output DIR] [--threshold FLOAT]
         [--rendering-method METHOD] [--rendering-driver DRIVER]
         [--gpu] [--display auto|xvfb|native] [--json]
```

| Option | Meaning |
| --- | --- |
| `--scene` | Capture these scenes instead of the shots in `gsk.toml`. `res://` paths or file paths. Repeatable. |
| `--device` | Device presets or `WIDTHxHEIGHT`. Repeatable. Overrides `devices` in the config. |
| `--update` | Save the captures as references instead of comparing. |
| `--rendering-method` | Defaults to the project's **mobile** renderer (`rendering/renderer/rendering_method.mobile`). |
| `--gpu` | Allow a hardware GPU. By default software rendering is forced (`LIBGL_ALWAYS_SOFTWARE=1`) so results do not depend on the machine. |
| `--display` | `auto` uses `$DISPLAY` when an X server answers there and falls back to `xvfb-run`; `xvfb` always uses a virtual display; `native` always uses `$DISPLAY` / Wayland. macOS and Windows use their own display unless `xvfb` is asked for. |

Exit codes: `0` everything matched (or was saved), `1` a shot changed or failed,
`2` usage error (no Godot, no xvfb, bad config).

## Devices

| Name | Size | Notes |
| --- | --- | --- |
| `phone` | 1080 x 2400 | 20:9, the most common modern Android shape |
| `phone-landscape` | 2400 x 1080 | |
| `phone-small` | 720 x 1600 | budget 20:9 phones |
| `phone-small-landscape` | 1600 x 720 | |
| `phone-16x9` | 1080 x 1920 | older phones |
| `phone-16x9-landscape` | 1920 x 1080 | |
| `tablet` | 1600 x 2560 | |
| `tablet-landscape` | 2560 x 1600 | |
| `WIDTHxHEIGHT` | any | e.g. `1440x3200` |

Without a `devices` setting, portrait projects use `phone` and landscape ones
`phone-landscape` (from `display/window/handheld/orientation`).

## gsk.toml

```toml
[eyes]
devices = ["phone", "tablet"]
references = "tests/eyes"        # relative to the project root
output = ".gsk/eyes"             # report folder
threshold = 0.99                 # minimum similarity (0..1)
max_changed = 0.0005             # maximum share of changed pixels (0..1); 0.0005 is about 1300 px on a 1080x2400 screen
tolerance = 24                   # per-channel difference (0..255) that counts as "changed"
region_pixels = 40               # one connected change this big fails the shot (0 = off)
wait = 1.0                       # default seconds to wait after a scene loads
boot_seconds = 120               # extra time budget for engine start and shader compiles

[[eyes.shot]]
name = "menu"                    # unique; used for file names
scene = "res://scenes/menu.tscn"
wait = 1.5
devices = ["phone"]              # optional: only on these devices
threshold = 0.97                 # optional per-shot override
max_changed = 0.01               # optional per-shot override
region_pixels = 200              # optional per-shot override
actions = [
  { tap = [0.5, 0.62] },                          # click at 50% x, 62% y of the screen
  { press = "ui_accept" },                        # press and release an input action
  { node = "Player", call = "jump", args = [] },  # call a method (path relative to the scene root)
  { node = "/root/Game", set = "paused", value = true },
  { wait = 0.5 },
]
zoom = [
  { name = "header", rect = [0.0, 0.0, 1.0, 0.2] },   # x, y, w, h as fractions of the frame
]
```

`tap` and `press` accept an optional `then_wait` (seconds, default 0.3).
Node paths starting with `/` are absolute (`/root/...`); others are relative to the
loaded scene.

## What gets written

```
.gsk/eyes/
  report.html            open in a browser
  report.md              short, for agents
  report.json            everything, machine-readable
  logs/<device>.log      full Godot output
  <device>/<shot>.png                the capture
  <device>/<shot>.json               scene facts (camera, meshes, materials, rigs)
  <device>/<shot>.diff.png           changed pixels in red, regions boxed
  <device>/<shot>.region1.png        reference | current crop of the largest change
  <device>/<shot>.zoom-<name>.png    full-resolution crop from your zoom list
tests/eyes/
  .gdignore                          keeps Godot from importing (and shipping) references
  <device>/<shot>.png
```

`.gsk/` gets its own `.gitignore`. Commit `tests/eyes/`.

## How the comparison works

1. **Changed pixels**: for every pixel, the largest RGB channel difference. Pixels above
   `tolerance` count as changed. `max_changed` is the allowed share.
2. **Similarity**: SSIM on luminance, computed on a copy downscaled about four times, with
   8x8 windows. Downscaling makes it about shapes and layout rather than single pixels.
3. A shot **matches** when similarity is at least `threshold`, the changed share is at
   most `max_changed`, and no single changed region holds `region_pixels` or more changed
   pixels. The last rule catches small, local edits (a digit in a price, a missing icon)
   that barely move the frame-wide numbers. A size mismatch never matches.
4. Changed pixels are grouped on a 24 px grid into regions; the largest ones are boxed in
   the diff image and saved as crops.

## Alerts

| Alert | Level | Trigger |
| --- | --- | --- |
| Frame is black | error | mean brightness under 6/255 and almost no contrast |
| Flat colour | warn | almost no contrast |
| Mostly pure white | warn | over half the pixels above 248/255 |
| No current Camera3D | warn | the scene has 3D nodes but no current camera |
| Mesh without material | warn | a visible mesh surface with no material, override or surface override |
| No mesh in view | warn | a camera exists, meshes exist, none intersects the view |
| Rig not animating | warn | an AnimationPlayer over a Skeleton3D has animations but none is playing and no active AnimationTree drives it |
| Tiny meshes | info | a mesh covers under 0.01% of the screen |
| Engine errors | warn | `ERROR` / `SCRIPT ERROR` lines printed while the shot ran |

## Determinism

Mesa's llvmpipe gives the same pixels for the same scene, Godot version and Mesa version.
Things that break determinism: animations and particles driven by time, random numbers
without a fixed seed, and anything that reads the wall clock. For those scenes, add a
small method that freezes the state you want to check and call it from an action
(`{ node = ".", call = "freeze_for_capture" }`), seed your RNG, or loosen `threshold` and
`max_changed` for that shot.

References are tied to the Godot version: text rasterisation changes between releases, so
an upgrade shows small differences on every screen with text. Regenerate references after
upgrading (look at a few crops first). Different Mesa versions can also differ by a few
antialiased pixels; generate references on the same image your CI uses.

## How the capture works

For each device, gsk writes a small GDScript (`.gsk/eyes/<device>/capture.gd`) with the
shot list compiled in as typed code, and starts Godot with
`godot --path <project> --resolution WxH -s capture.gd`, under `xvfb-run` when there is no
display, with `LIBGL_ALWAYS_SOFTWARE=1` unless `--gpu` is given. Because the shot data is
code rather than JSON parsed at runtime, the script also loads in projects whose warning
settings turn untyped or unsafe GDScript into errors. It runs your autoloads and scene
scripts like the game does, so only run it on projects you trust.
