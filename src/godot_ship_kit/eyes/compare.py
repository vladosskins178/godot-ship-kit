"""Perceptual comparison of a screenshot against its reference."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import numpy as np
    from PIL import Image, ImageDraw
except ImportError as exc:  # pragma: no cover - exercised only without the extra
    raise ImportError("gsk eyes needs Pillow and numpy: pip install 'godot-ship-kit[eyes]'") from exc


@dataclass
class Diff:
    similarity: float  # mean SSIM over luminance, 1.0 = identical
    changed_ratio: float  # share of pixels whose RGB moved more than the tolerance
    max_delta: int
    regions: list[tuple[float, float, float, float]] = field(default_factory=list)  # x, y, w, h fractions
    size_mismatch: bool = False
    largest_region_px: int = 0  # changed pixels inside the biggest region

    def to_dict(self) -> dict[str, Any]:
        return {
            "similarity": round(self.similarity, 5),
            "changed_ratio": round(self.changed_ratio, 6),
            "max_delta": self.max_delta,
            "regions": [[round(v, 4) for v in r] for r in self.regions],
            "size_mismatch": self.size_mismatch,
            "largest_region_px": self.largest_region_px,
        }


def load_rgb(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        return np.asarray(im.convert("RGB"), dtype=np.int16)


def _luma(rgb: np.ndarray) -> np.ndarray:
    return (rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114).astype(np.float64)


def _box_mean(img: np.ndarray, k: int) -> np.ndarray:
    """Mean over every k x k window (valid region) via an integral image."""
    s = np.pad(img, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    total: np.ndarray = s[k:, k:] - s[:-k, k:] - s[k:, :-k] + s[:-k, :-k]
    return total / (k * k)


def ssim(a: np.ndarray, b: np.ndarray, window: int = 8) -> float:
    """Structural similarity of two grayscale images (box-window variant)."""
    if min(a.shape) < window:
        return 1.0 if np.array_equal(a, b) else 0.0
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    mu_a, mu_b = _box_mean(a, window), _box_mean(b, window)
    var_a = _box_mean(a * a, window) - mu_a**2
    var_b = _box_mean(b * b, window) - mu_b**2
    cov = _box_mean(a * b, window) - mu_a * mu_b
    num = (2 * mu_a * mu_b + c1) * (2 * cov + c2)
    den = (mu_a**2 + mu_b**2 + c1) * (var_a + var_b + c2)
    return float(np.mean(num / den))


def _downscale(rgb: np.ndarray, long_side: int = 1024) -> np.ndarray:
    h, w = rgb.shape[:2]
    if max(h, w) <= long_side:
        return rgb
    scale = long_side / max(h, w)
    im = Image.fromarray(rgb.astype(np.uint8)).resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.BOX)
    return np.asarray(im, dtype=np.int16)


def _regions(mask: np.ndarray, cell: int = 24, max_regions: int = 6) -> list[tuple[int, int, int, int]]:
    """Group changed pixels into boxes on a coarse grid (pixel coordinates), largest first."""
    h, w = mask.shape
    gh, gw = (h + cell - 1) // cell, (w + cell - 1) // cell
    padded = np.zeros((gh * cell, gw * cell), dtype=bool)
    padded[:h, :w] = mask
    grid = padded.reshape(gh, cell, gw, cell).sum(axis=(1, 3)) >= max(2, cell * cell // 200)
    seen = np.zeros_like(grid)
    boxes: list[tuple[int, int, int, int, int]] = []
    for gy, gx in zip(*np.nonzero(grid), strict=False):
        if seen[gy, gx]:
            continue
        stack = [(gy, gx)]
        seen[gy, gx] = True
        y0 = y1 = gy
        x0 = x1 = gx
        n = 0
        while stack:
            cy, cx = stack.pop()
            n += 1
            y0, y1, x0, x1 = min(y0, cy), max(y1, cy), min(x0, cx), max(x1, cx)
            for ny in range(cy - 1, cy + 2):
                for nx in range(cx - 1, cx + 2):
                    if 0 <= ny < gh and 0 <= nx < gw and grid[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
        boxes.append((n, x0 * cell, y0 * cell, min(w, (x1 + 1) * cell) - x0 * cell, min(h, (y1 + 1) * cell) - y0 * cell))
    boxes.sort(key=lambda b: -b[0])
    return [b[1:] for b in boxes[:max_regions]]


def compare(reference: Path, current: Path, diff_out: Path | None = None, tolerance: int = 8) -> Diff:
    ref = load_rgb(reference)
    cur = load_rgb(current)
    if ref.shape != cur.shape:
        return Diff(0.0, 1.0, 255, [], True)
    delta = np.abs(ref - cur).max(axis=2)
    mask = delta > tolerance
    changed = float(mask.mean())
    # SSIM on a 4x box-downscaled copy: it judges structure (shapes, layout, colour
    # blocks) and is not thrown off by dithering or single-pixel rasterisation noise.
    long_side = min(640, max(128, max(ref.shape[:2]) // 4))
    sim = ssim(_luma(_downscale(ref, long_side)), _luma(_downscale(cur, long_side)))
    h, w = mask.shape
    px_regions = _regions(mask) if changed > 0 else []
    regions = [(x / w, y / h, bw / w, bh / h) for x, y, bw, bh in px_regions]
    largest = max((int(mask[y : y + bh, x : x + bw].sum()) for x, y, bw, bh in px_regions), default=0)
    if diff_out is not None:
        render_diff(cur, delta, mask, px_regions, diff_out)
    return Diff(sim, changed, int(delta.max()), regions, largest_region_px=largest)


def render_diff(cur: np.ndarray, delta: np.ndarray, mask: np.ndarray, boxes: list[tuple[int, int, int, int]], out: Path) -> None:
    """Dimmed current frame, changed pixels in red, changed regions boxed."""
    gray = _luma(cur) * 0.35 + 20
    base = np.stack([gray, gray, gray], axis=-1)
    strength = np.clip(delta / 96.0, 0.35, 1.0)[..., None]
    red = np.array([255, 48, 64], dtype=np.float64)
    base = np.where(mask[..., None], base * (1 - strength) + red * strength, base)
    im = Image.fromarray(np.clip(base, 0, 255).astype(np.uint8))
    draw = ImageDraw.Draw(im)
    lw = max(2, min(im.size) // 300)
    for x, y, bw, bh in boxes:
        draw.rectangle([x - lw, y - lw, x + bw + lw, y + bh + lw], outline=(255, 214, 0), width=lw)
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out, optimize=True)


@dataclass
class FrameStats:
    mean_luma: float
    std_luma: float
    white_ratio: float
    black_ratio: float

    def to_dict(self) -> dict[str, float]:
        return {k: round(v, 4) for k, v in self.__dict__.items()}


def frame_stats(path: Path) -> FrameStats:
    y = _luma(_downscale(load_rgb(path), 512))
    return FrameStats(float(y.mean()), float(y.std()), float((y > 248).mean()), float((y < 6).mean()))


def save_optimized(src: Path, dst: Path) -> None:
    """Re-encode a PNG with maximum lossless compression (references live in git)."""
    with Image.open(src) as im:
        im.save(dst, optimize=True)


def crop(path: Path, rect: tuple[float, float, float, float], out: Path, pad: float = 0.0) -> Path:
    """Save a full-resolution crop; ``rect`` is x, y, w, h as fractions."""
    with Image.open(path) as im:
        w, h = im.size
        x, y, rw, rh = rect
        x0 = max(0, int((x - pad) * w))
        y0 = max(0, int((y - pad) * h))
        x1 = min(w, int((x + rw + pad) * w))
        y1 = min(h, int((y + rh + pad) * h))
        out.parent.mkdir(parents=True, exist_ok=True)
        im.crop((x0, y0, max(x0 + 1, x1), max(y0 + 1, y1))).save(out)
    return out


def side_by_side(left: Path, right: Path, rect: tuple[float, float, float, float], out: Path, pad: float = 0.05) -> Path:
    """Reference crop | current crop, for zooming into one changed region."""
    tmp_l, tmp_r = out.with_suffix(".l.png"), out.with_suffix(".r.png")
    crop(left, rect, tmp_l, pad)
    crop(right, rect, tmp_r, pad)
    with Image.open(tmp_l) as a, Image.open(tmp_r) as b:
        gap = 12
        canvas = Image.new("RGB", (a.width + b.width + gap, max(a.height, b.height)), (24, 24, 28))
        canvas.paste(a.convert("RGB"), (0, 0))
        canvas.paste(b.convert("RGB"), (a.width + gap, 0))
        canvas.save(out)
    tmp_l.unlink(missing_ok=True)
    tmp_r.unlink(missing_ok=True)
    return out
