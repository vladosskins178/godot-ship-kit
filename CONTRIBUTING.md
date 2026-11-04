# Contributing

Thanks for helping. Bug reports with a failing project are the most useful thing you
can send: a minimal Godot project (or the relevant `project.godot`, `export_presets.cfg`
with secrets removed, and `.import` files) plus the `gsk ... --json` output.

## Setup

```bash
git clone https://github.com/vladosskins178/godot-ship-kit
cd godot-ship-kit
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
```

For end-to-end tests you also need Godot 4.6 and, on Linux, `xvfb` and Mesa:

```bash
sudo apt install xvfb libgl1-mesa-dri libx11-6 libxcursor1 libxinerama1 libxrandr2 libxi6 libxkbcommon0
export GODOT=/path/to/Godot_v4.6.3-stable_linux.x86_64
$GODOT --headless --path examples/hello-ship --import
```

## Before you open a pull request

```bash
ruff check . && ruff format --check .
mypy
pytest                      # unit tests; Godot is faked
pytest -m godot             # end-to-end; needs $GODOT and xvfb
```

## Guidelines

- **Secrets.** Nothing may print, log or write keystore passwords, aliases or credential
  file contents. There are tests for this; extend them when you touch signing code.
- **Dependencies.** `doctor`, `slim`, `build`, `verify` and `play-checklist` use only the
  standard library. `eyes` may use Pillow and numpy. Ask in an issue before adding more.
- **New doctor checks** need a stable id (`area.name`), a severity that matches the impact
  (`error` only for things that break an export or a Play upload), a `fix` a person can
  act on, a docs link when one exists, a test, and a line in [docs/checks.md](docs/checks.md).
- **Play requirements** go in `src/godot_ship_kit/playreq.py` with the source URL. Update
  `CHECKED` whenever you change a number.
- **Godot side.** `src/godot_ship_kit/eyes/capture.gd` should keep working on Godot 4.2+.
  Avoid APIs added later, or guard them.
- **Reference images.** If a change alters how the sample renders, run
  `gsk eyes examples/hello-ship --update`, look at the new images, and commit them with
  the change that caused them.
- Keep the CLI output short and specific. Lead with what is wrong and how to fix it.

## Releasing

1. Update `version` in `pyproject.toml`, `src/godot_ship_kit/__init__.py` and
   `.claude-plugin/plugin.json`.
2. Move the "Unreleased" notes in `CHANGELOG.md` under the new version.
3. Tag `vX.Y.Z` and push the tag.
