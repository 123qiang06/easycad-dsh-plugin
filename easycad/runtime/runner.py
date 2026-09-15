"""Die-cast runner (gating) P0: brief → propose → template build → rule QA.

Does not import reference projects. Geometry uses build123d boxes/cylinders,
not freeform LLM Sweep source.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
MODELS = ROOT / "models"

PARTING_DIRS = {
    "+X": (1.0, 0.0, 0.0),
    "-X": (-1.0, 0.0, 0.0),
    "+Y": (0.0, 1.0, 0.0),
    "-Y": (0.0, -1.0, 0.0),
    "+Z": (0.0, 0.0, 1.0),
    "-Z": (0.0, 0.0, -1.0),
}

# Aluminum HPDC defaults (mm / s). Soft bands only.
FILL_TIME_S = 0.08
GATE_SPEED_MMPS = 35000.0
GATE_THICK_MIN = 1.2
GATE_THICK_MAX = 2.4
RUNNER_TO_GATE = 3.2
# Bump when mold/runner geometry rules change so the preview iframe rebuilds.
MOLD_PREVIEW = "split-open-5"


def _read_json(path: Path, fallback):
    if not path.is_file():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return fallback


def runner_path(stem: str) -> Path:
    return MODELS / f"{stem}.runner.json"


def load_runner(stem: str) -> dict:
    data = _read_json(runner_path(stem), {})
    return data if isinstance(data, dict) else {}


def save_runner(stem: str, data: dict) -> Path:
    MODELS.mkdir(parents=True, exist_ok=True)
    path = runner_path(stem)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _norm_parting(value: str | None) -> str:
    text = str(value or "+Z").strip().upper().replace(" ", "")
    if text in PARTING_DIRS:
        return text
    aliases = {"Z": "+Z", "X": "+X", "Y": "+Y", "UP": "+Z", "TOP": "+Z"}
    return aliases.get(text, "+Z")


def _as_int_list(raw) -> list[int]:
    out = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return sorted(set(out))


def _as_count_range(raw, default=(1, 2)) -> list[int]:
    if isinstance(raw, list) and len(raw) >= 2:
        try:
            lo, hi = int(raw[0]), int(raw[1])
            if lo < 1:
                lo = 1
            if hi < lo:
                hi = lo
            return [lo, hi]
        except (TypeError, ValueError):
            pass
    try:
        n = int(raw)
        n = max(1, n)
        return [n, n]
    except (TypeError, ValueError):
        return [default[0], default[1]]


def default_brief(stem: str, payload: dict | None = None) -> dict:
    payload = payload if isinstance(payload, dict) else {}
    return {
        "name": stem,
        "kind": "runner",
        "parting_dir": _norm_parting(payload.get("parting_dir")),
        "keepout_face_ids": _as_int_list(payload.get("keepout_face_ids")),
        "gate_face_ids": _as_int_list(payload.get("gate_face_ids")),
        "gate_count": _as_count_range(payload.get("gate_count") or payload.get("gate_count_range")),
        "process": str(payload.get("process") or "hpdc"),
        "alloy": str(payload.get("alloy") or "AlSi12"),
        "prefer": str(payload.get("prefer") or "fill_quality"),
        "intent": str(payload.get("intent") or payload.get("notes") or "压铸流道：可剪、不碰禁区"),
    }


def validate_runner_brief(brief: dict, models: Path | None = None) -> list[dict]:
    defects = []
    stem = str(brief.get("name") or "")
    if not stem:
        defects.append({"id": "name", "message": "runner brief 缺少 name"})
    if _norm_parting(brief.get("parting_dir")) not in PARTING_DIRS:
        defects.append({"id": "parting", "message": "分型方向必须是 +X/-X/+Y/-Y/+Z/-Z"})
    counts = _as_count_range(brief.get("gate_count"))
    if counts[0] < 1 or counts[1] > 6:
        defects.append({"id": "gate_count", "message": "浇口数区间应在 1～6"})
    keep = set(_as_int_list(brief.get("keepout_face_ids")))
    gates = _as_int_list(brief.get("gate_face_ids"))
    clash = sorted(keep.intersection(gates))
    if clash:
        defects.append({"id": "keepout", "message": f"浇口面落在禁区: {clash}"})
    if models is not None and stem:
        has_part = any((models / f"{stem}{ext}").is_file() for ext in (".step", ".stp", ".step.py", ".glb"))
        if not has_part:
            defects.append({"id": "part_missing", "message": f"没有零件 models/{stem}.step，先导入或 gen"})
    return defects


def load_faces(stem: str) -> list[dict]:
    topo = _read_json(MODELS / f"{stem}.topology.json", {})
    faces = topo.get("faces") if isinstance(topo, dict) else None
    if isinstance(faces, list) and faces:
        return [f for f in faces if isinstance(f, dict)]
    return []


def _dot(a, b) -> float:
    return float(a[0]) * float(b[0]) + float(a[1]) * float(b[1]) + float(a[2]) * float(b[2])


def _norm(v) -> float:
    return max((_dot(v, v)) ** 0.5, 1e-9)


def _plane_uv(axis: int) -> tuple[int, int]:
    if axis == 2:
        return 0, 1
    if axis == 0:
        return 1, 2
    return 0, 2


def _inplane_vec(pt, origin, axis: int) -> tuple[float, float, float]:
    v = [float(pt[0]) - float(origin[0]), float(pt[1]) - float(origin[1]), float(pt[2]) - float(origin[2])]
    v[axis] = 0.0
    return (v[0], v[1], v[2])


def _score_face(face: dict, parting: str, bbox: dict) -> float:
    """Prefer outer side walls on the back parting. Down-rank pads, studs, bores."""
    normal = face.get("normal") or [0, 0, 1]
    area = float(face.get("area") or 0)
    ftype = str(face.get("type") or "").upper()
    pdir = PARTING_DIRS[parting]
    align = abs(_dot(normal, pdir)) / _norm(normal)
    side = 1.0 - min(1.0, align)
    centroid = face.get("centroid") or bbox.get("center") or [0, 0, 0]
    axis = _parting_axis(parting)
    mn, mx = bbox["min"][axis], bbox["max"][axis]
    span = max(mx - mn, 1e-6)
    along = (float(centroid[axis]) - mn) / span
    if not parting.startswith("+"):
        along = 1.0 - along
    near_back = 1.0 - along
    origin = bbox.get("center") or [0, 0, 0]
    radial_vec = _inplane_vec(centroid, origin, axis)
    u, v = _plane_uv(axis)
    r_bbox = max(abs(bbox["max"][u] - origin[u]), abs(bbox["min"][u] - origin[u]),
                 abs(bbox["max"][v] - origin[v]), abs(bbox["min"][v] - origin[v]), 1e-6)
    radial = min(_norm(radial_vec) / r_bbox, 1.2)
    n_in = list(normal)
    n_in[axis] = 0.0
    inward = _dot(n_in, radial_vec) < -0.15
    size = math.log10(max(area, 10.0)) * 0.45
    score = side * 1.3 + radial * 2.1 + near_back * 0.9 + size
    if "CYLINDER" in ftype and radial > 0.88 and not inward:
        score += 0.7
    if inward:
        score -= 2.8
    if area < 800 and radial < 0.9:
        score -= 1.1
    if align > 0.85:
        score -= 0.8
    return score


def _bbox_from_facts(facts: dict | None, faces: list[dict]) -> dict:
    size = (facts or {}).get("size_mm") if isinstance(facts, dict) else None
    center = (facts or {}).get("center_mm") if isinstance(facts, dict) else None
    if isinstance(size, list) and len(size) == 3 and isinstance(center, list) and len(center) == 3:
        half = [float(s) / 2 for s in size]
        c = [float(x) for x in center]
        return {
            "min": [c[i] - half[i] for i in range(3)],
            "max": [c[i] + half[i] for i in range(3)],
            "size": [float(s) for s in size],
            "center": c,
        }
    xs, ys, zs = [], [], []
    for face in faces:
        ctr = face.get("centroid") or []
        if len(ctr) == 3:
            xs.append(float(ctr[0]))
            ys.append(float(ctr[1]))
            zs.append(float(ctr[2]))
    if not xs:
        return {"min": [-40, -30, 0], "max": [40, 30, 8], "size": [80, 60, 8], "center": [0, 0, 4]}
    mn = [min(xs), min(ys), min(zs)]
    mx = [max(xs), max(ys), max(zs)]
    return {
        "min": mn,
        "max": mx,
        "size": [mx[i] - mn[i] for i in range(3)],
        "center": [(mn[i] + mx[i]) / 2 for i in range(3)],
    }


def _volume_mm3(facts: dict | None, bbox: dict) -> float:
    if isinstance(facts, dict) and facts.get("volume_mm3"):
        try:
            return max(float(facts["volume_mm3"]), 1.0)
        except (TypeError, ValueError):
            pass
    s = bbox["size"]
    return max(float(s[0]) * float(s[1]) * float(s[2]) * 0.55, 1.0)


def _section_params(volume_mm3: float, n_gates: int) -> dict:
    fill_s = FILL_TIME_S
    ag = volume_mm3 / max(GATE_SPEED_MMPS * fill_s, 1.0)
    ag = max(8.0, min(ag, 80.0))
    per = ag / max(n_gates, 1)
    thick = min(GATE_THICK_MAX, max(GATE_THICK_MIN, 1.6))
    width = max(per / thick, 6.0)
    runner_in = max(ag * RUNNER_TO_GATE, 24.0)
    runner_out = max(ag * 1.4, 14.0)
    return {
        "gate_area": round(ag, 2),
        "gate_width": round(width, 2),
        "gate_thick": round(thick, 2),
        "runner_in": round(runner_in, 2),
        "runner_out": round(runner_out, 2),
        "biscuit_d": 18.0,
        "fill_time_s": fill_s,
    }


def propose_candidates(stem: str, brief: dict, facts: dict | None = None) -> list[dict]:
    faces = load_faces(stem)
    bbox = _bbox_from_facts(facts, faces)
    keep = set(_as_int_list(brief.get("keepout_face_ids")))
    forced = _as_int_list(brief.get("gate_face_ids"))
    lo, hi = _as_count_range(brief.get("gate_count"))
    parting = _norm_parting(brief.get("parting_dir"))
    ranked = []
    for face in faces:
        fid = int(face.get("id") or 0)
        if fid in keep:
            continue
        if float(face.get("area") or 0) < 20:
            continue
        ranked.append((_score_face(face, parting, bbox), fid, face))
    ranked.sort(key=lambda row: row[0], reverse=True)

    def _pick(n: int) -> list[int]:
        axis = _parting_axis(parting)
        picked: list[int] = []
        dirs: list[tuple[float, float, float]] = []
        top = ranked[0][0] if ranked else 0.0
        for score, fid, face in ranked:
            if picked and score < top * 0.7:
                break
            d = _inplane_vec(face.get("centroid") or bbox["center"], bbox["center"], axis)
            nrm = _norm(d)
            d = (d[0] / nrm, d[1] / nrm, d[2] / nrm)
            if dirs and any(_dot(d, old) > 0.82 for old in dirs) and n > 1:
                continue
            picked.append(fid)
            dirs.append(d)
            if len(picked) >= n:
                break
        if not picked and ranked:
            picked = [ranked[0][1]]
        return picked

    def pack(ids: list[int], n_gates: int, label: str, rationale: str, prefer: str) -> dict:
        n = max(1, n_gates, len(ids))
        params = _section_params(_volume_mm3(facts, bbox), n)
        params["n_gates"] = n
        nodes = [{"id": "biscuit", "kind": "biscuit"}]
        edges = []
        graph_ids = list(ids)
        if graph_ids and n > len(graph_ids):
            graph_ids = graph_ids + [graph_ids[0]] * (n - len(graph_ids))
        for i, fid in enumerate(graph_ids):
            gid = f"gate_{i}"
            nodes.append({"id": gid, "kind": "gate", "face_id": fid})
            edges.append({"from": "biscuit", "to": gid})
        return {
            "id": label,
            "prefer": prefer,
            "gate_face_ids": ids,
            "rationale": rationale,
            "params": params,
            "graph": {"nodes": nodes, "edges": edges},
        }

    out = []
    if forced:
        clipped = [fid for fid in forced if fid not in keep][:hi]
        if clipped:
            out.append(pack(clipped, max(len(clipped), lo), "C", "尊重人指定的浇口面", "intent"))
    ids_a = _pick(1)
    if ids_a:
        out.append(pack(ids_a, min(lo, 6), "A", "外圆短树，贴分型面可剪", "cost"))
        if hi > lo:
            out.append(pack(ids_a, min(hi, 6), "B", "同侧多口，沿外圆充型", "fill_quality"))
    seen = set()
    uniq = []
    for cand in out:
        key = (tuple(cand["gate_face_ids"]), int((cand.get("params") or {}).get("n_gates") or 0))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(cand)
    if not uniq:
        uniq.append(pack([], 1, "A", "没有可用浇口面：请点面或放宽禁区", "cost"))
    return uniq[:3]


def review_runner(stem: str) -> dict:
    data = load_runner(stem)
    brief = data.get("brief") if isinstance(data.get("brief"), dict) else {}
    if not brief:
        brief = default_brief(stem)
    defects = validate_runner_brief(brief, MODELS)
    return {
        "ok": True,
        "pass": not defects,
        "name": stem,
        "defects": defects,
        "revisions": [d["message"] for d in defects],
        "hint": "修订 easycad_runner_brief 后再 propose/build" if defects else "可以 easycad_runner_propose / build",
        "brief": brief,
    }


def cmd_runner_brief(stem: str, payload: dict) -> dict:
    brief = default_brief(stem, payload)
    defects = validate_runner_brief(brief, MODELS)
    data = load_runner(stem)
    data["name"] = stem
    data["brief"] = brief
    if defects:
        data.pop("selected", None)
    path = save_runner(stem, data)
    return {
        "ok": not defects,
        "name": stem,
        "brief": brief,
        "path": f"models/{stem}.runner.json",
        "defects": defects,
        "hint": "先修缺陷再 propose" if defects else "接着 easycad_runner_review，通过后 propose",
        "runner_path": str(path),
    }


def cmd_runner_propose(stem: str) -> dict:
    data = load_runner(stem)
    brief = data.get("brief") if isinstance(data.get("brief"), dict) else default_brief(stem)
    defects = validate_runner_brief(brief, MODELS)
    if defects:
        return {"ok": False, "name": stem, "pass": False, "defects": defects, "error": "runner brief 未过闸"}
    facts = _read_json(MODELS / f"{stem}.qa.json", {})
    if not facts.get("size_mm"):
        mem = _read_json(MODELS / f"{stem}.memory.json", {})
        facts = (mem.get("facts") if isinstance(mem, dict) else None) or facts
    candidates = propose_candidates(stem, brief, facts if isinstance(facts, dict) else None)
    prefer = str(brief.get("prefer") or "fill_quality")
    selected = next((c["id"] for c in candidates if c.get("prefer") == prefer), candidates[0]["id"])
    if any(c["id"] == "C" for c in candidates):
        selected = "C"
    data["brief"] = brief
    data["candidates"] = candidates
    data["selected"] = selected
    save_runner(stem, data)
    return {
        "ok": True,
        "name": stem,
        "pass": True,
        "selected": selected,
        "candidates": candidates,
        "hint": "选候选后 easycad_runner_build；或直接 build 用 selected",
        "path": f"models/{stem}.runner.json",
    }


def _load_part_shape(stem: str):
    from build123d import import_step

    for ext in (".step", ".stp"):
        path = MODELS / f"{stem}{ext}"
        if path.is_file():
            return import_step(str(path))
    script = MODELS / f"{stem}.step.py"
    if script.is_file():
        import importlib.util

        spec = importlib.util.spec_from_file_location(f"easycad_{stem}", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        fn = getattr(module, "gen_step", None)
        if fn:
            return fn()
    raise FileNotFoundError(f"models/{stem}.step 不存在")


def _face_center(shape, face_id: int, fallback):
    try:
        faces = list(shape.faces())
        if 0 <= face_id < len(faces):
            c = faces[face_id].center()
            return (float(c.X), float(c.Y), float(c.Z))
    except Exception:
        pass
    return fallback


def _face_normal(shape, face_id: int, fallback):
    try:
        faces = list(shape.faces())
        if 0 <= face_id < len(faces):
            n = faces[face_id].normal_at(faces[face_id].center())
            return (float(n.X), float(n.Y), float(n.Z))
    except Exception:
        pass
    return fallback


def _parting_axis(parting: str) -> int:
    return {"+X": 0, "-X": 0, "+Y": 1, "-Y": 1, "+Z": 2, "-Z": 2}[_norm_parting(parting)]


def _on_parting(pt, axis: int, value: float) -> tuple[float, float, float]:
    q = [float(pt[0]), float(pt[1]), float(pt[2])]
    q[axis] = value
    return (q[0], q[1], q[2])


def _inplane_outward(normal, pdir) -> tuple[float, float, float]:
    d = _dot(normal, pdir)
    v = (normal[0] - d * pdir[0], normal[1] - d * pdir[1], normal[2] - d * pdir[2])
    length = _norm(v)
    if length < 1e-5:
        if abs(pdir[0]) < 0.9:
            v = (1.0, 0.0, 0.0)
        else:
            v = (0.0, 1.0, 0.0)
        d = _dot(v, pdir)
        v = (v[0] - d * pdir[0], v[1] - d * pdir[1], v[2] - d * pdir[2])
        length = _norm(v)
    return (v[0] / length, v[1] / length, v[2] / length)


def _parting_value(mn, mx, parting: str) -> float:
    """Back face: features live in the +parting half, lid is nearly flat."""
    axis = _parting_axis(parting)
    if parting.startswith("+"):
        return float(mn[axis])
    return float(mx[axis])


def _bbox_xyz(shape) -> tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]:
    bbox = shape.bounding_box()
    mn = (float(bbox.min.X), float(bbox.min.Y), float(bbox.min.Z))
    mx = (float(bbox.max.X), float(bbox.max.Y), float(bbox.max.Z))
    center = ((mn[0] + mx[0]) / 2, (mn[1] + mx[1]) / 2, (mn[2] + mx[2]) / 2)
    return mn, mx, center


def _angle_of(vec, axis: int) -> float:
    u, v = _plane_uv(axis)
    return math.atan2(float(vec[v]), float(vec[u]))


def _dir_from_angle(ang: float, axis: int) -> tuple[float, float, float]:
    u, v = _plane_uv(axis)
    out = [0.0, 0.0, 0.0]
    out[u] = math.cos(ang)
    out[v] = math.sin(ang)
    return (out[0], out[1], out[2])


def _rim_radius(center, outward, mn, mx, axis: int) -> float:
    u, v = _plane_uv(axis)
    hx = max((mx[u] - mn[u]) / 2.0, 1e-6)
    hy = max((mx[v] - mn[v]) / 2.0, 1e-6)
    if min(hx, hy) / max(hx, hy) > 0.85:
        return min(hx, hy)
    hit = 1e9
    for i in (u, v):
        if abs(outward[i]) < 1e-9:
            continue
        limit = mx[i] if outward[i] > 0 else mn[i]
        t = (limit - center[i]) / outward[i]
        if t > 1e-6:
            hit = min(hit, t)
    return hit if hit < 1e8 else min(hx, hy)


def _rim_point(center, outward, mn, mx, axis: int, pval: float):
    r = _rim_radius(center, outward, mn, mx, axis)
    pt = [center[i] + outward[i] * r for i in range(3)]
    return _on_parting(pt, axis, pval)


def _point_at_r(center, outward, radius: float, axis: int, pval: float):
    pt = [center[i] + outward[i] * radius for i in range(3)]
    return _on_parting(pt, axis, pval)


def _unwrap_cluster(angles: list[float]) -> list[float]:
    """Sort angles along the shortest covering arc, unwrapped and nondecreasing.

    A naive sort on atan2 jumps at ±π. Two gates beside a left-side biscuit
    then span ~330° and the manifold becomes a hoop around the OD.
    """
    if not angles:
        return []
    ordered = sorted(a % (2.0 * math.pi) for a in angles)
    if len(ordered) == 1:
        return list(ordered)
    n = len(ordered)
    gaps = [ordered[i + 1] - ordered[i] for i in range(n - 1)]
    gaps.append(ordered[0] + 2.0 * math.pi - ordered[-1])
    k = max(range(n), key=lambda i: gaps[i])
    start = ordered[(k + 1) % n]
    seq = []
    for i in range(n):
        ang = ordered[(k + 1 + i) % n]
        if i and ang < start - 1e-9:
            ang += 2.0 * math.pi
        seq.append(ang)
    return seq


def _fan_outwards(start, axis: int, n: int) -> list[tuple[float, float, float]]:
    """Same-side fan around the biscuit. Never walk a full circle."""
    base = _angle_of(start, axis)
    if n <= 1:
        return [_dir_from_angle(base, axis)]
    spread = math.radians(min(28.0 * (n - 1), 100.0))
    return [
        _dir_from_angle(base - spread / 2.0 + spread * i / (n - 1), axis)
        for i in range(n)
    ]


def _gate_outward(raw, nrm, center, axis: int, pdir) -> tuple[float, float, float]:
    radial = _inplane_vec(raw, center, axis)
    if _norm(radial) > 1e-4:
        n = _norm(radial)
        return (radial[0] / n, radial[1] / n, radial[2] / n)
    return _inplane_outward(nrm, pdir)


def _common_volume(a, b) -> float:
    try:
        common = a.intersect(b)
    except Exception:
        return 0.0
    try:
        solids = list(common.solids())
        if solids:
            return max(sum(max(float(s.volume), 0.0) for s in solids), 0.0)
    except Exception:
        pass
    try:
        return max(float(common.volume), 0.0)
    except Exception:
        return 0.0


def _bar(a, b, w: float, h: float, z_dir=None):
    from build123d import Align, Box, Plane, Vector

    va, vb = Vector(*a), Vector(*b)
    vec = vb - va
    length = vec.length
    if length < 0.8:
        return None
    x_dir = vec.normalized()
    if z_dir is not None:
        z_hint = Vector(*z_dir)
        if z_hint.length < 1e-6:
            z_hint = Vector(0, 0, 1)
        else:
            z_hint = z_hint.normalized()
        if abs(x_dir.dot(z_hint)) > 0.92:
            z_hint = Vector(0, 1, 0) if abs(x_dir.Y) < 0.9 else Vector(1, 0, 0)
    else:
        z_hint = Vector(0, 0, 1)
        if abs(x_dir.dot(z_hint)) > 0.92:
            z_hint = Vector(0, 1, 0)
    y_dir = z_hint.cross(x_dir)
    if y_dir.length < 1e-6:
        y_dir = Vector(0, 1, 0)
    else:
        y_dir = y_dir.normalized()
    z_use = x_dir.cross(y_dir)
    if z_use.length < 1e-6:
        z_use = Vector(0, 0, 1)
    else:
        z_use = z_use.normalized()
    plane = Plane(origin=va + vec * 0.5, x_dir=x_dir, z_dir=z_use)
    return plane * Box(length, w, h, align=(Align.CENTER, Align.CENTER, Align.CENTER))


def _build_solids(stem: str, candidate: dict, part_shape, parting: str = "+Z"):
    from build123d import Align, Compound, Cylinder, Plane, Vector

    mn, mx, center = _bbox_xyz(part_shape)
    parting = _norm_parting(parting)
    pdir = PARTING_DIRS[parting]
    axis = _parting_axis(parting)
    pval = _parting_value(mn, mx, parting)
    params = dict(candidate.get("params") or {})
    gates = list(candidate.get("gate_face_ids") or [])
    n_use = max(int(params.get("n_gates") or 0), len(gates), 1)
    runner_in = float(params.get("runner_in") or 28)
    runner_out = float(params.get("runner_out") or 16)
    gate_w = float(params.get("gate_width") or 10)
    gate_t = float(params.get("gate_thick") or 1.8)
    biscuit_d = float(params.get("biscuit_d") or 18)
    # Runner sits on the parting plane (half in the lid, half kissing the back
    # face). Shifting it wholly into the cavity made a blank lid and a hoop
    # fused into the ring wall.
    slab = max(gate_t * 2.0, 5.0)
    in_w = min(max(runner_in / slab, 8.0), 22.0)
    out_w = min(max(runner_out / slab, 6.0), 16.0)
    bite = 3.0
    pval_run = pval

    fallback_out = (-1.0, 0.0, 0.0) if axis != 0 else (0.0, -1.0, 0.0)
    targets = []
    for fid in gates:
        raw = _face_center(part_shape, fid, center)
        nrm = _face_normal(part_shape, fid, fallback_out)
        nlen = _norm(nrm)
        nrm = (nrm[0] / nlen, nrm[1] / nlen, nrm[2] / nlen)
        outward = _gate_outward(raw, nrm, center, axis, pdir)
        pt = _rim_point(center, outward, mn, mx, axis, pval_run)
        targets.append((pt, outward))
    if not targets:
        targets.append((_rim_point(center, fallback_out, mn, mx, axis, pval_run), fallback_out))

    uniq = []
    for item in targets:
        if any(_dot(item[1], old[1]) > 0.97 for old in uniq):
            continue
        uniq.append(item)
    targets = uniq
    if len(targets) < n_use:
        start = targets[0][1]
        targets = [
            (_rim_point(center, out, mn, mx, axis, pval_run), out)
            for out in _fan_outwards(start, axis, n_use)
        ]

    avg_out = [0.0, 0.0, 0.0]
    for _pt, outward in targets:
        for i in range(3):
            avg_out[i] += outward[i]
    avg_out = _inplane_outward((avg_out[0] / n_use, avg_out[1] / n_use, avg_out[2] / n_use), pdir)
    r_out = _rim_radius(center, avg_out, mn, mx, axis)
    r_run = r_out + max(out_w, 8.0) / 2.0 + 6.0
    r_bis = r_out + biscuit_d / 2.0 + 14.0
    biscuit = _point_at_r(center, avg_out, r_bis, axis, pval_run)
    manifold = _point_at_r(center, avg_out, r_run, axis, pval_run)

    solids = []
    cyl = Plane(origin=Vector(*biscuit), z_dir=Vector(*pdir)) * Cylinder(
        biscuit_d / 2, max(slab * 4, 10), align=(Align.CENTER, Align.CENTER, Align.CENTER)
    )
    solids.append(cyl)
    main = _bar(biscuit, manifold, in_w, slab, z_dir=pdir)
    if main is not None:
        solids.append(main)

    cluster = _unwrap_cluster(
        [_angle_of(out, axis) for _pt, out in targets] + [_angle_of(avg_out, axis)]
    )
    if len(cluster) >= 2:
        span = cluster[-1] - cluster[0]
        if span > math.radians(8) and span < math.radians(170):
            steps = max(3, int(span / math.radians(18)) + 1)
            arc_pts = [
                _point_at_r(
                    center,
                    _dir_from_angle(cluster[0] + span * i / steps, axis),
                    r_run,
                    axis,
                    pval_run,
                )
                for i in range(steps + 1)
            ]
            for a, b in zip(arc_pts, arc_pts[1:]):
                bar = _bar(a, b, out_w, slab, z_dir=pdir)
                if bar is not None:
                    solids.append(bar)

    for pt, outward in targets:
        r_gate = _rim_radius(center, outward, mn, mx, axis)
        approach = _point_at_r(center, outward, r_gate + 8.0, axis, pval_run)
        bite_pt = _point_at_r(center, outward, max(r_gate - bite, r_gate * 0.92), axis, pval_run)
        from_arc = _point_at_r(center, outward, r_run, axis, pval_run)
        run = _bar(from_arc, approach, out_w, slab, z_dir=pdir)
        if run is not None:
            solids.append(run)
        gate = _bar(approach, bite_pt, max(gate_w, 6.0), slab, z_dir=pdir)
        if gate is not None:
            solids.append(gate)

    runner = solids[0]
    for extra in solids[1:]:
        try:
            runner = runner.fuse(extra)
        except Exception:
            continue
    shot = part_shape.fuse(runner)
    return Compound(children=[part_shape, runner]), shot, runner


def _rotation_to_plus_z(direction) -> tuple[tuple[float, float, float], float] | None:
    x, y, z = float(direction.X), float(direction.Y), float(direction.Z)
    if z > 0.9:
        return None
    if z < -0.9:
        return ((1.0, 0.0, 0.0), 180.0)
    if x > 0.9:
        return ((0.0, 1.0, 0.0), 90.0)
    if x < -0.9:
        return ((0.0, 1.0, 0.0), -90.0)
    if y > 0.9:
        return ((1.0, 0.0, 0.0), -90.0)
    if y < -0.9:
        return ((1.0, 0.0, 0.0), 90.0)
    return None


def _rotate_about(shape, pivot, axis_dir, angle):
    from build123d import Axis, Vector

    axis = Axis(Vector(float(pivot.X), float(pivot.Y), float(pivot.Z)), Vector(*axis_dir))
    return shape.rotate(axis, angle)


def _as_one_solid(shape):
    try:
        solids = list(shape.solids())
    except Exception:
        return shape
    if not solids:
        return shape
    out = solids[0]
    for extra in solids[1:]:
        try:
            out = out.fuse(extra)
        except Exception:
            continue
    return out


def _largest_solid(shape):
    try:
        solids = list(shape.solids())
    except Exception:
        return shape
    if not solids:
        return shape
    return max(solids, key=lambda item: float(item.volume))


def _steel_minus_shot(block, shot):
    """Prefer a still-plate-sized valid solid. Direct B-rep cut of a gear
    often yields inverted (negative-volume) junk plus leftover part solids."""
    try:
        plate_vol = float(block.volume)
    except Exception:
        return block

    def pick(cut):
        try:
            solids = [s for s in cut.solids() if s.is_valid and float(s.volume) > 0.35 * plate_vol]
        except Exception:
            return None
        if not solids:
            return None
        return max(solids, key=lambda item: float(item.volume))

    fused = _as_one_solid(shot)
    try:
        got = pick(block - fused)
        if got is not None:
            return got
    except Exception:
        pass
    steel = block
    try:
        for extra in list(shot.solids()) or [shot]:
            steel = steel - extra
        got = pick(steel)
        if got is not None:
            return got
    except Exception:
        pass
    return block


def _profile_of_solid(solid, plane):
    try:
        hits = solid.intersect(plane)
    except Exception:
        return None
    faces = [h for h in hits if getattr(h, "area", 0)]
    if not faces:
        return None
    faces.sort(key=lambda face: float(face.area), reverse=True)
    prof = faces[0]
    for face in faces[1:]:
        try:
            prof = prof.fuse(face)
        except Exception:
            continue
    return prof


def _parting_profiles(bodies, plane):
    """One silhouette per body. Fusing a ring + planets fills the bore."""
    profiles = []
    for body in bodies:
        try:
            solids = list(body.solids()) or [body]
        except Exception:
            solids = [body]
        for solid in solids:
            prof = _profile_of_solid(solid, plane)
            if prof is not None:
                profiles.append(prof)
    return profiles


def _pocket_plate_many(block, profiles, depth: float):
    steel = block
    for prof in profiles:
        steel = _pocket_plate(steel, prof, depth)
    return steel


def _pocket_plate(block, profile, depth: float):
    from build123d import extrude

    if profile is None or abs(depth) <= 0.2:
        return block
    try:
        cavity = extrude(profile, amount=depth)
    except Exception:
        return block
    try:
        cut = block - cavity
        steel = _largest_solid(cut)
        if steel.is_valid and float(steel.volume) > 1.0 and float(steel.volume) < float(block.volume) * 0.999:
            return steel
    except Exception:
        pass
    return block


def _face_outward_to_plus_z(shape, outward, pivot):
    spec = _rotation_to_plus_z(outward)
    if spec is None:
        return shape
    return _rotate_about(shape, pivot, spec[0], spec[1])


def _align_parting_face_z0(shape):
    from build123d import Location, Vector

    bbox = shape.bounding_box()
    return shape.move(Location(Vector(-float(bbox.center().X), -float(bbox.center().Y), -float(bbox.max.Z))))


def _moved_to_parting(profile, axis: int, z_from: float, pval: float):
    from build123d import Location, Vector

    delta = [0.0, 0.0, 0.0]
    delta[axis] = pval - z_from
    if abs(delta[axis]) < 1e-9:
        return profile
    return profile.move(Location(Vector(*delta)))


def _solid_silhouette(solid, pdir, axis: int, pval: float):
    """Stack sections through a solid onto the parting plane so one-sided
    bosses are not lost when the midplane misses them."""
    try:
        bbox = solid.bounding_box()
    except Exception:
        return None
    mn = (float(bbox.min.X), float(bbox.min.Y), float(bbox.min.Z))
    mx = (float(bbox.max.X), float(bbox.max.Y), float(bbox.max.Z))
    span = mx[axis] - mn[axis]
    if span < 0.4:
        zs = [(mn[axis] + mx[axis]) / 2.0]
    else:
        zs = [mn[axis] + span * t for t in (0.08, 0.28, 0.5, 0.72, 0.92)]
    fused = None
    from build123d import Plane

    for z in zs:
        origin = [(mn[i] + mx[i]) / 2.0 for i in range(3)]
        origin[axis] = z
        plane = Plane(origin=origin, z_dir=(float(pdir.X), float(pdir.Y), float(pdir.Z)))
        prof = _profile_of_solid(solid, plane)
        if prof is None:
            continue
        prof = _moved_to_parting(prof, axis, z, pval)
        if fused is None:
            fused = prof
        else:
            try:
                fused = fused.fuse(prof)
            except Exception:
                continue
    return fused


def _cut_or_pocket(plate, tool, bodies, pdir, axis, pval, depth, expect_removed: float):
    plate_vol = float(plate.volume)
    cut = _steel_minus_shot(plate, tool)
    try:
        removed = plate_vol - float(cut.volume)
    except Exception:
        removed = 0.0
    if expect_removed <= 1.0 or removed >= 0.25 * expect_removed:
        return cut
    profiles = []
    for body in bodies:
        try:
            solids = list(body.solids()) or [body]
        except Exception:
            solids = [body]
        for solid in solids:
            prof = _solid_silhouette(solid, pdir, axis, pval)
            if prof is not None:
                profiles.append(prof)
    return _pocket_plate_many(plate, profiles, depth)


def _build_mold_preview(
    shot,
    parting: str,
    steel: float = 32.0,
    margin: float = 16.0,
    gap: float = 28.0,
    part=None,
    runner=None,
):
    """Two die inserts opened on the back parting plane, inner faces to +Z.

    The +parting half gets the real 3D cavity (pads, studs, ring). The other
    half is a lid with runner grooves. Plates meet at the part's back face,
    not the bounding-box midplane.
    """
    from build123d import Align, Box, Compound, Location, Vector

    ref = part if part is not None else shot
    mn, mx, center_t = _bbox_xyz(ref)
    shot_mn, shot_mx, _shot_c = _bbox_xyz(shot)
    mn = tuple(min(mn[i], shot_mn[i]) for i in range(3))
    mx = tuple(max(mx[i], shot_mx[i]) for i in range(3))
    parting = _norm_parting(parting)
    pdir = Vector(*PARTING_DIRS[parting])
    axis = _parting_axis(parting)
    pval = _parting_value(*_bbox_xyz(ref)[:2], parting)
    inplane_center = list(center_t)
    inplane_center[axis] = pval
    origin = Vector(*inplane_center)
    extents = [mx[i] - mn[i] for i in range(3)]
    part_mn, part_mx, _part_c = _bbox_xyz(ref)
    cavity_h = (part_mx[axis] - pval) if parting.startswith("+") else (pval - part_mn[axis])
    thick = max(steel, abs(cavity_h) + 12.0)
    dims = [extents[0] + 2 * margin, extents[1] + 2 * margin, extents[2] + 2 * margin]
    dims[axis] = thick

    def plate(sign: float):
        loc = Location(origin + pdir * (sign * thick / 2.0))
        return loc * Box(dims[0], dims[1], dims[2], align=(Align.CENTER, Align.CENTER, Align.CENTER))

    bodies = []
    cavity_tool = part if part is not None else shot
    if part is not None:
        try:
            bodies.extend(list(part.solids()) or [part])
        except Exception:
            bodies.append(part)
    if not bodies:
        try:
            bodies.extend(list(shot.solids()) or [shot])
        except Exception:
            bodies.append(shot)

    try:
        part_vol = float(cavity_tool.volume)
    except Exception:
        part_vol = 1.0
    plus_depth = min(thick - 2.0, abs(cavity_h) + 0.6)
    plus = _cut_or_pocket(plate(1.0), cavity_tool, bodies, pdir, axis, pval, plus_depth, part_vol)
    minus = _cut_or_pocket(plate(-1.0), cavity_tool, bodies, pdir, axis, pval, -min(2.0, plus_depth), 0.0)
    if runner is not None:
        plus = _steel_minus_shot(plus, runner)
        minus = _steel_minus_shot(minus, runner)
    plus = _face_outward_to_plus_z(plus, pdir * -1.0, origin)
    minus = _face_outward_to_plus_z(minus, pdir, origin)
    plus = _align_parting_face_z0(plus)
    minus = _align_parting_face_z0(minus)
    plus_w = float(plus.bounding_box().size.X)
    minus_w = float(minus.bounding_box().size.X)
    minus = minus.move(Location(Vector(-(minus_w / 2.0 + gap / 2.0), 0, 0)))
    plus = plus.move(Location(Vector(plus_w / 2.0 + gap / 2.0, 0, 0)))
    mid_x = (float(minus.bounding_box().center().X) + float(plus.bounding_box().center().X)) / 2.0
    if abs(mid_x) > 1e-6:
        minus = minus.move(Location(Vector(-mid_x, 0, 0)))
        plus = plus.move(Location(Vector(-mid_x, 0, 0)))
    return Compound(children=[minus, plus])


def _shape_facts(shape) -> dict:
    if shape is None:
        raise RuntimeError("empty runner shape")
    bbox = shape.bounding_box()
    size = [bbox.size.X, bbox.size.Y, bbox.size.Z]
    center = [bbox.center().X, bbox.center().Y, bbox.center().Z]
    try:
        volume = float(shape.volume)
    except Exception:
        volume = 0.0
    try:
        valid = bool(shape.is_valid)
    except Exception:
        valid = True
    try:
        n_solids = len(shape.solids())
    except Exception:
        n_solids = 1
    return {
        "size_mm": [round(v, 3) for v in size],
        "center_mm": [round(v, 3) for v in center],
        "volume_mm3": round(volume, 3),
        "solid_count": n_solids,
        "is_valid": valid,
    }


def qa_runner(stem: str, brief: dict, candidate: dict, runner_shape, part_shape, mold=None) -> dict:
    checks = []
    keep = set(_as_int_list(brief.get("keepout_face_ids")))
    gates = list(candidate.get("gate_face_ids") or [])
    params = candidate.get("params") or {}
    n_eff = max(len(gates), int(params.get("n_gates") or 0))
    lo, hi = _as_count_range(brief.get("gate_count"))
    clash = [g for g in gates if g in keep]
    checks.append({
        "id": "keepout",
        "pass": not clash,
        "message": "浇口不在禁区" if not clash else f"浇口落在禁区 {clash}",
    })
    in_range = lo <= n_eff <= hi if n_eff else False
    checks.append({
        "id": "gate_count",
        "pass": bool(n_eff) and in_range,
        "message": f"浇口数 {n_eff} 在 [{lo},{hi}]" if n_eff else "没有浇口面",
    })
    rin = float(params.get("runner_in") or 0)
    rout = float(params.get("runner_out") or 0)
    mono = rin + 1e-6 >= rout > 0
    checks.append({
        "id": "section_mono",
        "pass": mono,
        "message": "截面沿程递减" if mono else "截面未递减（runner_in 应 ≥ runner_out）",
    })
    try:
        valid = bool(runner_shape.is_valid) and float(runner_shape.volume) > 1
        run_vol = float(runner_shape.volume)
    except Exception:
        valid = False
        run_vol = 0.0
    checks.append({
        "id": "watertight",
        "pass": valid,
        "message": "流道实体有效" if valid else "流道实体无效或体积过小",
    })
    try:
        part_vol = float(part_shape.volume)
    except Exception:
        part_vol = 1.0
    overlap_vol = _common_volume(runner_shape, part_shape) if runner_shape is not None else 0.0
    budget = max(float(params.get("gate_width") or 6) * max(float(params.get("gate_thick") or 2), 2.0) * 8.0 * max(n_eff, 1), 200.0)
    overlap_ok = overlap_vol <= max(budget, 0.015 * part_vol, 0.08 * max(run_vol, 1.0))
    checks.append({
        "id": "runner_clear",
        "pass": overlap_ok,
        "message": (
            "流道贴外圆、与零件仅浇口咬合"
            if overlap_ok
            else f"流道与零件重叠 {overlap_vol:.0f} mm³，可能穿过型腔而不是贴分型面"
        ),
    })
    hoop = False
    try:
        rb = runner_shape.bounding_box()
        pb = part_shape.bounding_box()
        axis = _parting_axis(_norm_parting(brief.get("parting_dir")))
        u, v = _plane_uv(axis)
        rmin = (float(rb.min.X), float(rb.min.Y), float(rb.min.Z))
        rmax = (float(rb.max.X), float(rb.max.Y), float(rb.max.Z))
        pmn = (float(pb.min.X), float(pb.min.Y), float(pb.min.Z))
        pmx = (float(pb.max.X), float(pb.max.Y), float(pb.max.Z))
        pad = 5.0
        beyond = 0
        for idx in (u, v):
            if rmin[idx] < pmn[idx] - pad:
                beyond += 1
            if rmax[idx] > pmx[idx] + pad:
                beyond += 1
        hoop = beyond >= 4
    except Exception:
        hoop = False
    checks.append({
        "id": "no_hoop",
        "pass": not hoop,
        "message": (
            "流道是单侧树，没有绕外圆成圈"
            if not hoop
            else "横浇道绕外圆成圈了，应是料饼一侧的短弧"
        ),
    })
    checks.append({
        "id": "overflow",
        "pass": True,
        "advisory": True,
        "message": "第一期不做溢流；充填末端卷气仅作提示",
    })
    try:
        part_solids = list(part_shape.solids())
    except Exception:
        part_solids = []
    if len(part_solids) > 1:
        overlap = False
        for i, a in enumerate(part_solids):
            ba = a.bounding_box()
            for b in part_solids[i + 1:]:
                bb = b.bounding_box()
                if ba.max.X < bb.min.X or bb.max.X < ba.min.X:
                    continue
                if ba.max.Y < bb.min.Y or bb.max.Y < ba.min.Y:
                    continue
                overlap = True
                break
            if overlap:
                break
        checks.append({
            "id": "eject_assembly",
            "pass": not overlap,
            "message": (
                "啮合装配不能作为一射压铸：内齿圈与行星/太阳轮占同一型腔空间，"
                "凝固后无法从两半模脱出。请只浇单个零件（例如外齿圈）。"
                if overlap
                else f"零件含 {len(part_solids)} 个实体；若只是并排一模多件，可分别顶出"
            ),
        })
    if mold is not None:
        try:
            vols = sorted(float(s.volume) for s in mold.solids())
            if len(vols) >= 2:
                rel = abs(vols[-1] - vols[0]) / max(vols[-1], 1.0)
                split_ok = rel > 0.03
                checks.append({
                    "id": "cavity_split",
                    "pass": split_ok,
                    "message": (
                        "型腔两半不对称，特征在分型一侧"
                        if split_ok
                        else "型腔两半几乎相同，分型面可能仍切在零件中腰"
                    ),
                })
        except Exception:
            pass
    hard = [c for c in checks if not c.get("advisory") and not c.get("pass")]
    return {"pass": not hard, "checks": checks, "tolerance_mm": 0.2}


def prepare_runner_build(stem: str, candidate_id: str | None = None) -> dict:
    """Validate + instantiate. Caller exports STEP/GLB."""
    data = load_runner(stem)
    brief = data.get("brief") if isinstance(data.get("brief"), dict) else default_brief(stem)
    defects = validate_runner_brief(brief, MODELS)
    if defects:
        return {"ok": False, "name": stem, "error": "runner brief 未过闸", "defects": defects}
    if not data.get("candidates"):
        proposed = cmd_runner_propose(stem)
        if not proposed.get("ok"):
            return proposed
        data = load_runner(stem)
    candidates = data.get("candidates") or []
    pick = str(candidate_id or data.get("selected") or (candidates[0]["id"] if candidates else "A"))
    candidate = next((c for c in candidates if c.get("id") == pick), None)
    if candidate is None:
        return {"ok": False, "name": stem, "error": f"没有候选 {pick}，先 easycad_runner_propose"}
    if not candidate.get("gate_face_ids"):
        return {"ok": False, "name": stem, "error": "候选没有浇口面。点选面后写入 gate_face_ids，再 propose"}

    part = _load_part_shape(stem)
    parting = _norm_parting(brief.get("parting_dir"))
    _combo, shot, runner = _build_solids(stem, candidate, part, parting)
    mold = _build_mold_preview(shot, parting, part=part, runner=runner)
    qa = qa_runner(stem, brief, candidate, runner, part, mold=mold)
    facts = _shape_facts(shot)
    data["selected"] = pick
    data["mold_preview"] = MOLD_PREVIEW
    data["built"] = {
        "candidate": pick,
        "params": candidate.get("params"),
        "gate_face_ids": candidate.get("gate_face_ids"),
        "qa": qa,
        "facts": facts,
    }
    save_runner(stem, data)
    (MODELS / f"{stem}.runner.qa.json").write_text(
        json.dumps(qa, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {
        "ok": True,
        "name": stem,
        "selected": pick,
        "candidate": candidate,
        "shot": shot,
        "runner": runner,
        "mold": mold,
        "facts": facts,
        "qa": qa,
        "mold_preview": MOLD_PREVIEW,
        "hint": "预览型腔（两半拆开、分型面朝镜头）或 shot（零件+流道）。改截面用 runner 参数或流道工作台。",
    }


def prepare_runner_mold(stem: str) -> dict:
    """Build opened-die preview from an existing shot, or full build if needed."""
    from build123d import import_step

    data = load_runner(stem)
    brief = data.get("brief") if isinstance(data.get("brief"), dict) else default_brief(stem)
    parting = _norm_parting(brief.get("parting_dir"))
    shot_path = MODELS / f"{stem}.shot.step"
    if not shot_path.is_file():
        return prepare_runner_build(stem, data.get("selected"))
    shot = import_step(str(shot_path))
    part = _load_part_shape(stem)
    runner = None
    pick = data.get("selected")
    candidate = next((c for c in (data.get("candidates") or []) if c.get("id") == pick), None)
    if candidate:
        try:
            _combo, shot, runner = _build_solids(stem, candidate, part, parting)
        except Exception:
            runner = None
    mold = _build_mold_preview(shot, parting, part=part, runner=runner)
    built = data.get("built") if isinstance(data.get("built"), dict) else {}
    qa = (
        qa_runner(stem, brief, candidate or {}, runner, part, mold=mold)
        if candidate
        else (built.get("qa") or {"pass": True, "checks": []})
    )
    if built:
        built["qa"] = qa
        data["built"] = built
    data["mold_preview"] = MOLD_PREVIEW
    save_runner(stem, data)
    (MODELS / f"{stem}.runner.qa.json").write_text(
        json.dumps(qa, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {
        "ok": True,
        "name": stem,
        "selected": data.get("selected"),
        "candidate": candidate,
        "shot": shot,
        "mold": mold,
        "facts": built.get("facts") or _shape_facts(shot),
        "qa": qa,
        "mold_preview": MOLD_PREVIEW,
        "hint": "已生成拆开型腔（两半分型面朝镜头）",
    }


def cmd_runner_qa(stem: str) -> dict:
    data = load_runner(stem)
    built = data.get("built") if isinstance(data.get("built"), dict) else {}
    qa = built.get("qa") or _read_json(MODELS / f"{stem}.runner.qa.json", {})
    if not qa:
        return {"ok": False, "name": stem, "error": "还没有 runner_build 结果"}
    return {"ok": True, "name": stem, "qa": qa, "pass": bool(qa.get("pass")), "selected": data.get("selected")}


def apply_runner_params(stem: str, values: dict) -> dict:
    data = load_runner(stem)
    candidates = data.get("candidates") or []
    pick = str(values.get("candidate") or data.get("selected") or "")
    candidate = next((c for c in candidates if c.get("id") == pick), None)
    if candidate is None:
        return {"ok": False, "name": stem, "error": "没有已选候选，先 propose/build"}
    params = dict(candidate.get("params") or {})
    for key in ("gate_area", "gate_width", "gate_thick", "runner_in", "runner_out", "biscuit_d"):
        if key in values and values[key] not in (None, ""):
            params[key] = float(values[key])
    if float(params.get("runner_in") or 0) < float(params.get("runner_out") or 0):
        return {"ok": False, "name": stem, "error": "runner_in 必须 ≥ runner_out"}
    candidate["params"] = params
    for i, row in enumerate(candidates):
        if row.get("id") == pick:
            candidates[i] = candidate
    data["candidates"] = candidates
    data["selected"] = pick
    save_runner(stem, data)
    return prepare_runner_build(stem, pick)


def is_runner_param_payload(values: dict) -> bool:
    keys = {"gate_area", "gate_width", "gate_thick", "runner_in", "runner_out", "biscuit_d", "candidate"}
    return any(k in values for k in keys)
