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


class ArgumentError(ValueError):
    pass


def _s(a: dict[str, Any], key: str, required: bool = False) -> str | None:
    v = a.get(key)
    if v is None:
        if required:
            raise ArgumentError(f"missing argument '{key}'")
        return None
    if not isinstance(v, str) or not v.strip():
        raise ArgumentError(f"'{key}' must be a non-empty string")
    return v


def _flag(a: dict[str, Any], key: str) -> bool:
    v = a.get(key, False)
    if not isinstance(v, bool):
        raise ArgumentError(f"'{key}' must be true or false")
    return v


def _strings(a: dict[str, Any], key: str) -> list[str]:
    v = a.get(key) or []
    if not isinstance(v, list) or not all(isinstance(x, str) and x for x in v):
        raise ArgumentError(f"'{key}' must be a list of strings")
    return v


def _opt(flag: str, value: str | None) -> list[str]:
    # "--flag=value" keeps values that start with "-" from being read as options.
    return [f"{flag}={value}"] if value is not None else []


def _argv(name: str, a: dict[str, Any]) -> list[str]:
    """Translate tool arguments into a gsk command line. Paths go after "--"."""
    if name == "gsk_doctor":
        return ["doctor", "--json", *_opt("--preset", _s(a, "preset")), "--", str(_s(a, "project", True))]
    if name == "gsk_slim":
        flags = [f"--{k}" for k in ("apply", "aggressive", "reimport") if _flag(a, k)]
        return ["slim", "--json", *flags, "--", str(_s(a, "project", True))]
    if name == "gsk_build":
        argv = ["build", "--json"]
        if _flag(a, "debug"):
            argv.append("--debug")
        argv += _opt("--preset", _s(a, "preset")) + _opt("--output", _s(a, "output"))
        return [*argv, "--", str(_s(a, "project", True))]
    if name == "gsk_verify":
        return ["verify", "--json", *_opt("--project", _s(a, "project")), "--", str(_s(a, "file", True))]
    if name == "gsk_eyes":
        argv = ["eyes", "--json"]
        for scene in _strings(a, "scenes"):
            argv += _opt("--scene", scene)
        for device in _strings(a, "devices"):
            argv += _opt("--device", device)
        if _flag(a, "update"):
            argv.append("--update")
        return [*argv, "--", str(_s(a, "project", True))]
    if name == "gsk_play_checklist":
        return ["play-checklist", "--", str(_s(a, "project", True))]
    raise ArgumentError(f"unknown tool {name}")


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
    base = Path(str(payload.get("output_dir", "")))  # paths in the payload are absolute; joining keeps them
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
    seen: set[str] = set()
    for label, path in picks:
        if len(seen) >= MAX_IMAGES:
            break
        img = _image_block(path)
        if img and img["data"] not in seen:  # identical captures from different shots are sent once
            seen.add(img["data"])
            blocks += [{"type": "text", "text": f"{label}: {path}"}, img]
    return blocks


def _eyes_summary(payload: dict[str, Any]) -> str:
    """One readable verdict before the JSON: counts, then each shot that needs a look."""
    c = payload.get("counts", {})
    lines = [
        f"{c.get('match', 0)} match, {c.get('changed', 0)} changed, {c.get('new', 0)} new, "
        f"{c.get('updated', 0)} saved, {c.get('error', 0)} error(s). Full report: {payload.get('output_dir', '')}/report.md"
    ]
    for r in payload.get("results", []):
        alerts = [a["message"].splitlines()[0] for a in r.get("alerts", []) if a.get("level") in ("warn", "error")]
        if r.get("status") != "match" or alerts:
            lines.append(f"- {r.get('shot')} on {r.get('device')}: {r.get('status')}" + "".join(f"; {a}" for a in alerts))
    return "\n".join(lines)


def call_tool(name: str, arguments: Any, runner: Callable[[list[str]], int] | None = None) -> dict[str, Any]:
    from .cli import main

    run = runner or main
    try:
        if not isinstance(arguments, dict):
            raise ArgumentError("arguments must be an object")
        argv = _argv(name, arguments)
    except ArgumentError as exc:
        return {"content": [{"type": "text", "text": f"Invalid arguments: {exc}"}], "isError": True}
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = run(argv)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 2
        except Exception as exc:  # a tool failure must never take the server down
            err.write(f"gsk: internal error: {type(exc).__name__}: {exc}\n")
            code = 2
    text = out.getvalue().strip() or err.getvalue().strip()
    content: list[dict[str, Any]] = [{"type": "text", "text": text}]
    if err.getvalue().strip() and out.getvalue().strip():
        content.append({"type": "text", "text": err.getvalue().strip()})
    if name == "gsk_eyes" and out.getvalue().strip():
        with contextlib.suppress(ValueError, TypeError, KeyError, AttributeError):
            payload = json.loads(out.getvalue())
            if "results" in payload:
                content = [{"type": "text", "text": _eyes_summary(payload)}, *content, *_eyes_images(payload)]
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
    method = message.get("method")
    msg_id = message.get("id")
    if msg_id is None or not isinstance(method, str):  # notifications, and responses to us
        return
    params = message.get("params") or {}
    if not isinstance(params, dict):
        _reply(stream, msg_id, error={"code": -32602, "message": "params must be an object"})
        return
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
            try:
                handle(message, out)
            except Exception as exc:  # keep serving after an unexpected error
                sys.stderr.write(f"gsk mcp: {type(exc).__name__}: {exc}\n")
                if message.get("id") is not None:
                    _reply(out, message.get("id"), error={"code": -32603, "message": "Internal error"})
    return 0
