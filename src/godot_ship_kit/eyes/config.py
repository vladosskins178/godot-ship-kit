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
    tolerance: int = 8
    region_pixels: int = 20
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


def _num(value: Any, where: str, lo: float, hi: float, integer: bool = False) -> float:
    """A finite number within [lo, hi] from gsk.toml, or a clear error."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or not lo <= value <= hi:
        raise EyesConfigError(f"{where} must be a number between {lo:g} and {hi:g}, got {value!r}.")
    if integer and not float(value).is_integer():
        raise EyesConfigError(f"{where} must be a whole number, got {value!r}.")
    return float(value)


def _str_list(value: Any, where: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise EyesConfigError(f"{where} must be a list of strings, got {value!r}.")
    return list(value)


def _opt(raw: dict[str, Any], key: str, where: str, lo: float, hi: float, integer: bool = False) -> float | None:
    return None if key not in raw else _num(raw[key], f"{where} {key}", lo, hi, integer)


ACTION_KEYS = {"wait", "tap", "press", "call", "set"}


def _actions(value: Any, where: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise EyesConfigError(f"{where} actions must be a list of tables, got {value!r}.")
    out = []
    for a in value:
        if not isinstance(a, dict) or not ACTION_KEYS & set(a):
            raise EyesConfigError(f"{where}: each action needs one of wait, tap, press, call, set; got {a!r}.")
        if "wait" in a:
            _num(a["wait"], f"{where} wait", 0, 600)
        if "then_wait" in a:
            _num(a["then_wait"], f"{where} then_wait", 0, 600)
        if "tap" in a:
            tap = a["tap"]
            if not isinstance(tap, list) or len(tap) != 2:
                raise EyesConfigError(f"{where} tap needs [x, y] as fractions of the screen, got {tap!r}.")
            for v in tap:
                _num(v, f"{where} tap", 0, 1)
        for key in ("press", "call", "set", "node"):
            if key in a and not isinstance(a[key], str):
                raise EyesConfigError(f"{where} {key} must be a string, got {a[key]!r}.")
        if "args" in a and not isinstance(a["args"], list):
            raise EyesConfigError(f"{where} args must be a list, got {a['args']!r}.")
        out.append(a)
    return out


def _zooms(value: Any, where: str) -> list[Zoom]:
    if not isinstance(value, list):
        raise EyesConfigError(f"{where} zoom must be a list of tables, got {value!r}.")
    out = []
    for i, z in enumerate(value):
        rect = z.get("rect") if isinstance(z, dict) else None
        if not isinstance(rect, list) or len(rect) != 4:
            raise EyesConfigError(f"{where} zoom needs rect = [x, y, w, h] as fractions, got {z!r}.")
        x, y, w, h = (_num(v, f"{where} zoom rect", 0, 1) for v in rect)
        out.append(Zoom(_slug(str(z.get("name", f"zoom{i + 1}"))), (x, y, w, h)))
    return out


def load_config(
    project: GodotProject,
    config_path: Path | None = None,
    scenes: list[str] | None = None,
    devices: list[str] | None = None,
) -> EyesConfig:
    path = config_path or project.root / "gsk.toml"
    data: Any = {}
    if path.is_file():
        try:
            with path.open("rb") as fh:
                data = tomllib.load(fh).get("eyes", {})
        except tomllib.TOMLDecodeError as exc:
            raise EyesConfigError(f"{path.name}: {exc}") from None
    elif config_path:
        raise EyesConfigError(f"Config file not found: {config_path}")
    if not isinstance(data, dict):
        raise EyesConfigError("[eyes] in gsk.toml must be a table.")

    default_wait = _num(data.get("wait", 1.0), "[eyes] wait", 0, 600)
    shots: list[Shot] = []
    if scenes:
        for s in scenes:
            res = s if s.startswith(("res://", "uid://")) else _scene_res(project, s)
            shots.append(Shot(name=_slug(Path(res.removeprefix("res://")).stem), scene=res, wait=default_wait))
    else:
        raw_shots = data.get("shot", [])
        if not isinstance(raw_shots, list):
            raise EyesConfigError("Shots must be an array of tables: write [[eyes.shot]], not [eyes.shot].")
        for n, raw in enumerate(raw_shots, 1):
            where = f"[[eyes.shot]] #{n}"
            if not isinstance(raw, dict) or not isinstance(raw.get("scene"), str):
                raise EyesConfigError(f'{where} needs scene = "res://...".')
            shot_devices = raw.get("devices")
            shots.append(
                Shot(
                    name=_slug(str(raw.get("name") or Path(raw["scene"].removeprefix("res://")).stem)),
                    scene=raw["scene"],
                    wait=_num(raw.get("wait", default_wait), f"{where} wait", 0, 600),
                    actions=_actions(raw.get("actions", []), where),
                    zoom=_zooms(raw.get("zoom", []), where),
                    devices=None if shot_devices is None else _str_list(shot_devices, f"{where} devices"),
                    threshold=_opt(raw, "threshold", where, 0, 1),
                    max_changed=_opt(raw, "max_changed", where, 0, 1),
                    region_pixels=None
                    if "region_pixels" not in raw
                    else int(_num(raw["region_pixels"], f"{where} region_pixels", 0, 1e9, True)),
                )
            )
    if not shots:
        main = project.main_scene_res
        if main:
            shots.append(Shot(name="main", scene=main, wait=_num(data.get("wait", 2.0), "[eyes] wait", 0, 600)))
        else:
            raise EyesConfigError("Nothing to capture: no scenes given, no [[eyes.shot]] in gsk.toml and no main scene.")
    for shot in shots:
        if shot.scene.startswith("res://") and not project.res_to_path(shot.scene).is_file():
            raise EyesConfigError(f"Scene {shot.scene} not found (shot '{shot.name}').")
    names = [s.name for s in shots]
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        raise EyesConfigError(f"Shot names must be unique: {', '.join(sorted(dupes))}")

    devs = devices or (_str_list(data["devices"], "[eyes] devices") if "devices" in data else default_devices(project))
    for d in devs:
        device_size(d)
    return EyesConfig(
        shots=shots,
        devices=list(devs),
        references=_inside(project, str(data.get("references", "tests/eyes")), "references"),
        output=_inside(project, str(data.get("output", ".gsk/eyes")), "output"),
        threshold=_num(data.get("threshold", 0.99), "[eyes] threshold", 0, 1),
        max_changed=_num(data.get("max_changed", 0.0005), "[eyes] max_changed", 0, 1),
        tolerance=int(_num(data.get("tolerance", 8), "[eyes] tolerance", 0, 255, True)),
        region_pixels=int(_num(data.get("region_pixels", 20), "[eyes] region_pixels", 0, 1e9, True)),
        boot_seconds=_num(data.get("boot_seconds", 120), "[eyes] boot_seconds", 1, 3600),
    )
