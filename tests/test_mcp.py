from __future__ import annotations

import io
import json
from pathlib import Path

from godot_ship_kit import mcp


def _session(*messages: dict) -> list[dict]:  # type: ignore[type-arg]
    inp = io.StringIO("".join(json.dumps(m) + "\n" for m in messages) + "not json\n")
    out = io.StringIO()
    mcp.serve(inp, out)
    return [json.loads(line) for line in out.getvalue().splitlines()]


def test_handshake_and_tool_list() -> None:
    replies = _session(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "ping"},
        {"jsonrpc": "2.0", "id": 4, "method": "nope"},
    )
    assert replies[0]["result"]["protocolVersion"] == "2025-03-26"
    assert replies[0]["result"]["serverInfo"]["name"] == "godot-ship-kit"
    names = {t["name"] for t in replies[1]["result"]["tools"]}
    assert {"gsk_doctor", "gsk_slim", "gsk_build", "gsk_verify", "gsk_eyes", "gsk_play_checklist"} == names
    assert replies[2]["result"] == {}
    assert replies[3]["error"]["code"] == -32601
    assert replies[4]["error"]["code"] == -32700  # the "not json" line


def test_tool_calls(example: Path, toolchain) -> None:  # type: ignore[no-untyped-def]
    toolchain()
    replies = _session(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "gsk_doctor", "arguments": {"project": str(example)}}},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "gsk_play_checklist", "arguments": {"project": str(example)}},
        },
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "gsk_doctor", "arguments": {"project": "/nonexistent"}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "gsk_doctor", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "rm_rf", "arguments": {}}},
    )
    doctor = json.loads(replies[0]["result"]["content"][0]["text"])
    assert doctor["findings"] and replies[0]["result"]["isError"] is False
    assert "Google Play launch checklist" in replies[1]["result"]["content"][0]["text"]
    assert replies[2]["result"]["isError"] is True and "No project.godot" in replies[2]["result"]["content"][0]["text"]
    assert replies[3]["result"]["isError"] is True
    assert replies[4]["error"]["code"] == -32602


def test_eyes_images_are_attached(tmp_path: Path) -> None:
    from PIL import Image

    (tmp_path / "phone").mkdir()
    Image.new("RGB", (2000, 1000), (200, 10, 10)).save(tmp_path / "phone" / "menu.region1.png")
    Image.new("RGB", (100, 200)).save(tmp_path / "phone" / "menu.diff.png")
    payload = {
        "output_dir": str(tmp_path),
        "results": [
            {
                "shot": "menu",
                "device": "phone",
                "status": "changed",
                "region_crops": ["phone/menu.region1.png"],
                "diff_image": "phone/menu.diff.png",
                "alerts": [],
            },
            {"shot": "ok", "device": "phone", "status": "pass", "image": "phone/ok.png", "alerts": []},
        ],
    }
    blocks = mcp._eyes_images(payload)
    images = [b for b in blocks if b["type"] == "image"]
    assert len(images) == 2 and images[0]["mimeType"] == "image/png"
