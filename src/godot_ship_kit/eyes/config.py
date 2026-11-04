"""Shot list and device presets for ``gsk eyes``, read from ``gsk.toml``."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..project import GodotProject

# Common phone and tablet render sizes (portrait unless named otherwise).
DEVICES: dict[str, tuple[int, int]] = {
    "phone": (1080, 2400),  # 20:9, the most common modern Android shape
    "phone-landscape": (2400, 1080),
    "phone-small": (720, 1600),  # budget 20:9 phones
    "phone-small-landscape": (1600, 720),
    "phone-16x9": (1080, 1920),
    "phone-16x9-landscape": (1920, 1080),
    "tablet": (1600, 2560),
    "tablet-landscape": (2560, 1600),
}


class EyesConfigError(Exception):
    pass


@dataclass
class Zoom:
    name: str
    rect: tuple[float, float, float, float]  # x, y, w, h as fractions of the frame


@dataclass
class Shot:
    name: str
    scene: str
    wait: float = 1.0
    actions: list[dict[str, Any]] = field(default_factory=list)
    zoom: list[Zoom] = field(default_factory=list)
    devices: list[str] | None = None
    threshold: float | None = None
    max_changed: float | None = None
    region_pixels: int | None = None


@dataclass
class EyesConfig:
    shots: list[Shot]
    devices: list[str]
    references: Path
    output: Path
    threshold: float = 0.99
    max_changed: float = 0.0005
    tolerance: int = 24
    region_pixels: int = 40
    boot_seconds: float = 120.0


def device_size(name: str) -> tuple[int, int]:
    if name in DEVICES:
        return DEVICES[name]
    m = re.fullmatch(r"(\d{2,5})x(\d{2,5})", name)
    if m:
        return int(m.group(1)), int(m.group(2))
    raise EyesConfigError(f"Unknown device '{name}'. Use one of {', '.join(DEVICES)} or WIDTHxHEIGHT.")


def default_devices(project: GodotProject) -> list[str]:
    orient = project.setting("display/window/handheld/orientation", 0)
    portrait = orient in (1, 3, 5)
    if orient == 6:  # sensor: decide from the base viewport
        w = project.setting("display/window/size/viewport_width", 1152)
        h = project.setting("display/window/size/viewport_height", 648)
        portrait = isinstance(w, int) and isinstance(h, int) and h > w
    return ["phone"] if portrait else ["phone-landscape"]


def _scene_res(project: GodotProject, given: str) -> str:
    """A scene given as a file path: relative to the current folder, else to the project."""
    for cand in (Path(given), project.root / given):
        if cand.is_file():
            try:
                return project.path_to_res(cand)
            except ValueError:
                raise EyesConfigError(f"Scene {given} is outside the project.") from None
    raise EyesConfigError(f"Scene {given} not found.")


def _inside(project: GodotProject, rel: str, key: str) -> Path:
    """Resolve a folder from gsk.toml; it must stay inside the project (use --output to go elsewhere)."""
    path = (project.root / rel).resolve()
    if not path.is_relative_to(project.root.resolve()):
        raise EyesConfigError(f"[eyes] {key} = {rel!r} points outside the project.")
    return path


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("_") or "shot"


def load_config(
    project: GodotProject,
    config_path: Path | None = None,
    scenes: list[str] | None = None,
    devices: list[str] | None = None,
) -> EyesConfig:
    path = config_path or project.root / "gsk.toml"
    data: dict[str, Any] = {}
    if path.is_file():
        with path.open("rb") as fh:
            data = tomllib.load(fh).get("eyes", {})
    elif config_path:
        raise EyesConfigError(f"Config file not found: {config_path}")

    shots: list[Shot] = []
    if scenes:
        for s in scenes:
            res = s if s.startswith(("res://", "uid://")) else _scene_res(project, s)
            shots.append(Shot(name=_slug(Path(res.removeprefix("res://")).stem), scene=res, wait=float(data.get("wait", 1.0))))
    else:
        for raw in data.get("shot", []):
            if "scene" not in raw:
                raise EyesConfigError(f"[[eyes.shot]] entry without 'scene': {raw}")
            zooms = [
                Zoom(_slug(z.get("name", f"zoom{i + 1}")), tuple(float(v) for v in z["rect"]))  # type: ignore[arg-type]
                for i, z in enumerate(raw.get("zoom", []))
            ]
            shots.append(
                Shot(
                    name=_slug(raw.get("name") or Path(str(raw["scene"]).removeprefix("res://")).stem),
                    scene=str(raw["scene"]),
                    wait=float(raw.get("wait", data.get("wait", 1.0))),
                    actions=list(raw.get("actions", [])),
                    zoom=zooms,
                    devices=raw.get("devices"),
                    threshold=raw.get("threshold"),
                    max_changed=raw.get("max_changed"),
                    region_pixels=raw.get("region_pixels"),
                )
            )
    if not shots:
        if project.main_scene:
            shots.append(Shot(name="main", scene=project.main_scene, wait=float(data.get("wait", 2.0))))
        else:
            raise EyesConfigError("Nothing to capture: no scenes given, no [[eyes.shot]] in gsk.toml and no main scene.")
    for shot in shots:
        if shot.scene.startswith("res://") and not project.res_to_path(shot.scene).is_file():
            raise EyesConfigError(f"Scene {shot.scene} not found (shot '{shot.name}').")
    names = [s.name for s in shots]
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        raise EyesConfigError(f"Shot names must be unique: {', '.join(sorted(dupes))}")

    devs = devices or data.get("devices") or default_devices(project)
    for d in devs:
        device_size(d)
    return EyesConfig(
        shots=shots,
        devices=list(devs),
        references=_inside(project, str(data.get("references", "tests/eyes")), "references"),
        output=_inside(project, str(data.get("output", ".gsk/eyes")), "output"),
        threshold=float(data.get("threshold", 0.99)),
        max_changed=float(data.get("max_changed", 0.0005)),
        tolerance=int(data.get("tolerance", 24)),
        region_pixels=int(data.get("region_pixels", 40)),
        boot_seconds=float(data.get("boot_seconds", 120)),
    )
