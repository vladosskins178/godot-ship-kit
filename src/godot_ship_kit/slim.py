"""``gsk slim``: find what makes the export big and fix the import settings.

What ships in an Android build is not your PNG or GLB but Godot's imported
copy in ``.godot/imported``. A 600 KB JPEG imported as *Lossless* at 4K can
cost 20 MB in the APK. The fix almost always lives in the ``.import`` sidecar:

* ``compress/mode=2`` (VRAM compressed: ETC2/ASTC on phones, GPU native)
* ``process/size_limit`` to cap textures that phones never show at full size
* ``compress/normal_map=1`` for normal maps once they are VRAM compressed

Changes are written with a backup so ``gsk slim --undo`` restores them.
"""

from __future__ import annotations

import json
import re
import shutil
import struct
import subprocess
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import godot_cfg
from .project import SCENE_SOURCE_EXT, GodotProject, ImportEntry, image_size

SAFE = "safe"
REVIEW = "review"

RULES: dict[str, tuple[str, str]] = {
    "vram-3d": (
        "VRAM-compress 3D textures",
        "Godot's own default for 3D. ETC2/ASTC: 4-8x less GPU memory, no decode on load. May be larger on disk than Lossless.",
    ),
    "cap-3d": (
        "Cap 3D texture size",
        "Phones rarely show more than ~1024 px of a model texture. Check close-ups after.",
    ),
    "lossy-2d": (
        "Store big 2D art as Lossy (WebP)",
        "Usually indistinguishable for painted art and photos. Keep Lossless for pixel art and crisp UI.",
    ),
    "cap-2d": (
        "Cap 2D texture size",
        "Larger than any phone screen shows. Skip textures that are zoomed or scrolled.",
    ),
    "wav-qoa": (
        "Compress WAV with QOA",
        "About 5x smaller and cheap to decode (Godot 4.3+). Music is better as .ogg.",
    ),
}

MATERIAL_SUFFIX = re.compile(
    r"(albedo|diffuse|basecolor|base_color|_color|normal|_nrm|rough|metal|_orm|_ao\b|occlusion|emission|emissive|height|_disp|_col\b)",
    re.I,
)
NORMAL_TOKENS = {"normal", "normals", "normalgl", "normaldx", "normalmap", "nrm", "nor"}


def is_normal_map_name(path: Path) -> bool:
    """``rock_normal.png``, ``wall-nrm.png``, ``rock_NormalGL_<uuid>.jpg``, ``brick_n.png``.

    Token based, so ``Leaves_NormalTree.png`` or ``button_normal_state`` style names in UI
    folders are not mistaken for normal maps.
    """
    tokens = [t for t in re.split(r"[_\-.\s]+", path.stem.lower()) if t]
    return any(t in NORMAL_TOKENS for t in tokens) or (len(tokens) > 1 and tokens[-1] == "n")


UI_HINT = re.compile(r"(^|[/_\-])(ui|hud|gui|icons?|fonts?|buttons?|menu|frames?|portraits?)([/_\-.]|$)", re.I)


@dataclass
class Proposal:
    rule: str
    risk: str
    entry: ImportEntry
    changes: dict[str, Any]
    reason: str
    current_bytes: int
    estimated_bytes: int | None = None
    gpu_before: int | None = None  # texture memory on the phone's GPU, estimated
    gpu_after: int | None = None

    @property
    def saving(self) -> int:
        """Bytes saved in the export (never negative; see ``disk_delta``)."""
        if self.estimated_bytes is None:
            return 0
        return max(0, self.current_bytes - self.estimated_bytes)

    @property
    def disk_delta(self) -> int:
        return 0 if self.estimated_bytes is None else self.estimated_bytes - self.current_bytes

    @property
    def gpu_saving(self) -> int:
        if self.gpu_before is None or self.gpu_after is None:
            return 0
        return max(0, self.gpu_before - self.gpu_after)

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.entry.res_path,
            "import_file": str(self.entry.import_path),
            "rule": self.rule,
            "risk": self.risk,
            "changes": self.changes,
            "reason": self.reason,
            "current_bytes": self.current_bytes,
            "estimated_bytes": self.estimated_bytes,
            "estimated_saving": self.saving,
            "gpu_bytes_before": self.gpu_before,
            "gpu_bytes_after": self.gpu_after,
        }


@dataclass
class SlimReport:
    total_shipped: int
    by_kind: dict[str, int]
    biggest: list[ImportEntry]
    proposals: list[Proposal]
    unreferenced: list[ImportEntry]
    heavy_models: list[tuple[ImportEntry, int]] = field(default_factory=list)
    has_cache: bool = True

    def estimate(self, aggressive: bool) -> tuple[int, int]:
        """(change in export bytes, GPU bytes saved) if the chosen rules are applied together.

        Rules on the same file compound: each one scales the size by
        ``estimated / current`` relative to the state before it.
        """
        by_file: dict[Path, list[Proposal]] = {}
        for p in self.proposals:
            if p.risk == SAFE or aggressive:
                by_file.setdefault(p.entry.import_path, []).append(p)
        disk = gpu = 0
        for props in by_file.values():
            current = props[0].entry.shipped_bytes
            factor = 1.0
            for p in props:
                if p.estimated_bytes is not None and p.current_bytes:
                    factor *= p.estimated_bytes / p.current_bytes
            disk += int(current * factor) - current
            gpu_props = [p for p in props if p.gpu_before]
            if gpu_props:
                start = max(p.gpu_before or 0 for p in gpu_props)
                end = min(p.gpu_after or 0 for p in gpu_props)
                gpu += max(0, start - end)
        return disk, gpu

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_shipped_bytes": self.total_shipped,
            "by_kind": self.by_kind,
            "import_cache_present": self.has_cache,
            "biggest": [{"file": e.res_path, "shipped_bytes": e.shipped_bytes, "importer": e.importer} for e in self.biggest],
            "proposals": [p.to_dict() for p in self.proposals],
            "estimated_change_bytes": {"safe": self.estimate(False)[0], "aggressive": self.estimate(True)[0]},
            "estimated_gpu_saving_bytes": {"safe": self.estimate(False)[1], "aggressive": self.estimate(True)[1]},
            "possibly_unreferenced": [{"file": e.res_path, "shipped_bytes": e.shipped_bytes} for e in self.unreferenced],
            "heavy_models": [{"file": e.res_path, "triangles": t} for e, t in self.heavy_models],
        }


def kind_of(entry: ImportEntry) -> str:
    if entry.is_texture:
        return "textures"
    if entry.is_audio:
        return "audio"
    if entry.is_scene:
        return "3d models"
    if entry.importer.startswith("font"):
        return "fonts"
    return "other"


def texture_3d_evidence(project: GodotProject, entry: ImportEntry) -> str | None:
    """Why we think a texture is sampled by 3D materials, or None.

    ``"model"`` and ``"material"`` are hard evidence; ``"name"`` is only a naming
    convention (``*_albedo``, ``*_normal``, ...) and never drives a safe change.
    """
    if UI_HINT.search(entry.res_path):
        return None
    if _is_glb_sidecar(entry):
        return "model"  # textures extracted from a GLB/FBX sit next to it with its name as prefix
    if entry.res_path in project.reference_index.texture_3d_hint:
        return "material"
    if MATERIAL_SUFFIX.search(entry.source_path.name):
        return "name"
    return None


def is_3d_texture(project: GodotProject, entry: ImportEntry) -> bool:
    return texture_3d_evidence(project, entry) is not None


def _vram_bytes(w: int, h: int, wide: bool, mipmaps: bool) -> int:
    """ETC2 size: 4 bpp for RGB, 8 bpp for RGBA and two-channel normal maps."""
    bpp = 8 if wide else 4
    base = w * h * bpp // 8
    return int(base * 4 / 3) if mipmaps else base


def _rgba_bytes(w: int, h: int, mipmaps: bool) -> int:
    """Lossless and Lossy textures are decoded to uncompressed RGBA8 on the GPU."""
    base = w * h * 4
    return int(base * 4 / 3) if mipmaps else base


def _capped(dims: tuple[int, int], limit: int) -> tuple[int, int]:
    w, h = dims
    if limit and max(w, h) > limit:
        scale = limit / max(w, h)
        return max(1, int(w * scale)), max(1, int(h * scale))
    return w, h


def _has_alpha(path: Path) -> bool:
    if path.suffix.lower() in (".jpg", ".jpeg"):
        return False
    try:
        with path.open("rb") as fh:
            head = fh.read(64 * 1024)
        if head.startswith(b"\x89PNG"):
            color_type = head[25]
            # RGBA / grey+alpha, or a palette / grey / RGB image with a tRNS chunk.
            return color_type in (4, 6) or b"tRNS" in head
    except (OSError, IndexError):
        pass
    return True


def analyze(
    project: GodotProject,
    max_3d: int = 1024,
    max_2d: int = 2048,
    top: int = 15,
) -> SlimReport:
    entries = project.imports
    has_cache = (project.root / ".godot" / "imported").is_dir()
    by_kind: dict[str, int] = {}
    for e in entries:
        by_kind[kind_of(e)] = by_kind.get(kind_of(e), 0) + e.shipped_bytes
    biggest = sorted(entries, key=lambda e: -e.shipped_bytes)[:top]

    proposals: list[Proposal] = []
    for e in entries:
        if e.is_texture:
            proposals += _texture_rules(project, e, max_3d, max_2d)
        elif e.importer == "wav" and _version_at_least(project.godot_version, (4, 3)):
            proposals += _wav_rules(e)  # QOA compression arrived in Godot 4.3
    proposals.sort(key=lambda p: (p.risk != SAFE, -p.saving))

    idx = project.reference_index
    main = project.main_scene_res or ""
    unreferenced = sorted(
        (
            e
            for e in entries
            if e.shipped_bytes > 200_000
            and e.res_path != main
            and not idx.is_referenced(e.res_path, str(e.remap.get("uid", "")) or None)
            and not _is_glb_sidecar(e)
        ),
        key=lambda e: -e.shipped_bytes,
    )[:top]

    heavy_models: list[tuple[ImportEntry, int]] = []
    for e in entries:
        if e.is_scene and e.source_path.suffix.lower() == ".glb":
            tris = glb_triangles(e.source_path)
            if tris and tris > 50_000:
                heavy_models.append((e, tris))
    heavy_models.sort(key=lambda t: -t[1])
    return SlimReport(sum(by_kind.values()), by_kind, biggest, proposals, unreferenced, heavy_models[:top], has_cache)


def _version_at_least(version: str | None, minimum: tuple[int, int]) -> bool:
    if not version:
        return True
    try:
        major, minor = (int(x) for x in version.split(".")[:2])
    except ValueError:
        return True
    return (major, minor) >= minimum


def _is_glb_sidecar(e: ImportEntry) -> bool:
    """``tree_Image_0.png`` or ``tree_normal.png`` next to ``tree.glb`` (Godot extracts them like that)."""
    stem = e.source_path.stem.lower()
    return any(stem == s or (stem.startswith(s) and stem[len(s)] in "_-. ") for s in _scene_stems(e.source_path.parent))


@lru_cache(maxsize=4096)
def _scene_stems(folder: Path) -> tuple[str, ...]:
    try:
        return tuple(p.stem.lower() for p in folder.iterdir() if p.suffix.lower() in SCENE_SOURCE_EXT)
    except OSError:
        return ()


def _texture_rules(project: GodotProject, e: ImportEntry, max_3d: int, max_2d: int) -> list[Proposal]:
    out: list[Proposal] = []
    dims = image_size(e.source_path)
    alpha = _has_alpha(e.source_path)
    limit = int(e.params.get("process/size_limit", 0) or 0)
    mip = bool(e.params.get("mipmaps/generate", False))
    evidence = texture_3d_evidence(project, e)
    three_d = evidence is not None
    mode = e.compress_mode
    cur = _capped(dims, limit) if dims else None

    if three_d:
        is_normal = is_normal_map_name(e.source_path)
        # Normal-map compression stores two channels at 8 bits per pixel (EAC RG11).
        wide = alpha or is_normal
        changes: dict[str, Any] = {}
        reasons = []
        if mode in (0, 1):
            changes["compress/mode"] = 2
            changes["mipmaps/generate"] = True
            reasons.append("3D texture stored Lossless/Lossy")
        if is_normal and int(e.params.get("compress/normal_map", 0) or 0) == 0:
            changes["compress/normal_map"] = 1
            reasons.append("normal map without normal compression")
        if changes:
            if evidence == "name":
                reasons.append("3D use guessed from the file name only")
            risk = SAFE if evidence in ("model", "material") else REVIEW
            prop = Proposal("vram-3d", risk, e, changes, "; ".join(reasons), e.shipped_bytes)
            if cur and mode in (0, 1):
                prop.estimated_bytes = _vram_bytes(*cur, wide, True)
                prop.gpu_before = _rgba_bytes(*cur, mip)
                prop.gpu_after = prop.estimated_bytes
            out.append(prop)
        if dims and max(dims) > max_3d and (not limit or limit > max_3d):
            w, h = _capped(dims, max_3d)
            assert cur is not None
            before = _vram_bytes(*cur, wide, True)
            out.append(
                Proposal(
                    "cap-3d",
                    REVIEW,
                    e,
                    {"process/size_limit": max_3d},
                    f"{dims[0]}x{dims[1]} source" + (", sizes assume VRAM compression" if mode in (0, 1) else ""),
                    before if mode in (0, 1) or "compress/mode" in changes else e.shipped_bytes,
                    _vram_bytes(w, h, wide, True),
                    gpu_before=before,
                    gpu_after=_vram_bytes(w, h, wide, True),
                )
            )
        return out

    # 2D textures: UI, sprites, backgrounds.
    if mode == 0 and e.shipped_bytes > 1_000_000:
        out.append(
            Proposal(
                "lossy-2d",
                REVIEW,
                e,
                {"compress/mode": 1, "compress/lossy_quality": 0.8},
                "large 2D texture stored Lossless",
                e.shipped_bytes,
                e.shipped_bytes // 4,
            )
        )
    if dims and max(dims) > max_2d and (not limit or limit > max_2d):
        w, h = _capped(dims, max_2d)
        assert cur is not None
        ratio = (w * h) / (cur[0] * cur[1])
        vram = mode == 2
        out.append(
            Proposal(
                "cap-2d",
                REVIEW,
                e,
                {"process/size_limit": max_2d},
                f"{dims[0]}x{dims[1]} source",
                e.shipped_bytes,
                int(e.shipped_bytes * ratio),
                gpu_before=_vram_bytes(*cur, alpha, mip) if vram else _rgba_bytes(*cur, mip),
                gpu_after=_vram_bytes(w, h, alpha, mip) if vram else _rgba_bytes(w, h, mip),
            )
        )
    return out


def _wav_rules(e: ImportEntry) -> list[Proposal]:
    if e.compress_mode != 0 or e.shipped_bytes < 1_000_000:
        return []
    return [
        Proposal(
            "wav-qoa",
            REVIEW,
            e,
            {"compress/mode": 2},
            "uncompressed WAV",
            e.shipped_bytes,
            e.shipped_bytes // 5,
        )
    ]


# ----------------------------------------------------------------- apply / undo


def backups_dir(project: GodotProject) -> Path:
    return project.root / ".gsk" / "backups"


def apply(project: GodotProject, proposals: list[Proposal]) -> Path | None:
    """Write proposals into their ``.import`` files. Returns the backup folder."""
    if not proposals:
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    backup = backups_dir(project) / stamp
    n = 1
    while backup.exists():
        n += 1
        backup = backups_dir(project) / f"{stamp}-{n}"
    backup.mkdir(parents=True)
    merged: dict[Path, dict[str, Any]] = {}
    for p in proposals:
        merged.setdefault(p.entry.import_path, {}).update(p.changes)
    files: list[dict[str, Any]] = []
    manifest = {"created": stamp, "files": files}
    for path, changes in merged.items():
        rel = path.relative_to(project.root)
        dst = backup / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dst)
        with path.open(encoding="utf-8", newline="") as fh:  # keep the file's own line endings
            text = fh.read()
        with path.open("w", encoding="utf-8", newline="") as fh:
            fh.write(godot_cfg.set_values(text, "params", changes))
        files.append({"file": rel.as_posix(), "changes": changes})
    (backup / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    _ensure_gitignored(project)
    project.reload_imports()
    return backup


def _ensure_gitignored(project: GodotProject) -> None:
    gsk_dir = project.root / ".gsk"
    gi = gsk_dir / ".gitignore"
    if not gi.exists():
        gi.write_text("*\n", encoding="utf-8")


def list_backups(project: GodotProject) -> list[Path]:
    root = backups_dir(project)
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if (p / "manifest.json").is_file() and not p.name.endswith(".restored"))


def undo(project: GodotProject, name: str | None = None) -> Path | None:
    backups = list_backups(project)
    if not backups:
        return None
    target = next((b for b in backups if b.name == name), None) if name else backups[-1]
    if target is None:
        return None
    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    root = project.root.resolve()
    pairs: list[tuple[Path, Path]] = []
    for item in manifest.get("files", []):
        rel = str(item.get("file", ""))
        src, dst = target / rel, project.root / rel
        # A manifest is data from disk (possibly from a cloned repo): only restore
        # regular .import files that stay inside the backup and the project.
        if (
            not rel.endswith(".import")
            or Path(rel).is_absolute()
            or src.is_symlink()
            or dst.is_symlink()
            or not src.resolve().is_relative_to(target.resolve())
            or not dst.resolve().is_relative_to(root)
        ):
            raise ValueError(f"Refusing to restore {rel!r} from {target.name}: not an .import file inside the project")
        pairs.append((src, dst))
    for src, dst in pairs:
        shutil.copy2(src, dst)
    shutil.move(str(target), str(target.with_name(target.name + ".restored")))
    project.reload_imports()
    return target


def reimport(godot: str, project: GodotProject, timeout: int = 3600) -> subprocess.CompletedProcess[str]:
    """Re-import changed assets headlessly so ``.godot/imported`` reflects the new settings."""
    return subprocess.run(
        [godot, "--headless", "--path", str(project.root), "--import"],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# ----------------------------------------------------------------- GLB stats


def glb_triangles(path: Path) -> int | None:
    """Triangle count of a binary glTF from its JSON chunk, without loading meshes."""
    try:
        with path.open("rb") as fh:
            header = fh.read(12)
            if len(header) < 12 or header[:4] != b"glTF":
                return None
            length, ctype = struct.unpack("<I4s", fh.read(8))
            if ctype != b"JSON":
                return None
            doc = json.loads(fh.read(length))
    except (OSError, ValueError, struct.error):
        return None
    try:
        accessors = doc.get("accessors", [])
        total = 0
        for mesh in doc.get("meshes", []):
            for prim in mesh.get("primitives", []):
                if prim.get("mode", 4) != 4:
                    continue
                if "indices" in prim:
                    count = accessors[prim["indices"]].get("count", 0)
                else:
                    pos = prim.get("attributes", {}).get("POSITION")
                    count = accessors[pos].get("count", 0) if pos is not None else 0
                total += int(count) // 3
        return total
    except (IndexError, KeyError, TypeError, AttributeError, ValueError):
        return None
