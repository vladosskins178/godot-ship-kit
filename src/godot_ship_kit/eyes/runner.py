"""Run Godot under a virtual display, capture shots, compare, and collect results."""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

from ..env import GodotBinary
from ..project import GodotProject
from . import compare as cmp
from .config import EyesConfig, Shot, device_size

MARK = "[gsk-eyes]"
ERROR_LINE = re.compile(r"^(SCRIPT ERROR|ERROR|USER ERROR|Parse Error|Invalid)\b|^\s*at: ", re.M)


class EyesError(Exception):
    pass


@dataclass
class Alert:
    level: str  # "error" | "warn" | "info"
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"level": self.level, "message": self.message}


@dataclass
class ShotResult:
    shot: str
    device: str
    scene: str
    status: str  # pass | changed | new | updated | error
    image: Path | None = None
    reference: Path | None = None
    diff_image: Path | None = None
    diff: cmp.Diff | None = None
    stats: cmp.FrameStats | None = None
    facts: dict[str, Any] = field(default_factory=dict)
    alerts: list[Alert] = field(default_factory=list)
    zooms: list[tuple[str, Path]] = field(default_factory=list)
    region_crops: list[Path] = field(default_factory=list)
    log_lines: list[str] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return self.status in ("changed", "error") or any(a.level == "error" for a in self.alerts)

    def to_dict(self, base: Path) -> dict[str, Any]:
        def rel(p: Path | None) -> str | None:
            return os.path.relpath(p, base) if p else None

        return {
            "shot": self.shot,
            "device": self.device,
            "scene": self.scene,
            "status": self.status,
            "image": rel(self.image),
            "reference": rel(self.reference),
            "diff_image": rel(self.diff_image),
            "diff": self.diff.to_dict() if self.diff else None,
            "stats": self.stats.to_dict() if self.stats else None,
            "facts": self.facts,
            "alerts": [a.to_dict() for a in self.alerts],
            "zooms": [{"name": n, "image": rel(p)} for n, p in self.zooms],
            "region_crops": [rel(p) for p in self.region_crops],
            "log": self.log_lines[:30],
        }


@dataclass
class EyesRun:
    project: GodotProject
    config: EyesConfig
    godot: GodotBinary
    results: list[ShotResult]
    rendering_method: str
    started: str
    seconds: float
    updated_references: bool = False

    @property
    def failed(self) -> bool:
        return any(r.failed for r in self.results)

    def counts(self) -> dict[str, int]:
        out = {"pass": 0, "changed": 0, "new": 0, "updated": 0, "error": 0}
        for r in self.results:
            out[r.status] = out.get(r.status, 0) + 1
        return out


def capture_script() -> Path:
    return Path(str(resources.files("godot_ship_kit.eyes").joinpath("capture.gd")))


def display_usable(display: str | None) -> bool:
    """True if ``$DISPLAY`` points at an X server that is actually there."""
    if not display:
        return False
    m = re.fullmatch(r"(?P<host>[^:]*):(?P<num>\d+)(?:\.\d+)?", display)
    if not m:
        return False
    host, num = m.group("host"), int(m.group("num"))
    if host in ("", "unix"):
        return Path(f"/tmp/.X11-unix/X{num}").exists()
    try:
        with socket.create_connection((host, 6000 + num), timeout=0.5):
            return True
    except OSError:
        return False


def _display_wrapper(width: int, height: int, mode: str = "auto") -> list[str]:
    if sys.platform in ("darwin", "win32") and mode != "xvfb":
        return []  # macOS and Windows always have a native display
    if mode == "native" or (mode == "auto" and (display_usable(os.environ.get("DISPLAY")) or os.environ.get("WAYLAND_DISPLAY"))):
        return []
    xvfb = shutil.which("xvfb-run")
    if not xvfb:
        raise EyesError(
            "No display and no xvfb-run found. Install it (Debian/Ubuntu: sudo apt install xvfb "
            "libgl1-mesa-dri) or run inside a desktop session."
        )
    return [xvfb, "-a", "-s", f"-screen 0 {width + 64}x{height + 64}x24"]


def _ensure_gdignore(folder: Path) -> None:
    """Keep Godot from importing screenshots into the game (they would ship in the APK)."""
    folder.mkdir(parents=True, exist_ok=True)
    marker = folder / ".gdignore"
    if not marker.exists():
        marker.write_text("", encoding="utf-8")


def run_device(
    godot: GodotBinary,
    project: GodotProject,
    cfg: EyesConfig,
    device: str,
    shots: list[Shot],
    rendering_method: str | None,
    rendering_driver: str | None,
    software: bool,
    log_path: Path,
    display: str = "auto",
) -> str:
    w, h = device_size(device)
    out_dir = cfg.output / device
    out_dir.mkdir(parents=True, exist_ok=True)
    for s in shots:
        for ext in (".png", ".json"):
            (out_dir / f"{s.name}{ext}").unlink(missing_ok=True)
    budget = cfg.boot_seconds + sum(s.wait + 5 + 2 * len(s.actions) for s in shots) * 3
    job = {
        "size": [w, h],
        "output_dir": str(out_dir.resolve()),
        "budget_seconds": budget,
        "shots": [s.job_dict(s.name) for s in shots],
    }
    job_path = out_dir / "job.json"
    job_path.write_text(json.dumps(job, indent=2), encoding="utf-8")

    cmd = [
        *_display_wrapper(w, h, display),
        godot.path,
        "--path",
        str(project.root),
        "--windowed",
        "--resolution",
        f"{w}x{h}",
        "--position",
        "0,0",
        "--audio-driver",
        "Dummy",
        "--disable-vsync",
    ]
    if rendering_method:
        cmd += ["--rendering-method", rendering_method]
    if rendering_driver:
        cmd += ["--rendering-driver", rendering_driver]
    cmd += ["-s", str(capture_script()), "--", f"--gsk-job={job_path.resolve()}"]

    env = dict(os.environ)
    if software:
        env.setdefault("LIBGL_ALWAYS_SOFTWARE", "1")
        env.setdefault("GALLIUM_DRIVER", "llvmpipe")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as fh:
        fh.write("$ " + " ".join(cmd) + "\n\n")
        fh.flush()
        proc = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT, env=env, start_new_session=os.name != "nt")
        try:
            proc.wait(timeout=budget + 60)
        except subprocess.TimeoutExpired:
            _kill_group(proc)
            fh.write(f"\n{MARK} killed after {budget + 60:.0f}s timeout\n")
    return log_path.read_text(encoding="utf-8", errors="replace")


def _kill_group(proc: subprocess.Popen[bytes]) -> None:
    if os.name == "nt":
        proc.kill()
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=10)
    except (ProcessLookupError, subprocess.TimeoutExpired, PermissionError):
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)


def split_log(text: str) -> tuple[list[str], dict[str, list[str]]]:
    """Error lines before the first shot (boot) and per shot."""
    boot: list[str] = []
    per: dict[str, list[str]] = {}
    current: str | None = None
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith(f"{MARK} BEGIN "):
            current = line.split(" BEGIN ", 1)[1].strip()
            per.setdefault(current, [])
            continue
        if line.startswith(f"{MARK} END "):
            current = None
            continue
        if ERROR_LINE.search(line) or (line.startswith(MARK) and "cannot" in line):
            entry = line.strip()
            if i + 1 < len(lines) and lines[i + 1].lstrip().startswith("at:"):
                entry += "  " + lines[i + 1].strip()
            if line.lstrip().startswith("at:"):
                continue
            (per.setdefault(current, []) if current else boot).append(entry)
    return boot, per


def analyse_shot(
    res: ShotResult,
    cfg: EyesConfig,
    shot: Shot,
    update: bool,
) -> None:
    """Fill stats, alerts, diff and crops for one captured shot."""
    assert res.image is not None
    out_dir = res.image.parent
    res.stats = cmp.frame_stats(res.image)
    st = res.stats
    facts = res.facts
    if st.mean_luma < 6 and st.std_luma < 3:
        res.alerts.append(Alert("error", "Frame is black. Is a camera current, and does the scene have lights or an environment?"))
    elif st.std_luma < 1.5:
        res.alerts.append(Alert("warn", "Frame is one flat colour; the scene may not have drawn anything."))
    if st.white_ratio > 0.5:
        res.alerts.append(Alert("warn", f"{st.white_ratio:.0%} of the frame is pure white (overexposed, or meshes without materials?)."))
    if facts.get("has_3d") and not facts.get("camera_3d"):
        res.alerts.append(Alert("warn", "Scene has 3D nodes but no current Camera3D."))
    if facts.get("no_material"):
        names = ", ".join(facts["no_material"][:5])
        res.alerts.append(Alert("warn", f"{len(facts['no_material'])} mesh(es) have no material and render plain white: {names}"))
    if facts.get("camera_3d") and facts.get("meshes", 0) > 0 and facts.get("meshes_on_screen", 0) == 0:
        res.alerts.append(Alert("warn", "No visible mesh is inside the camera view."))
    if facts.get("idle_skeletons"):
        names = ", ".join(facts["idle_skeletons"][:5])
        res.alerts.append(Alert("warn", f"Rigged model(s) with animations but nothing playing (bind/T-pose): {names}"))
    if facts.get("tiny"):
        res.alerts.append(
            Alert("info", f"{len(facts['tiny'])} mesh(es) cover less than 0.01% of the screen: {', '.join(facts['tiny'][:5])}")
        )
    if res.log_lines:
        res.alerts.append(Alert("warn", f"{len(res.log_lines)} engine error line(s) while capturing; first: {res.log_lines[0][:200]}"))

    for z in shot.zoom:
        res.zooms.append((z.name, cmp.crop(res.image, z.rect, out_dir / f"{res.shot}.zoom-{z.name}.png")))

    ref = cfg.references / res.device / f"{res.shot}.png"
    res.reference = ref
    if update:
        _ensure_gdignore(cfg.references)
        ref.parent.mkdir(parents=True, exist_ok=True)
        cmp.save_optimized(res.image, ref)
        res.status = "updated"
        return
    if not ref.is_file():
        res.status = "new"
        res.reference = None
        return
    diff_path = out_dir / f"{res.shot}.diff.png"
    res.diff = cmp.compare(ref, res.image, diff_path, cfg.tolerance)
    res.diff_image = diff_path if not res.diff.size_mismatch else None
    threshold = shot.threshold if shot.threshold is not None else cfg.threshold
    max_changed = shot.max_changed if shot.max_changed is not None else cfg.max_changed
    if res.diff.size_mismatch:
        res.status = "changed"
        res.alerts.append(Alert("error", "Screenshot size differs from the reference."))
    elif res.diff.similarity >= threshold and res.diff.changed_ratio <= max_changed:
        res.status = "pass"
    else:
        res.status = "changed"
        for i, r in enumerate(res.diff.regions[:3], 1):
            res.region_crops.append(cmp.side_by_side(ref, res.image, r, out_dir / f"{res.shot}.region{i}.png"))


def run(
    godot: GodotBinary,
    project: GodotProject,
    cfg: EyesConfig,
    update: bool = False,
    rendering_method: str | None = None,
    rendering_driver: str | None = None,
    software: bool = True,
    display: str = "auto",
) -> EyesRun:
    started = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    t0 = time.time()
    _ensure_gdignore(cfg.output)
    gi = cfg.output / ".gitignore"
    if not gi.exists():
        gi.write_text("*\n", encoding="utf-8")
    method = rendering_method or project.renderer_mobile
    results: list[ShotResult] = []
    for device in cfg.devices:
        shots = [s for s in cfg.shots if s.devices is None or device in s.devices]
        if not shots:
            continue
        log = run_device(
            godot, project, cfg, device, shots, method, rendering_driver, software, cfg.output / "logs" / f"{device}.log", display
        )
        boot_errors, per_shot = split_log(log)
        for s in shots:
            img = cfg.output / device / f"{s.name}.png"
            facts_path = cfg.output / device / f"{s.name}.json"
            res = ShotResult(s.name, device, s.scene, "error", log_lines=boot_errors + per_shot.get(s.name, []))
            if not img.is_file():
                tail = [ln for ln in log.splitlines() if ln.strip()][-6:]
                msg = "No screenshot was produced. Last log lines:\n" + "\n".join(tail)
                if "Unable to create DisplayServer" in log or "X11 Display is not available" in log:
                    msg = (
                        "Godot could not open a window. On Linux install xvfb and the X11 client libraries "
                        "(Debian/Ubuntu: xvfb libgl1-mesa-dri libx11-6 libxcursor1 libxinerama1 libxrandr2 libxi6 "
                        "libxkbcommon0), or pass --display xvfb if $DISPLAY points at a dead server.\n" + msg
                    )
                res.alerts.append(Alert("error", msg))
                results.append(res)
                continue
            res.image = img
            if facts_path.is_file():
                try:
                    res.facts = json.loads(facts_path.read_text(encoding="utf-8"))
                except ValueError:
                    res.facts = {}
            analyse_shot(res, cfg, s, update)
            results.append(res)
    return EyesRun(project, cfg, godot, results, method, started, time.time() - t0, update)
