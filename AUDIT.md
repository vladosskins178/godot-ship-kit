# Pre-release audit (v0.1.0)

Three rounds of independent review before the first public release, each run by a
reviewer that had not seen the code before, followed by fixes. This file records what was
found and what was done about it. Dates: 2026-11-04.

## How it was tested

- `ruff check`, `ruff format --check`, `mypy --strict`: clean.
- `pytest`: 95 tests. Unit tests fake Godot, the Android SDK, apksigner, aapt2, zipalign and
  bundletool; the end-to-end tests run a real Godot 4.6.3 under xvfb + llvmpipe.
- The sample project (`examples/hello-ship`) was exercised with every command: doctor, slim
  (apply, re-import, undo), debug APK and AAB builds with verification (bundletool,
  jarsigner, apksigner, zipalign), eyes (match, regressions, overflow, strict warnings),
  play-checklist and the MCP server.
- A fresh Ubuntu 24.04 container with the official Godot 4.6.3 download reproduced the
  committed reference screenshots exactly, and ran the GitHub Action's steps (checksum
  verification of Godot and the export templates included).
- The capture script was run on Godot 4.2.2, 4.4.1, 4.5.1 and 4.6.3, and in a project that
  treats untyped and unsafe GDScript as errors.
- A real, shipped 3D Godot 4.6 game (about 3 GB of sources, 1145 imported assets) was used
  read-only, on a copy: doctor (2 s), slim (2 s; safe tier applied and re-imported:
  predicted -6.3 MB / -169 MB GPU, measured -5.0 MB), eyes on its main scene (50 s), and
  verify on its released 352 MB APK.

## Round 1: security, code quality, docs and "would I star it"

| Area | Finding | Resolution |
| --- | --- | --- |
| Security (high) | `slim --undo` trusted paths in `manifest.json`; a crafted backup could overwrite files outside the project. | Only `.import` files inside both the backup and the project are restored; symlinks refused; nothing is restored if any entry is bad. Test added. |
| Security | `gsk.toml` `output` / `references` could point outside the project. | Must resolve inside the project (an explicit `--output` is still allowed). |
| Security | Symlinked or special `.import` files were followed (writes outside, `/dev/zero` memory exhaustion). | Only regular files up to 16 MB are read. |
| Security | Plain-text passwords in a committed `export_presets.cfg` were not flagged; build logs were echoed raw. | New `git.preset_secrets` check (values never shown); logs stripped of escape codes, signing values masked, written `0600`. |
| Security | `git` run on untrusted repositories. | `core.fsmonitor=false` on every call. |
| Correctness (high) | Name-based normal-map detection matched words such as `NormalTree`, which would have wrecked colour maps. | Token-based detection; name-only 3D guesses moved to the review tier. |
| Correctness | Standalone `.tres` materials, `Sprite3D` and `Decal` textures were not recognised as 3D. | Section-aware material parser. |
| Correctness | .NET builds looked for the wrong template folder; Godot 4.0-4.2 editor settings file name. | `.mono` templates, `godot.dotnet` check, per-version settings file. |
| Correctness | AAB 16 KB check gave false warnings; whole-file AAB size compared with the base-module limit. | 16 KB support read from ELF program headers of every 64-bit `.so`; sizes per module and asset pack. |
| Correctness | Capture script failed to parse in projects with strict GDScript warnings. | The shot list is now generated as fully typed GDScript; e2e test with strict warnings. |
| Correctness | A one-word UI change could still count as a match. | Stricter defaults plus a largest-changed-region rule. |
| Correctness | `slim --apply --json` printed text after the JSON; Windows line endings rewritten. | All JSON outputs are single documents (tested); line endings preserved. |
| UX | `gsk build` without a keystore ran a full export, then failed without a hint. | Release builds stop before exporting with clear next steps; `--install-template` added. |
| UX | `skill print` put YAML front matter into `AGENTS.md`. | Front matter dropped unless `--frontmatter`. |
| Docs | Install steps failed on externally managed Pythons; inconsistent check counts; screenshots showed noise. | venv in the quick start, numbers aligned, images regenerated. |
| Reach | No MCP server, no one-line CI integration. | `gsk mcp` and a composite GitHub Action added. |

## Round 2: security and correctness, docs and stars, an agent using the tool

| Area | Finding | Resolution |
| --- | --- | --- |
| Security (high) | MCP tool arguments starting with `--` were parsed as options (`--godot=/tmp/evil.sh` ran a binary). | Options use `--flag=value` and paths follow `--`; arguments are type-checked. Test with a hostile binary. |
| Robustness (high) | One malformed MCP request ended the server. | Every request is isolated; errors come back as tool errors or JSON-RPC errors. |
| Correctness | Bad `gsk.toml` values produced tracebacks (exit 1, indistinguishable from a verdict). | Full validation with clear messages and exit 2. |
| Correctness | Small colour shifts (delta 21-23) and a changed comma passed. | Per-pixel tolerance 24 to 8, region rule 40 to 20 px. All three cases now fail; unchanged scenes still match exactly. |
| Correctness | Actions on missing nodes or unknown input actions were silent, and broken shots could be saved as references. | They are error alerts, and error captures are never saved. |
| Correctness | AAB signatures and debuggable flags were not checked. | jarsigner verification; debuggable check on release. |
| Correctness | Unreadable ELF headers were reported as 16 KB-ready. | Reported as unknown. |
| Agent UX (high) | A card wider than a 720 px phone produced no alert; only the crops showed it. | New "UI runs off the screen" alert naming the Control and the overflow in pixels; e2e test. |
| Agent UX | Status was `pass` in JSON and `match` elsewhere; stale crops from earlier runs remained; relative paths in JSON. | `match` everywhere, per-shot cleanup at the start of a run, absolute paths. |
| Agent UX | `slim`'s safe tier can grow the download, which contradicted doctor's advice. | Both now say plainly: safe tier cuts GPU memory, review tier shrinks the download. |
| Docs (high) | A fresh clone (no `.godot/`) rendered garbage, so the documented CI snippet would fail. | `gsk eyes` imports a fresh checkout first; `--strict` makes missing references fail. |
| Docs | Hero GIF looked staged. | Rebuilt from real command output and the real crop, without added highlights. |
| Docs | Version support claim not backed by CI. | CI matrix job for Godot 4.2, 4.4 and 4.5. |
| Other | Action split arguments unsafely; MCP stdin could be read by child processes; JSON errors left stdout empty; debug and release builds overwrote each other. | `shlex` splitting, `stdin=DEVNULL` everywhere, JSON error objects, `-debug` suffix. |

## Round 3: full re-audit

Result: no launch blockers, quality 8.5/10. All earlier fixes were spot-checked and held.
No secrets in any output, no watermarks or private references in the repository or its
history, all commits authored as `vladosskins178`.

Fixed after the re-audit:

- A shot with an error alert and no reference was counted as `new` (summary said "0
  errors"). It is now `error`.
- Device sizes are limited to 64-8192 px; `--threshold`, `--max-3d`, `--max-2d`, `--top`
  are range-checked.
- `play-checklist -o` creates missing folders; `slim --undo <name>` lists the backups that
  exist; `verify` on a corrupt file no longer prints empty sections.
- References now record the Godot version (`godot-version.txt`); a changed shot under a
  different version gets an explanatory note.
- `SECURITY.md` scope, two undocumented check ids, the Action fails early on non-Linux
  runners, doctor screenshot regenerated.

## Known limitations and follow-ups

- **Before publishing:** push, wait for CI to pass on GitHub (the workflow, including the
  Godot 4.2/4.4/4.5 matrix, has only been run locally and in containers), then tag
  `v0.1.0` and a moving `v0` tag; the README's `uses: ...@v0` depends on it. Confirm the
  repository name `vladosskins178/godot-ship-kit` matches the URLs in the docs.
- Not on PyPI yet; install is from the Git URL.
- The 16 KB check covers LOAD segment alignment; Google's page also mentions RELRO
  alignment, which is not checked yet.
- `gsk eyes` uses software rendering: GPU-specific bugs and performance still need a
  real device. References belong to a Godot version.
- The Claude Code plugin manifest passes `claude plugin validate` (one warning: no icon).
