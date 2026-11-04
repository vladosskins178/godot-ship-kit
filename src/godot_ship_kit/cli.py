"""Command-line entry point: ``gsk <command>``."""

from __future__ import annotations

import argparse
import shutil
import sys
from importlib import resources
from pathlib import Path
from typing import TextIO

from . import __version__
from .env import editor_settings_path, find_godot, write_editor_settings
from .godot_cfg import format_value
from .project import GodotProject, ProjectError
from .report import Severity, Style, dump_json, findings_markdown, human_bytes, print_findings

EXIT_OK, EXIT_FAIL, EXIT_USAGE = 0, 1, 2


def _out() -> TextIO:
    return sys.stdout


def _open_project(path: str) -> GodotProject:
    return GodotProject.open(path)


_JSON_MODE = False


def _fail(message: str, code: int = EXIT_USAGE) -> int:
    """Errors go to stderr with a ``gsk:`` prefix; with --json, stdout also gets an error object."""
    sys.stderr.write(f"gsk: {message}\n")
    if _JSON_MODE:
        dump_json({"ok": False, "error": message})
    return code


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


NO_GODOT = "Godot not found. Set GODOT=/path/to/godot or pass --godot."


# ---------------------------------------------------------------- doctor


def cmd_doctor(args: argparse.Namespace) -> int:
    from .doctor import run_doctor

    project = _open_project(args.project)
    findings, _ = run_doctor(project, args.godot, args.preset)
    failed = any(f.severity == Severity.ERROR for f in findings) or (args.strict and any(f.severity == Severity.WARN for f in findings))
    if args.json:
        dump_json({"project": str(project.root), "ok": not failed, "findings": [f.to_dict() for f in findings]})
    elif args.markdown:
        _out().write(findings_markdown(f"gsk doctor: {project.name}", findings) + "\n")
    else:
        print_findings(f"gsk doctor · {project.name}", findings, verbose=args.verbose)
    return EXIT_FAIL if failed else EXIT_OK


# ---------------------------------------------------------------- slim


def cmd_slim(args: argparse.Namespace) -> int:
    from . import slim

    project = _open_project(args.project)
    style = Style()
    out = _out()

    if args.undo is not None:
        try:
            restored = slim.undo(project, args.undo or None)
        except ValueError as exc:
            return _fail(str(exc), EXIT_FAIL)
        if not restored:
            names = [b.name for b in slim.list_backups(project)]
            if args.undo and names:
                return _fail(f"backup {args.undo!r} not found. Available: {', '.join(names)}", EXIT_FAIL)
            return _fail("nothing to undo: no gsk slim backups found.", EXIT_FAIL)
        code, info = _maybe_reimport(project, args)
        if args.json:
            dump_json({"restored": restored.name, **info})
        else:
            out.write(f"Restored .import files from {restored.name}.\n")
            _print_reimport(info, out)
        return code

    if min(args.max_3d, args.max_2d) < 16 or args.top < 1:
        return _fail("--max-3d / --max-2d must be at least 16 px and --top at least 1.")
    report = slim.analyze(project, max_3d=args.max_3d, max_2d=args.max_2d, top=args.top)
    chosen = [p for p in report.proposals if p.risk == slim.SAFE or args.aggressive]

    if args.json and not args.apply:
        dump_json(report.to_dict())
        return EXIT_OK

    if not args.json:
        out.write(style.bold(f"gsk slim · {project.name}") + "\n\n")
        if not report.has_cache:
            out.write(style.yellow("  No .godot/imported cache, so sizes are unknown. Open the project once or run with --reimport.\n\n"))
        out.write(f"  Imported assets that ship on Android: {style.bold(human_bytes(report.total_shipped))}\n")
        for kind, size in sorted(report.by_kind.items(), key=lambda kv: -kv[1]):
            if size:
                out.write(f"    {kind:<12} {human_bytes(size):>10}\n")
        out.write("\n" + style.bold("  Biggest assets") + "\n")
        for e in report.biggest:
            if e.shipped_bytes:
                out.write(f"    {human_bytes(e.shipped_bytes):>10}  {e.res_path}\n")
        out.write("\n" + style.bold("  Import changes") + "\n")
        if not report.proposals:
            out.write("    Nothing to change. Import settings already look lean.\n")
        groups = [(rule, risk) for rule in slim.RULES for risk in (slim.SAFE, slim.REVIEW)]
        for rule, risk in groups:
            title, why = slim.RULES[rule]
            group = sorted((p for p in report.proposals if p.rule == rule and p.risk == risk), key=lambda p: -p.saving)
            if not group:
                continue
            if rule == "vram-3d" and risk == slim.REVIEW:
                title += " (3D use guessed from file names)"
            tag = style.green("safe") if risk == slim.SAFE else style.yellow("review")
            disk = sum(p.disk_delta for p in group)
            gpu = sum(p.gpu_saving for p in group)
            effect = [f"export {_signed(disk)}"] + ([f"GPU memory -{human_bytes(gpu)}"] if gpu else [])
            out.write(f"\n    {tag}  {style.bold(title)}  {len(group)} file(s): {', '.join(effect)}\n")
            out.write(style.dim(f"          {why}") + "\n")
            for p in group[: args.top]:
                changes = ", ".join(f"{k}={format_value(v)}" for k, v in p.changes.items())
                gpu_note = f"GPU -{human_bytes(p.gpu_saving)}; " if p.gpu_saving else ""
                out.write(f"          {_signed(p.disk_delta):>10}  {p.entry.res_path}\n")
                out.write(style.dim(f"                      {gpu_note}{p.reason}; {changes}") + "\n")
            if len(group) > args.top:
                out.write(style.dim(f"          ... {len(group) - args.top} more (--top N or --json)") + "\n")
        if report.proposals:
            for label, aggressive in (("safe changes", False), ("with --aggressive", True)):
                disk, gpu = report.estimate(aggressive)
                out.write(f"\n  Estimate, {label}: export {style.bold(_signed(disk))}, GPU memory -{human_bytes(gpu)}")
            out.write("\n")
        if report.unreferenced:
            out.write("\n" + style.bold("  Possibly unused (no res:// reference found; check before deleting)") + "\n")
            for e in report.unreferenced:
                out.write(f"    {human_bytes(e.shipped_bytes):>10}  {e.res_path}\n")
        if report.heavy_models:
            out.write("\n" + style.bold("  Heavy models (over 50k triangles; on phones, props usually need 1-5k, heroes 10-30k)") + "\n")
            for e, tris in report.heavy_models:
                out.write(f"    {tris:>10,} tris  {e.res_path}\n")

    if not args.apply:
        if chosen and not args.json:
            out.write(
                f"\n  Run {style.cyan('gsk slim --apply' + (' --aggressive' if args.aggressive else ''))} "
                f"to write {len(chosen)} change(s) (backed up, undo with gsk slim --undo).\n"
            )
        return EXIT_OK

    backup = slim.apply(project, chosen)
    code, info = _maybe_reimport(project, args, before=report.total_shipped) if backup else (EXIT_OK, {})
    if args.json:
        dump_json({"applied": [p.to_dict() for p in chosen], "backup": str(backup) if backup else None, **info})
        return code
    if backup:
        out.write(f"\n  Wrote {len(chosen)} change(s). Backup: {backup.relative_to(project.root)}\n")
        _print_reimport(info, out)
    else:
        out.write("\n  Nothing to apply.\n")
    return code


def _signed(delta: int) -> str:
    if delta == 0:
        return "~0"
    return ("+" if delta > 0 else "-") + human_bytes(abs(delta))


def _maybe_reimport(project: GodotProject, args: argparse.Namespace, before: int | None = None) -> tuple[int, dict[str, object]]:
    """Re-import if asked. Returns an exit code and facts for text or JSON output."""
    import subprocess

    from . import slim

    if not args.reimport:
        return EXIT_OK, {"reimported": False}
    godot = find_godot(args.godot)
    if not godot:
        return EXIT_FAIL, {"reimported": False, "error": NO_GODOT}
    if not args.json:
        sys.stderr.write(f"  Re-importing with Godot {godot.short} (this can take a while)...\n")
    try:
        res = slim.reimport(godot.path, project)
    except subprocess.TimeoutExpired:
        return EXIT_FAIL, {"reimported": False, "error": "Godot re-import timed out"}
    project.reload_imports()
    after = sum(e.shipped_bytes for e in project.imports)
    if res.returncode != 0:
        tail = (res.stdout + res.stderr)[-1500:]
        return EXIT_FAIL, {"reimported": False, "error": f"Godot exited with {res.returncode}", "log_tail": tail}
    return EXIT_OK, {"reimported": True, "shipped_bytes_before": before, "shipped_bytes_after": after}


def _print_reimport(info: dict[str, object], out: TextIO) -> None:
    if info.get("error"):
        out.write(f"  Re-import failed: {info['error']}\n")
        if info.get("log_tail"):
            out.write(f"{info['log_tail']}\n")
    elif not info.get("reimported"):
        out.write("  Re-import to see the effect: open the editor, or rerun with --reimport.\n")
    else:
        after = int(str(info["shipped_bytes_after"]))
        before = info.get("shipped_bytes_before")
        if isinstance(before, int) and before:
            out.write(f"  Imported assets: {human_bytes(before)} -> {human_bytes(after)}\n")
        else:
            out.write(f"  Imported assets now: {human_bytes(after)}\n")


# ---------------------------------------------------------------- build / verify


def _print_archive(rep: object, out: TextIO, verbose: bool) -> None:
    from .archive import ArchiveReport

    assert isinstance(rep, ArchiveReport)
    style = Style()
    print_findings(f"Verify · {rep.path.name}", rep.findings, out, verbose=True)
    m = rep.manifest
    rows = []
    if m.get("package"):
        version = f"version {m.get('version_name', '?')}, code {m.get('version_code', '?')}"
        rows.append(("package", f"{m['package']} ({version})" + (", debuggable" if m.get("debuggable") else "")))
    if m.get("target_sdk"):
        rows.append(("sdk", f"target {m['target_sdk']}, min {m.get('min_sdk', '?')}"))
    if m.get("signer"):
        rows.append(("signer", str(m["signer"])))
    if m.get("permissions"):
        rows.append(("permissions", ", ".join(map(str, m["permissions"]))))
    if m.get("modules"):
        rows.append(("modules", ", ".join(f"{k} {human_bytes(v)}" for k, v in m["modules"].items())))
    if rows:
        out.write("\n")
        for key, val in rows:
            out.write(f"  {style.dim(f'{key:<12}')} {val}\n")
    if not rep.categories:
        return
    out.write("\n" + style.bold("  What is inside (compressed)") + "\n")
    for cat, size in sorted(rep.categories.items(), key=lambda kv: -kv[1]):
        out.write(f"    {human_bytes(size):>10}  {cat}\n")
    out.write("\n" + style.bold("  Largest entries") + "\n")
    for name, size, src in rep.biggest[: 15 if verbose else 8]:
        out.write(f"    {human_bytes(size):>10}  {name}" + (style.dim(f"  <- {src}") if src else "") + "\n")


def cmd_build(args: argparse.Namespace) -> int:
    from . import archive
    from .build import BuildError, default_output, export, preset_format, signing_summary
    from .env import android_toolchain

    project = _open_project(args.project)
    out = _out()
    style = Style()
    godot = find_godot(args.godot)
    if not godot:
        return _fail(NO_GODOT)
    preset = project.find_preset(args.preset)
    if not preset:
        return _fail(f"no export preset named {args.preset!r}." if args.preset else "no Android export preset. Run `gsk doctor`.")
    release = not args.debug
    output = Path(args.output).resolve() if args.output else default_output(project, preset, release)
    if not args.json:
        out.write(style.bold(f"gsk build · {project.name}") + "\n\n")
        out.write(f"  preset   {preset.name} ({preset_format(preset).upper()}, {'release' if release else 'debug'})\n")
        out.write(f"  godot    {godot.version}\n  signing  {signing_summary(release, project, preset)}\n  output   {output}\n\n")
        out.flush()
    try:
        res = export(godot, project, preset, output, release=release, timeout=args.timeout, install_template=args.install_template)
    except BuildError as exc:
        if args.json:
            dump_json({"ok": False, "error": str(exc), "log": str(exc.log) if exc.log else None, "hints": exc.hints})
        else:
            out.write(style.red(f"  {exc}") + "\n")
            if exc.log:
                tail = exc.log.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-15:]
                out.write(style.dim("\n".join("    " + t for t in tail)) + "\n")
                out.write(f"\n  Full log: {exc.log}\n")
            for h in exc.hints:
                out.write(f"  {style.cyan('hint:')} {h}\n")
        return EXIT_FAIL
    if not args.json:
        out.write(f"  Exported in {res.seconds:.0f}s ({human_bytes(output.stat().st_size)}). Log: {res.log}\n\n")
    if args.no_verify:
        if args.json:
            dump_json({"ok": True, "output": str(output), "log": str(res.log)})
        return EXIT_OK
    rep = archive.inspect(output, android_toolchain(godot), project, expect_release=release)
    if args.json:
        dump_json({"ok": rep.ok, "output": str(output), "log": str(res.log), "seconds": round(res.seconds), "verify": rep.to_dict()})
    else:
        _print_archive(rep, out, args.verbose)
    return EXIT_OK if rep.ok else EXIT_FAIL


def cmd_verify(args: argparse.Namespace) -> int:
    from . import archive
    from .env import android_toolchain

    path = Path(args.file)
    if not path.is_file():
        return _fail(f"{path} does not exist.")
    project = None
    if args.project:
        project = _open_project(args.project)
    rep = archive.inspect(path, android_toolchain(find_godot(args.godot)), project, expect_release=args.release or None)
    if args.json:
        dump_json(rep.to_dict())
    else:
        _print_archive(rep, _out(), args.verbose)
    return EXIT_OK if rep.ok else EXIT_FAIL


# ---------------------------------------------------------------- eyes


def cmd_eyes(args: argparse.Namespace) -> int:
    from .eyes.config import EyesConfigError, load_config
    from .eyes.report import write_reports
    from .eyes.runner import EyesError, check_display, run

    project = _open_project(args.project)
    out = _out()
    style = Style()
    godot = find_godot(args.godot)
    if not godot:
        return _fail(NO_GODOT)
    try:
        cfg = load_config(project, Path(args.config) if args.config else None, args.scene, args.device)
        check_display(args.display)
    except (EyesConfigError, EyesError) as exc:
        return _fail(str(exc))
    if args.output:
        cfg.output = Path(args.output).resolve()
    if args.threshold is not None:
        if not 0 <= args.threshold <= 1:
            return _fail("--threshold must be between 0 and 1.")
        cfg.threshold = args.threshold
    if not args.json:
        n = sum(1 for d in cfg.devices for s in cfg.shots if s.devices is None or d in s.devices)
        out.write(style.bold(f"gsk eyes · {project.name}") + "\n\n")
        out.write(
            f"  Capturing {n} shot(s) on {', '.join(cfg.devices)} with Godot {godot.short}"
            f" ({args.rendering_method or project.renderer_mobile}, {'GPU' if args.gpu else 'software rendering'})...\n"
        )
        out.flush()
    try:
        result = run(
            godot,
            project,
            cfg,
            update=args.update,
            rendering_method=args.rendering_method,
            rendering_driver=args.rendering_driver,
            software=not args.gpu,
            display=args.display,
        )
    except EyesError as exc:
        return _fail(str(exc))
    paths = write_reports(result)
    if args.json:
        from .eyes.report import to_json

        payload = to_json(result)
        payload["reports"] = {k: str(v) for k, v in paths.items()}
        dump_json(payload)
    else:
        colors = {"match": style.green, "changed": style.red, "error": style.red, "new": style.blue, "updated": style.blue}
        out.write("\n")
        for r in result.results:
            label = {"updated": "saved"}.get(r.status, r.status)
            metric = f"similarity {r.diff.similarity:.4f}, {r.diff.changed_ratio:.2%} px changed" if r.diff else ""
            out.write(f"  {colors[r.status](f'{label:>7}')}  {r.shot} · {r.device}  {style.dim(metric)}\n")
            for a in r.alerts:
                col = style.red if a.level == "error" else style.yellow if a.level == "warn" else style.blue
                first, *rest = a.message.splitlines()
                out.write(f"           {col(a.level)}: {first}\n")
                for line in rest[-4:]:
                    out.write(style.dim(f"             {line[:160]}") + "\n")
        c = result.counts()
        out.write(
            f"\n  {c['match']} match, {c['changed']} changed, {c['new']} new, {c['updated']} saved, "
            f"{_plural(c['error'], 'error')} in {result.seconds:.0f}s\n"
        )
        out.write(f"  Report: {paths['html']}\n  For agents: {paths['markdown']}\n")
        if c["new"] and not args.update:
            out.write(style.dim("  No reference yet for new shots. Check them, then run `gsk eyes --update` to save references.") + "\n")
    if args.strict and result.counts()["new"]:
        if not args.json:
            sys.stderr.write("gsk: --strict: shots without a reference count as failures.\n")
        return EXIT_FAIL
    return EXIT_FAIL if result.failed else EXIT_OK


# ---------------------------------------------------------------- checklist / configure / skill


def cmd_checklist(args: argparse.Namespace) -> int:
    from .checklist import build_checklist

    project = _open_project(args.project)
    text = build_checklist(project)
    if args.output:
        out_path = Path(args.output)
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(text, encoding="utf-8")
        except OSError as exc:
            return _fail(f"cannot write {out_path}: {exc.strerror or exc}")
        _out().write(f"Wrote {args.output}\n")
    else:
        _out().write(text)
    return EXIT_OK


def cmd_configure(args: argparse.Namespace) -> int:
    godot = find_godot(args.godot)
    path = Path(args.settings) if args.settings else editor_settings_path(godot)
    if path is None:
        if not godot:
            return _fail("cannot locate editor settings: Godot not found. Pass --settings or --godot.")
        from .env import _user_config_dir

        path = _user_config_dir() / godot.settings_file_name
    values: dict[str, str] = {}
    for flag, key in (
        ("android_sdk", "export/android/android_sdk_path"),
        ("java_sdk", "export/android/java_sdk_path"),
        ("debug_keystore", "export/android/debug_keystore"),
    ):
        val = getattr(args, flag)
        if val:
            p = Path(val).expanduser().resolve()
            if not p.exists():
                return _fail(f"{p} does not exist.")
            values[key] = str(p)
    if not values:
        _out().write(f"Editor settings: {path}\nNothing to change. Pass --android-sdk, --java-sdk or --debug-keystore.\n")
        return EXIT_OK
    write_editor_settings(path, values)
    for k, v in values.items():
        _out().write(f"  {k} = {v}\n")
    _out().write(f"Updated {path}\n")
    return EXIT_OK


def _skill_source() -> Path:
    packaged = Path(str(resources.files("godot_ship_kit").joinpath("skill/SKILL.md")))
    if packaged.is_file():
        return packaged
    repo = Path(__file__).resolve().parents[2] / "skills" / "godot-ship-kit" / "SKILL.md"
    return repo


def cmd_skill(args: argparse.Namespace) -> int:
    src = _skill_source()
    if not src.is_file():
        return _fail("SKILL.md is not bundled with this install.", EXIT_FAIL)
    text = src.read_text(encoding="utf-8")
    if args.action == "print":
        if not args.frontmatter and text.startswith("---"):
            # The YAML header is for skill loaders; AGENTS.md and rules files want plain Markdown.
            text = text.split("---", 2)[2].lstrip("\n")
        _out().write(text)
        return EXIT_OK
    dest_root = Path(args.dir).expanduser() if args.dir else Path.home() / ".claude" / "skills"
    dest = dest_root / "godot-ship-kit" / "SKILL.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    _out().write(f"Installed the godot-ship-kit skill to {dest}\n")
    return EXIT_OK


def cmd_mcp(args: argparse.Namespace) -> int:
    from .mcp import serve

    return serve()


# ---------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gsk",
        description="godot-ship-kit: take a Godot 4 project from repo to a Play-ready Android build.",
        epilog="Docs: https://github.com/vladosskins178/godot-ship-kit",
    )
    p.add_argument("--version", action="version", version=f"gsk {__version__}")
    sub = p.add_subparsers(dest="command", metavar="<command>")

    def common(sp: argparse.ArgumentParser, project: bool = True) -> None:
        if project:
            sp.add_argument("project", nargs="?", default=".", help="Godot project folder (default: current folder)")
        sp.add_argument("--godot", help="path to the Godot 4 binary (default: $GODOT, then PATH)")
        sp.add_argument("--json", action="store_true", help="machine-readable output")

    d = sub.add_parser("doctor", help="check project, export preset, SDK/JDK, signing and assets")
    common(d)
    d.add_argument("--preset", help="export preset name (default: first Android preset)")
    d.add_argument("--markdown", action="store_true", help="Markdown output")
    d.add_argument("--strict", action="store_true", help="exit non-zero on warnings too")
    d.add_argument("-v", "--verbose", action="store_true", help="also list passing checks")
    d.set_defaults(func=cmd_doctor)

    s = sub.add_parser("slim", help="find what makes the build big and fix import settings")
    common(s)
    s.add_argument("--apply", action="store_true", help="write the proposed .import changes (with backup)")
    s.add_argument("--aggressive", action="store_true", help="include 'review' changes (size caps, lossy 2D, QOA audio)")
    s.add_argument("--max-3d", type=int, default=1024, metavar="PX", help="size cap for 3D textures (default 1024)")
    s.add_argument("--max-2d", type=int, default=2048, metavar="PX", help="size cap for 2D textures (default 2048)")
    s.add_argument("--top", type=int, default=15, help="how many items to list")
    s.add_argument("--reimport", action="store_true", help="re-import with Godot afterwards and show the new size")
    s.add_argument("--undo", nargs="?", const="", metavar="BACKUP", help="restore the latest (or named) backup")
    s.set_defaults(func=cmd_slim)

    b = sub.add_parser("build", help="headless export (APK/AAB), signing via env vars, verification")
    common(b)
    b.add_argument("--preset", help="export preset name (default: first Android preset)")
    b.add_argument("--debug", action="store_true", help="debug export (default: release)")
    b.add_argument("-o", "--output", help="output .apk/.aab path (default: preset export path or build/)")
    b.add_argument("--timeout", type=int, default=3600, help="seconds before the export is killed")
    b.add_argument("--no-verify", action="store_true", help="skip archive verification")
    b.add_argument(
        "--install-template",
        action="store_true",
        help="install the Android Gradle build template into android/ first (needed once for Gradle/AAB builds)",
    )
    b.add_argument("-v", "--verbose", action="store_true", help="list more of the largest entries")
    b.set_defaults(func=cmd_build)

    v = sub.add_parser("verify", help="verify an existing APK/AAB (zip, signature, alignment, target SDK, size)")
    v.add_argument("file", help="the .apk or .aab to check")
    v.add_argument("--project", help="project folder, to map imported files back to assets")
    v.add_argument("--godot", help="Godot binary, used to find the Android SDK it is configured with")
    v.add_argument("--release", action="store_true", help="fail if signed with a debug certificate or debuggable")
    v.add_argument("--json", action="store_true", help="machine-readable output")
    v.add_argument("-v", "--verbose", action="store_true", help="list more of the largest entries")
    v.set_defaults(func=cmd_verify)

    e = sub.add_parser("eyes", help="render scenes headlessly at phone resolutions and diff against references")
    common(e)
    e.add_argument("--scene", action="append", help="scene to capture (res:// or file path); repeatable")
    e.add_argument("--device", action="append", help="phone, phone-landscape, phone-small, tablet, ... or WxH; repeatable")
    e.add_argument("--config", help="config file (default: <project>/gsk.toml)")
    e.add_argument("--update", action="store_true", help="save the captures as the new references")
    e.add_argument("--threshold", type=float, help="minimum similarity to count as a match (default 0.99)")
    e.add_argument("--output", help="report folder (default: <project>/.gsk/eyes)")
    e.add_argument("--rendering-method", help="override renderer (default: the project's mobile renderer)")
    e.add_argument("--rendering-driver", help="override driver, e.g. opengl3 or vulkan")
    e.add_argument("--gpu", action="store_true", help="allow hardware GPU instead of forcing software rendering")
    e.add_argument("--strict", action="store_true", help="also fail when a shot has no reference yet (use in CI)")
    e.add_argument(
        "--display",
        choices=["auto", "xvfb", "native"],
        default="auto",
        help="auto: $DISPLAY if reachable, else xvfb-run; xvfb: always a virtual display; native: always $DISPLAY/Wayland",
    )
    e.set_defaults(func=cmd_eyes)

    c = sub.add_parser("play-checklist", help="Play Console launch checklist, filled in from the project")
    c.add_argument("project", nargs="?", default=".", help="Godot project folder (default: current folder)")
    c.add_argument("-o", "--output", help="write to a file instead of stdout")
    c.set_defaults(func=cmd_checklist)

    cf = sub.add_parser("configure", help="write Android SDK / JDK / debug keystore paths into Godot's editor settings")
    cf.add_argument("--android-sdk", help="Android SDK folder (with platform-tools and build-tools)")
    cf.add_argument("--java-sdk", help="JDK 17 folder (the one containing bin/java)")
    cf.add_argument("--debug-keystore", help="debug.keystore used for debug exports")
    cf.add_argument("--settings", help="editor settings file to edit (default: the one Godot uses)")
    cf.add_argument("--godot", help="Godot binary, to pick the right editor settings file")
    cf.set_defaults(func=cmd_configure)

    sk = sub.add_parser("skill", help="install or print the agent skill (SKILL.md)")
    sk.add_argument("action", choices=["install", "print"])
    sk.add_argument("--dir", help="skills folder (default: ~/.claude/skills)")
    sk.add_argument("--frontmatter", action="store_true", help="print: keep the YAML header (for skill loaders)")
    sk.set_defaults(func=cmd_skill)

    m = sub.add_parser(
        "mcp",
        help="run as an MCP server over stdio (gsk_doctor, gsk_slim, gsk_build, gsk_verify, gsk_eyes, gsk_play_checklist)",
    )
    m.set_defaults(func=cmd_mcp)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return EXIT_USAGE
    global _JSON_MODE
    _JSON_MODE = bool(getattr(args, "json", False))
    try:
        return int(args.func(args))
    except ProjectError as exc:
        return _fail(str(exc))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
