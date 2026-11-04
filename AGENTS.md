# Notes for coding agents

This file is for agents working **on this repository**. To use godot-ship-kit inside a
game project, load [`skills/godot-ship-kit/SKILL.md`](skills/godot-ship-kit/SKILL.md)
instead (Claude Code picks it up as a skill; for Codex, Cursor, Gemini CLI and others,
append it to the game's own `AGENTS.md` with `gsk skill print >> AGENTS.md`).

## Layout

- `src/godot_ship_kit/` the `gsk` package. One module per command: `doctor.py`, `slim.py`,
  `build.py` + `archive.py` (export and verification), `checklist.py`, `eyes/`.
- `src/godot_ship_kit/godot_cfg.py` reads and edits Godot's config formats line by line.
  Keep edits minimal so `.import` files round-trip byte for byte.
- `src/godot_ship_kit/playreq.py` holds every Google Play number with its source URL and
  the date it was checked. Update the date when you change a number.
- `src/godot_ship_kit/eyes/capture.gd` runs inside Godot. It must work on Godot 4.2+.
- `examples/hello-ship/` is a small Godot 4.6 project used by tests, CI and the README.
  Its golden screenshots live in `examples/hello-ship/tests/eyes/`.

## Checks before you finish

```bash
pip install -e ".[dev]"
ruff check . && ruff format --check .
mypy
pytest                                   # unit tests, no Godot needed
GODOT=/path/to/godot pytest -m godot     # end-to-end, needs Godot 4.6 and xvfb
```

## Rules

- Never print, log or store signing secrets. Tests assert this; keep them passing.
- The core (`doctor`, `slim`, `build`, `verify`, `play-checklist`) is standard library
  only. Only `gsk eyes` may use Pillow and numpy.
- Every new doctor check needs a test, a `fix:` text a user can act on, and a docs link
  when one exists. Add it to `docs/checks.md`.
- If you change how the example renders, regenerate references with
  `gsk eyes examples/hello-ship --update` and look at the images before committing.
