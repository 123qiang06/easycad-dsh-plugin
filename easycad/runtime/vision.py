"""EasyCAD-owned image helpers: CAD shape vs reference-image similarity.

Approach (v1): silhouette IoU. The generated B-rep is tessellated and projected
orthographically into standard views (front / right / top); each projection is
rasterised into a binary silhouette and normalised to a square grid. The
reference image is thresholded into a foreground mask (Otsu, alpha-aware) and
the largest coherent blob is kept. The best IoU across the standard views and
the mirror / flip / transpose variants is the similarity score.

This is deliberately a geometry-shape comparator, not a photo-embedding model:
it tolerates different lighting and background, but a full multi-view
engineering drawing will fragment into many blobs and is reported as a
"drawing-like" reference (lower confidence) rather than a clean silhouette.
"""

from __future__ import annotations

import numpy as np

GRID = 128
VIEWS = ("front", "right", "top")

# screen axes for each standard view: (u_axis, v_axis) into (X, Y, Z).
#  front = look along +Y (X across, Z up)  right = look along +X  top = look down
_VIEW_AXES = {
    "front": (0, 2),
    "right": (1, 2),
    "top": (0, 1),
}


def _otsu(gray: np.ndarray) -> float | None:
    """Otsu threshold on a float image in [0, 1]. Returns the threshold or None.

    Unlike a naive argmax over bin indices (which can return an extreme bin when
    the within-gap between two clean modes yields a flat variance plateau), this
    returns the MIDPOINT of the two class means at the best split, which stays in
    the middle of a clean bimodal histogram.
    """
    hist, _ = np.histogram(gray, bins=256, range=(0.0, 1.0))
    total = int(gray.size)
    if total == 0:
        return None
    hist = hist.astype(np.float64)
    bins = np.arange(256, dtype=np.float64)
    weight = hist / total
    cum_w = np.cumsum(weight)
    cum_x = np.cumsum(bins * weight)
    total_mean = cum_x[-1]
    best_var, best_split = -1.0, None
    for i in range(1, 256):
        w_b = cum_w[i - 1]
        if w_b <= 0 or w_b >= 1.0:
            continue
        mean_b = cum_x[i - 1] / w_b
        mean_f = (total_mean - cum_x[i - 1]) / (1.0 - w_b)
        var = w_b * (1.0 - w_b) * (mean_b - mean_f) ** 2
        if var > best_var:
            best_var, best_split = var, (mean_b + mean_f) / 2.0
    if best_split is None:
        return None
    return best_split / 255.0


def _largest_blob(mask: np.ndarray, keep: int = 1, min_frac: float = 0.003):
    """Keep up to `keep` largest 8-connected components, each >= min_frac of total."""
    try:
        from scipy import ndimage
    except Exception:  # pragma: no cover - scipy has no fallback
        return mask
    labeled, n = ndimage.label(mask)
    if n <= 1:
        return mask
    sizes = np.bincount(labeled.ravel())
    sizes = sizes[1:]
    total = float(mask.sum())
    order = np.argsort(sizes)[::-1]
    chosen = []
    for idx in order:
        if sizes[idx] < max(8, min_frac * total):
            break
        if len(chosen) >= keep:
            break
        chosen.append(idx + 1)
    if not chosen:
        chosen = [int(order[0]) + 1]
    return np.isin(labeled, chosen)


def foreground_mask(img, *, keep_blob: bool = True):
    """Return (mask, source, note).

    source in {"alpha", "gray"} and note describes any degrading assumptions.
    A foreground that is very fragmented (many comparable blobs) is treated as a
    drawing-like reference and reduced to its largest blob.
    """
    from PIL import Image

    if not isinstance(img, Image.Image):
        img = Image.open(img)
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        alpha = np.asarray(rgba)[:, :, 3]
        if int(alpha.min()) < 250:
            mask = alpha > 0
            note = "alpha channel"
            if keep_blob:
                mask = _largest_blob(mask)
            return mask, "alpha", note
        img = img.convert("L")
    else:
        img = img.convert("L")
    gray = np.asarray(img, dtype=np.float64) / 255.0
    t = _otsu(gray)
    if t is None:
        t = 0.5
    mask = gray < t
    if float(mask.mean()) > 0.5:  # mostly dark => background is dark
        mask = ~mask
    # Count blobs to detect a fragmented (drawing-like) reference.
    try:
        from scipy import ndimage
        labeled, n = ndimage.label(mask)
        if n > 1:
            sizes = np.bincount(labeled.ravel())[1:]
            total = float(mask.sum())
            if total > 0 and (sizes > 0.05 * total).sum() > 1:
                mask = _largest_blob(mask)
                return mask, "gray", "fragmented (drawing-like); kept largest blob"
    except Exception:
        pass
    if keep_blob:
        mask = _largest_blob(mask)
    return mask, "gray", "Otsu threshold"


def _crop_square(mask: np.ndarray, grid: int = GRID):
    """Crop to bounding box, pad to square, resize to grid. Returns bool grid."""
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return None
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    sub = (mask[y0:y1, x0:x1].astype(np.uint8)) * 255
    from PIL import Image
    img = Image.fromarray(sub)
    w, h = img.size
    side = max(w, h, 1)
    canvas = Image.new("L", (side, side), 0)
    canvas.paste(img, ((side - w) // 2, (side - h) // 2))
    canvas = canvas.resize((grid, grid), Image.LANCZOS)
    return np.asarray(canvas) > 127


def reference_grid(mask: np.ndarray, grid: int = GRID):
    return _crop_square(mask, grid)


def iou(a: np.ndarray, b: np.ndarray) -> float:
    if a is None or b is None:
        return 0.0
    inter = float(np.logical_and(a, b).sum())
    union = float(np.logical_or(a, b).sum())
    if union == 0:
        return 0.0
    return inter / union


def best_iou(ref: np.ndarray, cad: np.ndarray) -> tuple[float, str]:
    """Best IoU over mirror / flip / transpose to absorb orientation ambiguity.

    cad variants: identity, vertical flip, horizontal flip, transpose (90 deg).
    """
    if ref is None or cad is None:
        return 0.0, "none"
    variants = {
        "identity": cad,
        "flipud": cad[::-1, :],
        "fliplr": cad[:, ::-1],
        "transpose": cad.T,
    }
    best, best_name = 0.0, "identity"
    for name, cand in variants.items():
        score = iou(ref, cand)
        if score > best:
            best, best_name = score, name
    return best, best_name


def _rasterize(u, v, tris, size: int, pad_frac: float = 0.06):
    """Fill triangles into a binary grid using PIL polygon fill.

    u and v are scaled by a COMMON factor (largest span) and centered, so the
    projected aspect ratio is preserved rather than stretched to a square.
    """
    from PIL import Image, ImageDraw

    umin, umax = float(u.min()), float(u.max())
    vmin, vmax = float(v.min()), float(v.max())
    span_u = max(umax - umin, 1e-9)
    span_v = max(vmax - vmin, 1e-9)
    pad = int(size * pad_frac)
    scale = (size - 2 * pad) / max(span_u, span_v)
    cu = (umin + umax) / 2.0
    cv = (vmin + vmax) / 2.0
    img = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(img)

    def px(x):
        return size / 2.0 + (x - cu) * scale

    def py(y):
        # flip Y so +v points up (screen coordinate grows downward)
        return size / 2.0 - (y - cv) * scale

    for t in tris:
        pts = [
            (px(u[t[0]]), py(v[t[0]])),
            (px(u[t[1]]), py(v[t[1]])),
            (px(u[t[2]]), py(v[t[2]])),
        ]
        draw.polygon(pts, fill=255)
    return np.asarray(img) > 0


def cad_silhouettes(vertices, triangles, grid: int = GRID, size: int = 512):
    """Return {view: normalized bool grid} for front/right/top."""
    vertices = np.asarray(vertices, dtype=np.float64)
    triangles = np.asarray(triangles, dtype=np.int64)
    out = {}
    for name, (aidx, bidx) in _VIEW_AXES.items():
        mask = _rasterize(vertices[:, aidx], vertices[:, bidx], triangles, size)
        out[name] = _crop_square(mask, grid)
    return out


def cad_view_image(vertices, triangles, view: str, size: int = 512):
    """Render a single view to a PIL image (white bg, black silhouette)."""
    if view == "iso":
        u = vertices[:, 0] + 0.35 * vertices[:, 1]
        v = vertices[:, 2] + 0.25 * vertices[:, 1]
    elif view in _VIEW_AXES:
        aidx, bidx = _VIEW_AXES[view]
        u, v = vertices[:, aidx], vertices[:, bidx]
    else:
        raise ValueError(f"unknown view: {view!r}")
    from PIL import Image, ImageDraw

    umin, umax = float(u.min()), float(u.max())
    vmin, vmax = float(v.min()), float(v.max())
    span_u = max(umax - umin, 1e-9)
    span_v = max(vmax - vmin, 1e-9)
    pad = int(size * 0.06)
    scale = (size - 2 * pad) / max(span_u, span_v)
    cu = (umin + umax) / 2.0
    cv = (vmin + vmax) / 2.0
    img = Image.new("L", (size, size), 255)
    draw = ImageDraw.Draw(img)
    for t in triangles:
        pts = [
            (size / 2.0 + (u[t[0]] - cu) * scale, size / 2.0 - (v[t[0]] - cv) * scale),
            (size / 2.0 + (u[t[1]] - cu) * scale, size / 2.0 - (v[t[1]] - cv) * scale),
            (size / 2.0 + (u[t[2]] - cu) * scale, size / 2.0 - (v[t[2]] - cv) * scale),
        ]
        draw.polygon(pts, fill=0)
    return img


def grade(score: float) -> tuple[str, str]:
    """Return (label, pass?) for a similarity score."""
    if score >= 0.80:
        return "很高", True
    if score >= 0.65:
        return "较高", True
    if score >= 0.50:
        return "中等", True
    if score >= 0.35:
        return "较低", False
    return "很低", False
