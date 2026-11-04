"""A read-only view of a Godot 4 project on disk."""

from __future__ import annotations

import os
import re
import struct
from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

from . import godot_cfg
from .godot_cfg import ConfigFile, as_list

SKIP_DIRS = {".godot", ".git", ".import", ".gsk", "android", "__pycache__", "node_modules"}
TEXT_RESOURCE_EXT = {".tscn", ".tres", ".gd", ".gdshader", ".cfg", ".godot", ".json", ".cs", ".gdshaderinc"}
SCENE_SOURCE_EXT = {".glb", ".gltf", ".fbx", ".blend", ".obj", ".dae"}


class ProjectError(Exception):
    pass


def find_project(start: str | Path = ".") -> Path:
    """Return the directory holding ``project.godot`` at or above ``start``."""
    path = Path(start).resolve()
    if path.is_file():
        path = path.parent
    for candidate in [path, *path.parents]:
        if (candidate / "project.godot").is_file():
            return candidate
    raise ProjectError(f"No project.godot found in {path} or its parents. Pass the project folder, e.g. `gsk doctor path/to/game`.")


@dataclass
class ImportEntry:
    """One ``*.import`` sidecar and what it produced in ``.godot/imported``."""

    import_path: Path
    source_path: Path
    res_path: str
    importer: str
    resource_type: str
    params: dict[str, Any]
    dest_files: list[str]
    remap: dict[str, Any]
    shipped_bytes: int = 0
    all_imported_bytes: int = 0
    source_bytes: int = 0

    @property
    def is_texture(self) -> bool:
        return self.importer in ("texture", "texture_2d") or self.resource_type == "CompressedTexture2D"

    @property
    def is_audio(self) -> bool:
        return self.importer in ("wav", "oggvorbisstr", "mp3", "ogg_vorbis")

    @property
    def is_scene(self) -> bool:
        return self.importer == "scene"

    @property
    def compress_mode(self) -> int:
        try:
            return int(self.params.get("compress/mode", 0))
        except (TypeError, ValueError):
            return 0


@dataclass
class Preset:
    index: int
    name: str
    platform: str
    values: dict[str, Any]
    options: dict[str, Any]

    def opt(self, key: str, default: Any = None) -> Any:
        return self.options.get(key, default)

    @property
    def is_android(self) -> bool:
        return self.platform == "Android"


@dataclass
class GodotProject:
    root: Path
    _imports: list[ImportEntry] | None = field(default=None, repr=False)

    @classmethod
    def open(cls, path: str | Path) -> GodotProject:
        return cls(find_project(path))

    # ------------------------------------------------------------------ config
    @cached_property
    def config(self) -> ConfigFile:
        return godot_cfg.load(self.root / "project.godot")

    def setting(self, dotted: str, default: Any = None) -> Any:
        """Read ``section/key`` style settings, e.g. ``rendering/renderer/rendering_method``."""
        section, _, key = dotted.partition("/")
        return self.config.get(section, key, default)

    @property
    def name(self) -> str:
        return str(self.setting("application/config/name", self.root.name))

    @property
    def features(self) -> list[str]:
        return [str(x) for x in as_list(self.setting("application/config/features"))]

    @property
    def godot_version(self) -> str | None:
        """Engine version the project was last saved with, e.g. ``4.6``."""
        for feat in self.features:
            if re.fullmatch(r"\d+\.\d+", feat):
                return feat
        return None

    @property
    def main_scene(self) -> str | None:
        val = self.setting("application/run/main_scene")
        return str(val) if val else None

    @property
    def renderer_desktop(self) -> str:
        return str(self.setting("rendering/renderer/rendering_method", "forward_plus"))

    @property
    def renderer_mobile(self) -> str:
        explicit = self.setting("rendering/renderer/rendering_method.mobile")
        if explicit:
            return str(explicit)
        return "gl_compatibility" if self.renderer_desktop == "gl_compatibility" else "mobile"

    # ----------------------------------------------------------------- presets
    @cached_property
    def presets(self) -> list[Preset]:
        path = self.root / "export_presets.cfg"
        if not path.is_file():
            return []
        cfg = godot_cfg.load(path)
        out: list[Preset] = []
        for sec, values in cfg.sections.items():
            m = re.fullmatch(r"preset\.(\d+)", sec)
            if not m:
                continue
            idx = int(m.group(1))
            out.append(
                Preset(
                    index=idx,
                    name=str(values.get("name", f"preset {idx}")),
                    platform=str(values.get("platform", "")),
                    values=values,
                    options=cfg.section(f"preset.{idx}.options"),
                )
            )
        return sorted(out, key=lambda p: p.index)

    def android_presets(self) -> list[Preset]:
        return [p for p in self.presets if p.is_android]

    def find_preset(self, name: str | None) -> Preset | None:
        if name:
            for p in self.presets:
                if p.name == name:
                    return p
            return None
        android = self.android_presets()
        return android[0] if android else None

    @cached_property
    def credentials(self) -> ConfigFile:
        path = self.root / ".godot" / "export_credentials.cfg"
        return godot_cfg.load(path) if path.is_file() else ConfigFile()

    # ------------------------------------------------------------------- files
    def res_to_path(self, res: str) -> Path:
        return self.root / res.removeprefix("res://")

    def path_to_res(self, path: Path) -> str:
        return "res://" + path.resolve().relative_to(self.root.resolve()).as_posix()

    def walk(self) -> Iterator[Path]:
        """Every project file, skipping caches, VCS and ``.gdignore`` folders."""
        for dirpath, dirnames, filenames in os.walk(self.root):
            if ".gdignore" in filenames:
                dirnames[:] = []
                continue
            base = Path(dirpath)
            at_root = base == self.root
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and (d not in SKIP_DIRS or (d == "android" and not at_root))]
            for fn in filenames:
                yield base / fn

    @property
    def imports(self) -> list[ImportEntry]:
        if self._imports is None:
            self._imports = [e for e in (self._read_import(p) for p in self.walk() if p.suffix == ".import") if e]
        return self._imports

    def reload_imports(self) -> None:
        self._imports = None

    def _read_import(self, path: Path) -> ImportEntry | None:
        try:
            cfg = godot_cfg.load(path)
        except OSError:
            return None
        remap = cfg.section("remap")
        importer = str(remap.get("importer", ""))
        if importer in ("", "keep", "skip"):
            return None
        source = path.with_suffix("")
        dest = [str(x) for x in as_list(cfg.get("deps", "dest_files"))]
        entry = ImportEntry(
            import_path=path,
            source_path=source,
            res_path=self.path_to_res(source) if source.exists() else str(cfg.get("deps", "source_file", "")),
            importer=importer,
            resource_type=str(remap.get("type", "")),
            params=cfg.section("params"),
            dest_files=dest,
            remap=remap,
        )
        entry.source_bytes = _size(source)
        sizes = {d: _size(self.res_to_path(d)) for d in dest}
        entry.all_imported_bytes = sum(sizes.values())
        entry.shipped_bytes = _android_shipped(dest, sizes)
        return entry

    # ------------------------------------------------------------ references
    @cached_property
    def reference_index(self) -> ReferenceIndex:
        return ReferenceIndex.build(self)


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _android_shipped(dest: list[str], sizes: dict[str, int]) -> int:
    """Bytes that end up in an Android export.

    VRAM-compressed textures are imported once per GPU format (``.s3tc.ctex``
    for desktop, ``.etc2.ctex`` / ``.astc.ctex`` for mobile). Only the mobile
    variant ships in an APK.
    """
    mobile = [d for d in dest if ".etc2." in d or ".astc." in d]
    if mobile:
        return sum(sizes[d] for d in mobile)
    desktop_only = [d for d in dest if ".s3tc." in d or ".bptc." in d]
    return sum(v for d, v in sizes.items() if d not in desktop_only)


_RES_LITERAL = re.compile(r"""res://[^"'\s)\]}>,;]*""")
_UID_LITERAL = re.compile(r"uid://[a-z0-9]+")


@dataclass
class ReferenceIndex:
    """Which ``res://`` paths are mentioned anywhere in text resources."""

    literals: set[str]
    prefixes: set[str]
    uids: set[str]
    basenames_in_code: set[str]
    texture_3d_hint: set[str]

    @classmethod
    def build(cls, project: GodotProject) -> ReferenceIndex:
        literals: set[str] = set()
        uids: set[str] = set()
        names: set[str] = set()
        tex3d: set[str] = set()
        for path in project.walk():
            if path.suffix not in TEXT_RESOURCE_EXT or path.name == "export_presets.cfg":
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            found = set(_RES_LITERAL.findall(text))
            literals |= found
            if path.suffix in (".gd", ".cs", ".json"):
                names |= set(re.findall(r"[\w\-]+\.\w{2,5}", text))
            if path.suffix in (".tscn", ".tres"):
                uids |= set(_UID_LITERAL.findall(text))
                if re.search(r'type="(StandardMaterial3D|ORMMaterial3D|ShaderMaterial)"', text):
                    tex3d |= {r for r in found if r.lower().endswith(_IMAGE_EXT)}
        prefixes: set[str] = set()
        for lit in literals:
            cut = re.split(r"[%{$]", lit, maxsplit=1)[0]
            if cut != lit and len(cut) > len("res://") + 1:
                prefixes.add(cut)  # "res://music/%s.ogg" -> anything under res://music/
        return cls(literals, prefixes, uids, names, tex3d)

    def is_referenced(self, res_path: str, uid: str | None = None) -> bool:
        if res_path in self.literals or (uid and uid in self.uids):
            return True
        if Path(res_path).name in self.basenames_in_code:
            return True
        # Paths assembled at runtime: "res://voices/" + id + ".ogg" or "res://music/%s.ogg" % track.
        if any(res_path.startswith(p) for p in self.prefixes):
            return True
        return any(len(lit) > 8 and res_path.startswith(lit) and lit != res_path for lit in self.literals)


_IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".tga", ".bmp", ".exr", ".hdr", ".svg", ".ktx", ".dds")


# ----------------------------------------------------------------------------
# Image header sniffing (stdlib only)
# ----------------------------------------------------------------------------


def image_size(path: Path) -> tuple[int, int] | None:
    """Width and height from PNG / JPEG / WebP headers without decoding pixels."""
    try:
        with path.open("rb") as fh:
            head = fh.read(32)
            if head.startswith(b"\x89PNG\r\n\x1a\n"):
                w, h = struct.unpack(">II", head[16:24])
                return int(w), int(h)
            if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
                return _webp_size(head)
            if head[:2] == b"\xff\xd8":
                fh.seek(2)
                return _jpeg_size(fh)
    except (OSError, struct.error):
        return None
    return None


def _webp_size(head: bytes) -> tuple[int, int] | None:
    chunk = head[12:16]
    if chunk == b"VP8X":
        w = int.from_bytes(head[24:27], "little") + 1
        h = int.from_bytes(head[27:30], "little") + 1
        return w, h
    if chunk == b"VP8L":
        bits = int.from_bytes(head[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if chunk == b"VP8 ":
        w, h = struct.unpack("<HH", head[26:30])
        return w & 0x3FFF, h & 0x3FFF
    return None


def _jpeg_size(fh: Any) -> tuple[int, int] | None:
    while True:
        byte = fh.read(1)
        while byte and byte != b"\xff":
            byte = fh.read(1)
        while byte == b"\xff":
            byte = fh.read(1)
        if not byte:
            return None
        marker = byte[0]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            continue
        seg_len = struct.unpack(">H", fh.read(2))[0]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            data = fh.read(5)
            h, w = struct.unpack(">HH", data[1:5])
            return int(w), int(h)
        fh.seek(seg_len - 2, 1)
