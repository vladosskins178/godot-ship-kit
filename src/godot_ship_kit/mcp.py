"""``gsk mcp``: the gsk commands as Model Context Protocol tools over stdio.

A small, dependency-free server: newline-delimited JSON-RPC 2.0 on stdin/stdout,
logs on stderr. Each tool runs the matching ``gsk`` command with ``--json`` and
returns its JSON. ``gsk_eyes`` also returns the most useful images (changed-region
crops, diffs, new captures) as image content, so a model can look at them directly.
"""

from __future__ import annotations

import base64
import contextlib
import io
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, TextIO

from . import __version__

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
MAX_IMAGES = 4
IMAGE_LONG_SIDE = 768

INSTRUCTIONS = (
    "Tools for shipping Godot 4 games to Android. Run gsk_doctor before exporting, gsk_slim for size, "
    "gsk_build to export and verify, and gsk_eyes after any visual change to see the result at phone "
    "resolution. Eyes returns crops of changed regions as images; look at them before judging a change."
)

_PROJECT = {"type": "string", "description": "Path to the Godot project folder (contains project.godot)."}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "gsk_doctor",
        "description": "Check a Godot project's Android export setup: preset, SDK/JDK/templates, signing (secrets never "
        "returned), Play target API level, renderer, textures and size. Every finding has a fix.",
        "inputSchema": {
            "type": "object",
            "properties": {"project": _PROJECT, "preset": {"type": "string", "description": "Export preset name."}},
            "required": ["project"],
        },
    },
    {
        "name": "gsk_slim",
        "description": "Measure what ships in the Android build and propose .import changes to shrink it. With apply=true "
        "the safe changes are written (backed up; undo with gsk slim --undo).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": _PROJECT,
                "apply": {"type": "boolean", "default": False},
                "aggressive": {"type": "boolean", "default": False, "description": "Include quality-trading changes."},
                "reimport": {"type": "boolean", "default": False, "description": "Re-import with Godot and measure."},
            },
            "required": ["project"],
        },
    },
    {
        "name": "gsk_build",
        "description": "Export an APK/AAB headlessly and verify it (signature, 16 KB pages, target SDK, size). Release "
        "builds read the keystore from GODOT_ANDROID_KEYSTORE_* environment variables.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": _PROJECT,
                "debug": {"type": "boolean", "default": False},
                "preset": {"type": "string"},
                "output": {"type": "string", "description": "Output .apk/.aab path."},
            },
            "required": ["project"],
        },
    },
    {
        "name": "gsk_verify",
        "description": "Verify an existing APK or AAB: zip integrity, signature, 16 KB page support, target SDK, "
        "permissions and a size breakdown.",
        "inputSchema": {
            "type": "object",
            "properties": {"file": {"type": "string"}, "project": _PROJECT},
            "required": ["file"],
        },
    },
    {
        "name": "gsk_eyes",
        "description": "Render Godot scenes at phone resolutions without a GPU, compare with reference screenshots, and "
        "return the verdict plus images of what changed. Use after any UI, material, model, lighting or camera change. "
        "update=true saves the captures as the new references (only after you have checked them).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": _PROJECT,
                "scenes": {"type": "array", "items": {"type": "string"}, "description": "res:// scenes; default: gsk.toml"},
                "devices": {"type": "array", "items": {"type": "string"}, "description": "phone, tablet, ... or WxH"},
                "update": {"type": "boolean", "default": False},
            },
            "required": ["project"],
        },
    },
    {
        "name": "gsk_play_checklist",
        "description": "A Google Play Console launch checklist in Markdown, filled in from the project.",
        "inputSchema": {"type": "object", "properties": {"project": _PROJECT}, "required": ["project"]},
    },
]


def _argv(name: str, a: dict[str, Any]) -> list[str]:
    if name == "gsk_doctor":
        return ["doctor", a["project"], "--json", *(["--preset", a["preset"]] if a.get("preset") else [])]
    if name == "gsk_slim":
        flags = [f"--{k}" for k in ("apply", "aggressive", "reimport") if a.get(k)]
        return ["slim", a["project"], "--json", *flags]
    if name == "gsk_build":
        argv = ["build", a["project"], "--json"]
        if a.get("debug"):
            argv.append("--debug")
        if a.get("preset"):
            argv += ["--preset", a["preset"]]
        if a.get("output"):
            argv += ["-o", a["output"]]
        return argv
    if name == "gsk_verify":
        return ["verify", a["file"], "--json", *(["--project", a["project"]] if a.get("project") else [])]
    if name == "gsk_eyes":
        argv = ["eyes", a["project"], "--json"]
        for s in a.get("scenes") or []:
            argv += ["--scene", s]
        for d in a.get("devices") or []:
            argv += ["--device", d]
        if a.get("update"):
            argv.append("--update")
        return argv
    if name == "gsk_play_checklist":
        return ["play-checklist", a["project"]]
    raise KeyError(name)


def _image_block(path: Path) -> dict[str, Any] | None:
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - eyes needs Pillow anyway
        return None
    try:
        with Image.open(path) as src:
            rgb = src.convert("RGB")
        rgb.thumbnail((IMAGE_LONG_SIDE, IMAGE_LONG_SIDE))
        buf = io.BytesIO()
        rgb.save(buf, "PNG", optimize=True)
    except OSError:
        return None
    return {"type": "image", "data": base64.b64encode(buf.getvalue()).decode("ascii"), "mimeType": "image/png"}


def _eyes_images(payload: dict[str, Any]) -> list[dict[str, Any]]:
    base = Path(str(payload.get("output_dir", "")))
    picks: list[tuple[str, Path]] = []
    for r in payload.get("results", []):
        label = f"{r.get('shot')} on {r.get('device')} ({r.get('status')})"
        if r.get("status") == "changed":
            for rel in (r.get("region_crops") or [])[:1] + ([r["diff_image"]] if r.get("diff_image") else []):
                picks.append((label, base / rel))
            continue
        alerted = any(a.get("level") in ("warn", "error") for a in r.get("alerts", []))
        if r.get("image") and (r.get("status") in ("new", "error") or alerted):
            picks.append((label, base / r["image"]))
    blocks: list[dict[str, Any]] = []
    for label, path in picks[:MAX_IMAGES]:
        img = _image_block(path)
        if img:
            blocks += [{"type": "text", "text": f"{label}: {path.name}"}, img]
    return blocks


def call_tool(name: str, arguments: dict[str, Any], runner: Callable[[list[str]], int] | None = None) -> dict[str, Any]:
    from .cli import main

    run = runner or main
    try:
        argv = _argv(name, arguments)
    except KeyError as exc:
        return {"content": [{"type": "text", "text": f"Missing or unknown argument: {exc}"}], "isError": True}
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = run(argv)
        except SystemExit as exc:
            code = int(exc.code or 0)
    text = out.getvalue().strip() or err.getvalue().strip()
    content: list[dict[str, Any]] = [{"type": "text", "text": text}]
    if err.getvalue().strip() and out.getvalue().strip():
        content.append({"type": "text", "text": err.getvalue().strip()})
    if name == "gsk_eyes" and out.getvalue().strip():
        with contextlib.suppress(ValueError):
            content += _eyes_images(json.loads(out.getvalue()))
    # Exit code 1 is a verdict (checks failed, shots changed), not a tool failure.
    return {"content": content, "isError": code == 2}


def _reply(stream: TextIO, msg_id: Any, result: Any = None, error: dict[str, Any] | None = None) -> None:
    msg: dict[str, Any] = {"jsonrpc": "2.0", "id": msg_id}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    stream.write(json.dumps(msg) + "\n")
    stream.flush()


def handle(message: dict[str, Any], stream: TextIO) -> None:
    method = message.get("method", "")
    msg_id = message.get("id")
    if msg_id is None:  # a notification, e.g. notifications/initialized
        return
    params = message.get("params") or {}
    if method == "initialize":
        requested = params.get("protocolVersion")
        version = requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        _reply(
            stream,
            msg_id,
            {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "godot-ship-kit", "version": __version__},
                "instructions": INSTRUCTIONS,
            },
        )
    elif method == "ping":
        _reply(stream, msg_id, {})
    elif method == "tools/list":
        _reply(stream, msg_id, {"tools": TOOLS})
    elif method == "tools/call":
        name = str(params.get("name", ""))
        if name not in {t["name"] for t in TOOLS}:
            _reply(stream, msg_id, error={"code": -32602, "message": f"Unknown tool: {name}"})
            return
        _reply(stream, msg_id, call_tool(name, params.get("arguments") or {}))
    else:
        _reply(stream, msg_id, error={"code": -32601, "message": f"Method not found: {method}"})


def serve(stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
    inp, out = stdin or sys.stdin, stdout or sys.stdout
    for line in inp:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            _reply(out, None, error={"code": -32700, "message": "Parse error"})
            continue
        if isinstance(message, dict):
            handle(message, out)
    return 0
