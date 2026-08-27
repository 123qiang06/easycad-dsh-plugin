"""EasyCAD-owned CAD CLI. Does not import text-to-cad / MAC / dsh source.

Commands:
  write-gen / gen / inspect / qa / export / brief / measure / preview / params / apply
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
import traceback
from pathlib import Path
from urllib.parse import quote

from ir import brief_view, build_ir
from params import extract_params, load_ir, update_ir, update_source, validate_parameterized_source
from vision import best_iou, cad_silhouettes, cad_view_image, foreground_mask, grade, reference_grid

ROOT = Path(__file__).resolve().parents[2]
MODELS = ROOT / "models"
PREVIEW_URL = "http://127.0.0.1:3246"


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _file_url(path: Path) -> str:
    resolved = path.resolve().as_posix()
    if len(resolved) > 1 and resolved[1] == ":":
        return "file:///" + quote(resolved, safe="/:")
    return "file://" + quote(resolved, safe="/")


def _models_stem(stem_or_path: str) -> str:
    text = str(stem_or_path).strip().replace("\\", "/")
    parts = Path(text).parts
    if ".." in parts or text.startswith("/") or (len(text) > 1 and text[1] == ":"):
        raise ValueError(f"scripts must live under models/: {stem_or_path!r}")
    parent = Path(text).parent.as_posix()
    if parent not in {".", "", "models"}:
        raise ValueError(f"scripts must live under models/: {stem_or_path!r}")
    name = Path(text).name
    if name.endswith(".step.py"):
        stem = name[: -len(".step.py")]
    elif name.endswith(".brief.json"):
        stem = name[: -len(".brief.json")]
    elif name.endswith(".py"):
        stem = name[: -len(".py")]
    else:
        stem = name
    if not stem or stem in {".", ".."} or any(ch in stem for ch in r'\/:*?"<>|'):
        raise ValueError(f"invalid part name: {stem_or_path!r}")
    return stem


def _models_script(stem_or_path: str) -> Path:
    stem = _models_stem(stem_or_path)
    script = (MODELS / f"{stem}.step.py").resolve()
    try:
        script.relative_to(MODELS.resolve())
    except ValueError as exc:
        raise ValueError("scripts must live under models/") from exc
    return script


def _load_gen_step(script: Path):
    spec = importlib.util.spec_from_file_location(f"easycad_{script.stem}", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {script}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        tb = "".join(traceback.format_exception(exc)).strip().splitlines()
        raise RuntimeError(
            f"user script failed: {type(exc).__name__}: {exc}\n" + "\n".join(tb[-12:])
        ) from exc
    fn = getattr(module, "gen_step", None)
    if fn is None:
        raise RuntimeError(f"{_rel(script)} must define gen_step()")
    try:
        return fn()
    except Exception as exc:
        tb = "".join(traceback.format_exception(exc)).strip().splitlines()
        raise RuntimeError(
            f"gen_step() failed: {type(exc).__name__}: {exc}\n" + "\n".join(tb[-12:])
        ) from exc


def _solid_count(shape) -> int:
    try:
        return len(shape.solids())
    except Exception:
        return 0


def _shape_facts(shape) -> dict:
    if shape is None:
        raise RuntimeError("gen_step() returned None")
    if getattr(shape, "is_null", False):
        raise RuntimeError("gen_step() returned an empty shape")

    valid = True
    if hasattr(shape, "is_valid"):
        valid = bool(shape.is_valid)

    bbox = shape.bounding_box()
    size = [
        round(float(bbox.max.X - bbox.min.X), 4),
        round(float(bbox.max.Y - bbox.min.Y), 4),
        round(float(bbox.max.Z - bbox.min.Z), 4),
    ]
    facts: dict = {
        "size_mm": size,
        "center_mm": [
            round(float((bbox.min.X + bbox.max.X) / 2), 4),
            round(float((bbox.min.Y + bbox.max.Y) / 2), 4),
            round(float((bbox.min.Z + bbox.max.Z) / 2), 4),
        ],
        "solid_count": _solid_count(shape),
        "is_valid": valid,
    }
    try:
        volume = round(float(shape.volume), 4)
    except Exception:
        volume = None
    if volume is not None:
        facts["volume_mm3"] = volume
    return facts


def _build_qa(facts: dict, expect_size: list[float] | None, tol: float) -> dict:
    checks: list[dict] = []
    if expect_size:
        if len(expect_size) != 3:
            raise ValueError("expect-size must be three numbers: X,Y,Z in mm")
        got = facts["size_mm"]
        mismatches = []
        for axis, actual, expected in zip("XYZ", got, expect_size, strict=True):
            delta = abs(actual - expected)
            if delta > tol:
                mismatches.append(
                    {
                        "axis": axis,
                        "got_mm": actual,
                        "expected_mm": expected,
                        "delta_mm": round(delta, 4),
                    }
                )
        checks.append(
            {
                "id": "overall_dimension",
                "pass": len(mismatches) == 0,
                "expected_mm": expect_size,
                "got_mm": got,
                "tolerance_mm": tol,
                "mismatches": mismatches,
            }
        )

    solid_count = int(facts.get("solid_count") or 0)
    checks.append(
        {
            "id": "single_body",
            "pass": solid_count == 1,
            "solid_count": solid_count,
        }
    )

    valid = bool(facts.get("is_valid", True))
    volume = facts.get("volume_mm3")
    watertight = valid and (volume is None or float(volume) > 0)
    checks.append(
        {
            "id": "watertight",
            "pass": watertight,
            "is_valid": valid,
            "volume_mm3": volume,
        }
    )

    return {
        "pass": all(item["pass"] for item in checks),
        "tolerance_mm": tol,
        "checks": checks,
    }


def _parse_size(text: str | None) -> list[float] | None:
    if not text:
        return None
    parts = [p.strip() for p in text.replace("x", ",").replace("X", ",").split(",") if p.strip()]
    if len(parts) != 3:
        raise ValueError("expect-size must look like 80,10,80")
    return [float(p) for p in parts]


def _result_for_shape(
    script: Path,
    shape,
    expect_size: list[float] | None,
    tol: float,
    *,
    export_step_file: bool,
    ref_image: str | None = None,
) -> dict:
    stem = script.name[: -len(".step.py")] if script.name.endswith(".step.py") else script.stem
    facts = _shape_facts(shape)
    result: dict = {
        "ok": True,
        "name": stem,
        "script": _rel(script),
        "facts": facts,
    }
    if export_step_file:
        from build123d import export_step

        step_path = script.with_name(f"{stem}.step")
        export_step(shape, str(step_path))
        result["step"] = _rel(step_path)
        glb_path = script.with_name(f"{stem}.glb")
        try:
            if _export_glb(shape, glb_path):
                result["glb"] = _rel(glb_path)
        except Exception as exc:
            result["glb_error"] = str(exc)
    qa = _build_qa(facts, expect_size, tol)
    result["qa"] = qa
    result["ok"] = qa["pass"]
    # Persist QA per part so the preview Issues section can read it without regenerate.
    try:
        (MODELS / f"{stem}.qa.json").write_text(
            json.dumps({"name": stem, "size_mm": facts["size_mm"], **qa}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except Exception:
        pass
    # Persist per-face topology so the viewer can pick whole faces (planar + curved).
    try:
        _write_topology_sidecar(shape, stem)
    except Exception:
        pass
    ir_path = MODELS / f"{stem}.ir.json"
    if ir_path.is_file():
        result["ir"] = _rel(ir_path)
    # CAD vs reference-image similarity (advisory, never fails the part on its own).
    sim = _similarity_sidecars(stem, shape, ref_image=ref_image, write_views=True)
    result["similarity"] = sim
    if sim.get("present"):
        qa["checks"].append(
            {
                "id": "similarity",
                "pass": sim.get("pass", False),
                "advisory": True,
                "score": sim.get("score"),
                "grade": sim.get("grade"),
                "best_view": sim.get("best_view"),
                "ref_image": sim.get("ref_image"),
            }
        )
    # Rule-based AI advice from QA + similarity + IR (text prompt intent).
    result["advice"] = _advice_for(stem, persist=True)
    return result


def _publish_latest(result: dict) -> None:
    name = result.get("name")
    if not name:
        return
    stem = str(name)
    artifacts: dict[str, str] = {}
    for key, suffix in (
        ("script", ".step.py"),
        ("step", ".step"),
        ("glb", ".glb"),
        ("stl", ".stl"),
        ("ir", ".ir.json"),
        ("brief", ".brief.json"),
    ):
        path = MODELS / f"{stem}{suffix}"
        if path.is_file():
            artifacts[key] = _rel(path)
    exports = result.get("exports")
    if isinstance(exports, dict):
        artifacts.update({k: str(v) for k, v in exports.items() if v})
    payload = {
        "name": stem,
        "updatedAt": int(time.time() * 1000),
        "ok": bool(result.get("ok")),
        "qa": result.get("qa"),
        "facts": result.get("facts"),
        "artifacts": artifacts,
        "view": f"/easycad/view?name={stem}&embed=1",
    }
    MODELS.mkdir(parents=True, exist_ok=True)
    (MODELS / ".easycad-latest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    result["dock"] = payload["view"]


def cmd_write_gen(name: str, source: str, expect_size: list[float] | None, tol: float, ref_image: str | None = None) -> dict:
    MODELS.mkdir(parents=True, exist_ok=True)
    script = _models_script(name)
    text = source.replace("\r\n", "\n")
    if "def gen_step" not in text:
        raise ValueError("source must define def gen_step()")
    # Hard gate: every generated part must be parameterized so it stays editable
    # in the in-pane editor and via easycad_apply.
    ok, issues = validate_parameterized_source(text)
    if not ok:
        raise ValueError("源码未参数化，拒绝生成：\n- " + "\n- ".join(issues))
    script.write_text(text, encoding="utf-8")
    return cmd_gen(script, expect_size=expect_size, tol=tol, ref_image=ref_image)


def cmd_gen(script: Path, expect_size: list[float] | None = None, tol: float = 0.2, ref_image: str | None = None) -> dict:
    script = script.resolve()
    if not script.is_file():
        raise FileNotFoundError(_rel(script))
    return _result_for_shape(script, _load_gen_step(script), expect_size, tol, export_step_file=True, ref_image=ref_image)


def cmd_inspect(script: Path, expect_size: list[float] | None = None, tol: float = 0.2, ref_image: str | None = None) -> dict:
    script = script.resolve()
    if not script.is_file():
        raise FileNotFoundError(_rel(script))
    return _result_for_shape(script, _load_gen_step(script), expect_size, tol, export_step_file=False, ref_image=ref_image)


def cmd_qa(script: Path, expect_size: list[float] | None = None, tol: float = 0.2, ref_image: str | None = None) -> dict:
    return cmd_inspect(script, expect_size=expect_size, tol=tol, ref_image=ref_image)


def cmd_export(script: Path, formats: list[str]) -> dict:
    script = script.resolve()
    if not script.is_file():
        raise FileNotFoundError(_rel(script))
    wanted = [item.lower().lstrip(".") for item in formats]
    allowed = {"stl", "glb"}
    unknown = [item for item in wanted if item not in allowed]
    if unknown:
        raise ValueError(f"unsupported format: {unknown}; use stl or glb")
    if not wanted:
        wanted = ["stl"]

    from build123d import export_stl

    shape = _load_gen_step(script)
    stem = script.name[: -len(".step.py")] if script.name.endswith(".step.py") else script.stem
    exports: dict[str, str] = {}
    for fmt in wanted:
        if fmt == "stl":
            path = script.with_name(f"{stem}.stl")
            if not export_stl(shape, str(path)):
                raise RuntimeError(f"export_stl failed for {stem}")
            exports["stl"] = _rel(path)
        elif fmt == "glb":
            path = script.with_name(f"{stem}.glb")
            if not _export_glb(shape, path):
                raise RuntimeError(f"export_glb failed for {stem}")
            exports["glb"] = _rel(path)
    return {"ok": True, "name": stem, "script": _rel(script), "exports": exports}


def cmd_brief(name: str, payload: dict) -> dict:
    MODELS.mkdir(parents=True, exist_ok=True)
    stem = _models_stem(name)
    ir = build_ir(stem, payload)
    ir_path = MODELS / f"{stem}.ir.json"
    ir_path.write_text(json.dumps(ir, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    brief = brief_view(ir)
    brief_path = MODELS / f"{stem}.brief.json"
    brief_path.write_text(json.dumps(brief, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "ok": True,
        "name": stem,
        "brief": brief,
        "ir": ir,
        "path": _rel(brief_path),
        "ir_path": _rel(ir_path),
    }


def cmd_measure(script: Path, checks: list[dict], tol: float) -> dict:
    script = script.resolve()
    if not script.is_file():
        raise FileNotFoundError(_rel(script))
    if not checks:
        raise ValueError("measure requires at least one check")
    facts = _shape_facts(_load_gen_step(script))
    size = facts["size_mm"]
    axis_index = {"X": 0, "Y": 1, "Z": 2}
    results = []
    for raw in checks:
        item = dict(raw)
        check_id = str(item.get("id") or item.get("axis") or "check")
        axis = str(item.get("axis") or "").upper()
        expected = item.get("expected_mm")
        check_tol = float(item.get("tol_mm") if item.get("tol_mm") is not None else tol)
        if axis not in axis_index:
            raise ValueError(f"measure axis must be X, Y, or Z (got {axis!r} on {check_id})")
        if expected is None:
            raise ValueError(f"check {check_id} needs expected_mm")
        got = size[axis_index[axis]]
        delta = abs(got - float(expected))
        results.append(
            {
                "id": check_id,
                "axis": axis,
                "got_mm": got,
                "expected_mm": float(expected),
                "delta_mm": round(delta, 4),
                "tolerance_mm": check_tol,
                "pass": delta <= check_tol,
            }
        )
    return {
        "ok": all(item["pass"] for item in results),
        "name": _models_stem(script.name),
        "script": _rel(script),
        "facts": facts,
        "measurements": results,
    }


def cmd_params(stem_or_path: str) -> dict:
    stem = _models_stem(stem_or_path)
    ir = load_ir(MODELS / f"{stem}.ir.json")
    script = _models_script(stem)
    source = script.read_text(encoding="utf-8") if script.is_file() else ""
    extracted = extract_params(ir, source)
    return {"ok": True, "name": stem, **extracted}


def cmd_apply(stem_or_path: str, values: dict) -> dict:
    stem = _models_stem(stem_or_path)
    script = _models_script(stem)
    ir_path = MODELS / f"{stem}.ir.json"
    ir = load_ir(ir_path)
    source = script.read_text(encoding="utf-8") if script.is_file() else ""
    extracted = extract_params(ir, source)
    merged = dict(extracted["values"])
    for key in ("length", "width", "height", "hole_d"):
        if key in values and values[key] is not None and values[key] != "":
            merged[key] = float(values[key])
    for key in ("length", "width", "height"):
        if float(merged.get(key) or 0) <= 0:
            raise ValueError(f"{key} must be > 0")
    hole = merged.get("hole_d")
    if hole:
        if hole <= 0:
            raise ValueError("hole_d must be > 0")
        if hole >= min(merged["length"], merged["width"]):
            raise ValueError("中心孔直径必须小于长度和宽度")
    MODELS.mkdir(parents=True, exist_ok=True)
    script.write_text(update_source(source, merged), encoding="utf-8")
    next_ir = update_ir(ir, stem, merged)
    ir_path.write_text(json.dumps(next_ir, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    expect = [merged["length"], merged["width"], merged["height"]]
    result = cmd_write_gen(stem, script.read_text(encoding="utf-8"), expect, 0.2)
    result["params"] = extract_params(next_ir, script.read_text(encoding="utf-8"))
    return result


def cmd_worker() -> int:
    """Persistent build123d worker: warm the heavy OCP import once, then serve
    one-shot gen/apply jobs over newline-delimited JSON on stdin/stdout. Makes
    successive CAD operations seconds instead of a ~14s cold import each time.
    Each job re-reads this module (importlib.reload), so editing the command
    logic needs no worker/dsh restart; build123d stays warm in sys.modules."""
    import build123d  # noqa: F401  (warm the import so later jobs reuse it)
    import importlib
    try:
        mod = importlib.import_module('cad_cli')  # this file as a reloadable module
    except Exception:
        mod = None

    def fc(name: str, *args, **kwargs):
        fn = getattr(mod, name) if mod is not None else globals()[name]
        return fn(*args, **kwargs)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception as exc:
            print(json.dumps({"ok": False, "error": f"bad request: {exc}"}, ensure_ascii=False))
            sys.stdout.flush()
            continue
        cmd = req.get("cmd")
        if cmd == "shutdown":
            break
        try:
            if mod is not None:
                importlib.reload(mod)  # pick up edits to cad_cli.py before dispatch
            if cmd == "gen":
                source = req.get("source", "")
                name = str(req.get("name", "part"))
                expect = req.get("expect_size")
                tol = float(req.get("tol", 0.2))
                if not source.strip():
                    raise ValueError("empty source")
                result = fc("cmd_write_gen", name, source, expect, tol)
            elif cmd == "apply":
                name = str(req.get("name", ""))
                params = req.get("params") or {}
                if not name:
                    raise ValueError("apply needs a name")
                result = fc("cmd_apply", name, params)
            elif cmd == "import":
                name = str(req.get("name", ""))
                step = str(req.get("step", ""))
                if not name or not step:
                    raise ValueError("import needs name and step")
                result = fc("cmd_import_step", name, Path(step))
            elif cmd == "preview":
                name = str(req.get("name", ""))
                if not name:
                    raise ValueError("preview needs a name")
                result = fc("cmd_preview", name, ensure_glb=True)
            elif cmd == "similarity":
                name = str(req.get("name", ""))
                ref = str(req.get("ref_image") or "")
                if not name:
                    raise ValueError("similarity needs a name")
                result = fc("cmd_similarity", name, ref or None)
            elif cmd == "snapshot":
                name = str(req.get("name", ""))
                views = req.get("views") or []
                if not name:
                    raise ValueError("snapshot needs a name")
                result = fc("cmd_snapshot", name, views)
            elif cmd == "advice":
                name = str(req.get("name", ""))
                if not name:
                    raise ValueError("advice needs a name")
                result = fc("cmd_advice", name)
            else:
                raise ValueError(f"unknown worker cmd: {cmd!r}")
            if result.get("name"):
                fc("_publish_latest", result)
            print(json.dumps(result, ensure_ascii=False))
            sys.stdout.flush()
        except Exception as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
            sys.stdout.flush()
    return 0


def cmd_import_step(stem: str, step_path: Path) -> dict:
    """Import a standalone STEP file and publish a GLB preview for it."""
    import shutil

    from build123d import import_step

    MODELS.mkdir(parents=True, exist_ok=True)
    shape = import_step(str(step_path))
    facts = _shape_facts(shape)
    step_out = MODELS / f"{stem}.step"
    if step_path.resolve() != step_out.resolve():
        shutil.copyfile(step_path, step_out)
    glb_out = MODELS / f"{stem}.glb"
    glb_err = None
    try:
        _export_glb(shape, glb_out)
        _write_topology_sidecar(shape, stem)
    except Exception as exc:
        glb_err = str(exc)
    result = {
        "ok": True,
        "name": stem,
        "script": _rel(step_out),
        "step": _rel(step_out),
        "facts": facts,
        "glb": _rel(glb_out) if glb_err is None else None,
        "glb_error": glb_err,
        "imported": True,
    }
    return result


def _glb_bad(glb: Path) -> bool:
    """A viewable GLB is a few KB; an empty/tiny file is a broken export."""
    if not glb.is_file():
        return True
    try:
        return glb.stat().st_size < 1024
    except OSError:
        return True


def _export_glb_trimesh(shape, path: Path, tol: float = 0.001, ang: float = 0.1) -> bool:
    """Write a GLB via trimesh: one separate mesh per B-rep face so the in-page
    viewer can still pick/cluster individual faces. Reliable for shapes that the
    OCCT glTF writer cannot serialize (e.g. STEP-imported compounds)."""
    import numpy as np
    import trimesh

    meshes = []
    for face in shape.faces():
        try:
            fv, ft = face.tessellate(tol, ang)
        except Exception:
            continue
        if not ft:
            continue
        verts = np.array([[v.X, v.Y, v.Z] for v in fv], dtype=np.float64)
        faces = np.array(ft, dtype=np.int64)
        meshes.append(trimesh.Trimesh(vertices=verts, faces=faces, process=False))
    if not meshes:
        raise RuntimeError("tessellation produced no renderable faces")
    scene = trimesh.Scene({f"face_{i}": m for i, m in enumerate(meshes)})
    scene.export(str(path), file_type="glb")
    return True


def _export_glb(shape, path: Path, tol: float = 0.001, ang: float = 0.1) -> bool:
    """Export a GLB the viewer can render. One mesh PER B-rep FACE, so the in-page
    viewer picks/maps a whole face (planar AND curved) by its mesh — not a sliver.
    Falls back to build123d's OCCT writer only if trimesh fails on this shape."""
    try:
        return _export_glb_trimesh(shape, path, tol, ang)
    except Exception:
        from build123d import export_gltf
        return bool(export_gltf(shape, str(path), binary=True))


def _write_topology_sidecar(shape, stem: str) -> None:
    """Persist per-B-rep-face topology metadata next to the GLB. The face id
    matches the per-face mesh order in the GLB, so the viewer shows each face's
    type / real area / normal / centroid."""
    import json as _json

    def _vec(v):
        return [float(v.X), float(v.Y), float(v.Z)]

    faces = []
    ranges = []
    cursor = 0
    for i, face in enumerate(shape.faces()):
        try:
            geom = face.geom_type
            ftype = geom.name if hasattr(geom, "name") else (type(geom).__name__ if geom is not None else "Unknown")
        except Exception:
            ftype = "Unknown"
        try:
            nrm = _vec(face.normal_at(face.center()))
        except Exception:
            nrm = [0.0, 0.0, 0.0]
        try:
            ctr = _vec(face.center())
        except Exception:
            ctr = [0.0, 0.0, 0.0]
        try:
            area = float(face.area)
        except Exception:
            area = 0.0
        # Number of tessellated triangles for THIS face => the [start,count) range of
        # its triangles in the per-face GLB (faces are emitted in shape.faces() order).
        try:
            _, ft = face.tessellate(0.001, 0.1)
            count = len(ft) if ft else 0
        except Exception:
            count = 0
        ranges.append({"id": i, "start": cursor, "count": count})
        cursor += count
        faces.append({"id": i, "type": ftype, "normal": nrm, "area": round(area, 4), "centroid": [round(x, 4) for x in ctr]})
    (MODELS / f"{stem}.topology.json").write_text(
        _json.dumps({"name": stem, "faces": faces, "ranges": ranges, "triangleCount": cursor}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )



def _load_sidecar(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _shape_triangles(shape, tol: float = 0.001, ang: float = 0.1):
    """Collect every tessellated triangle (vertices + index triples) of a shape."""
    import numpy as np

    verts: list[tuple[float, float, float]] = []
    tris: list[tuple[int, int, int]] = []
    vcount = 0
    for face in shape.faces():
        try:
            fv, ft = face.tessellate(tol, ang)
        except Exception:
            continue
        if not fv or not ft:
            continue
        base = vcount
        for vtx in fv:
            verts.append((float(vtx.X), float(vtx.Y), float(vtx.Z)))
        for tri in ft:
            tris.append((base + int(tri[0]), base + int(tri[1]), base + int(tri[2])))
        vcount += len(fv)
    if not verts:
        raise RuntimeError("tessellation produced no vertices")
    return np.array(verts, dtype=np.float64), np.array(tris, dtype=np.int64)


def _ref_path_for(stem: str, ref_image: str | None) -> Path | None:
    if ref_image:
        candidate = Path(ref_image)
        if candidate.is_file():
            return candidate.resolve()
        # A bare stem/path may be under models/ already.
        if not candidate.is_absolute():
            local = MODELS / candidate
            if local.is_file():
                return local.resolve()
        return None
    auto = MODELS / f"{stem}.ref.png"
    return auto if auto.is_file() else None


def _similarity_sidecars(stem: str, shape, ref_image: str | None = None, write_views: bool = True) -> dict:
    """Compute CAD-vs-reference silhouette IoU, persist <stem>.similarity.json
    and (optionally) per-view PNGs. Returns the similarity dictionary."""
    try:
        verts, tris = _shape_triangles(shape)
    except Exception as exc:
        sim = {"name": stem, "present": False, "error": f"render failed: {exc}"}
        (MODELS / f"{stem}.similarity.json").write_text(
            json.dumps(sim, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return sim

    views = {"front", "right", "top", "iso"}
    if write_views:
        for view in sorted(views):
            try:
                img = cad_view_image(verts, tris, view)
                img.save(MODELS / f"{stem}.view_{view}.png")
            except Exception:
                pass

    ref_path = _ref_path_for(stem, ref_image)
    if ref_path is None:
        sim = {"name": stem, "present": False, "reason": "no reference image"}
        (MODELS / f"{stem}.similarity.json").write_text(
            json.dumps(sim, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return sim

    try:
        mask, source, note = foreground_mask(str(ref_path))
    except Exception as exc:
        sim = {"name": stem, "present": True, "ref_image": _rel(ref_path), "error": f"mask failed: {exc}"}
        (MODELS / f"{stem}.similarity.json").write_text(
            json.dumps(sim, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return sim

    ref_grid = reference_grid(mask)
    cad = cad_silhouettes(verts, tris)
    per_view = {}
    best_view, best_score, best_variant = "front", 0.0, "identity"
    for name, grid in cad.items():
        score, variant = best_iou(ref_grid, grid)
        per_view[name] = round(score, 4)
        if score > best_score:
            best_score, best_view, best_variant = score, name, variant
    label, passed = grade(best_score)
    sim = {
        "name": stem,
        "present": True,
        "ref_image": _rel(ref_path),
        "mask_source": source,
        "note": note,
        "score": round(best_score, 4),
        "grade": label,
        "pass": passed,
        "best_view": best_view,
        "best_variant": best_variant,
        "views": per_view,
        "threshold": 0.5,
    }
    (MODELS / f"{stem}.similarity.json").write_text(
        json.dumps(sim, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return sim


def _advice_for(stem: str, *, persist: bool = True) -> dict:
    """Rule-based AI advice from QA + similarity + IR (text prompt intent)."""
    qa = _load_sidecar(MODELS / f"{stem}.qa.json")
    sim = _load_sidecar(MODELS / f"{stem}.similarity.json")
    ir = _load_sidecar(MODELS / f"{stem}.ir.json")
    intent = str(ir.get("intent") or "")
    features = [
        str(item.get("text") or item.get("id") or "")
        for item in (ir.get("features") or [])
        if isinstance(item, dict)
    ]
    script = _models_script(stem)
    src = script.read_text(encoding="utf-8") if script.is_file() else ""
    extracted = extract_params(ir, src) if script.is_file() else {}
    vals = extracted.get("values") or {}

    suggestions: list[str] = []
    checks = qa.get("checks") if isinstance(qa.get("checks"), list) else []
    failed = [c for c in checks if c.get("pass") is False]
    for c in checks:
        cid = c.get("id")
        if c.get("pass") is False:
            if cid == "overall_dimension":
                for m in c.get("mismatches") or []:
                    suggestions.append(
                        f"{m.get('axis')} 轴尺寸 {m.get('got_mm')} mm 与期望 {m.get('expected_mm')} mm 相差 "
                        f"{m.get('delta_mm')} mm：调整 PARAMS 中的对应外形键。"
                    )
            elif cid == "single_body":
                suggestions.append(f"模型由 {c.get('solid_count')} 个实体组成（应为一个）：合并为单一 BuildPart 或做布尔并集。")
            elif cid == "watertight":
                suggestions.append("模型非水密/体积异常：检查是否有开面、自交或空实体。")
    if not failed:
        suggestions.append("几何校验全部通过：外形尺寸、单实体、水密均已达标。")

    if sim.get("present"):
        score = float(sim.get("score") or 0)
        view = sim.get("best_view")
        if score >= 0.80:
            suggestions.append(f"外形与参考图贴合度很高（{score:.2f}），可进入导出/打印。")
        elif score >= 0.50:
            suggestions.append(f"外形与参考图贴合度{sim.get('grade', '中等')}（{score:.2f}，最佳 {view} 视图）：建议对照 {view} 视图微调轮廓。")
        else:
            suggestions.append(f"外形与参考图贴合度偏低（{score:.2f}）：重点比对 {view} 视图的轮廓形状、孔位与倒角。")
        if str(sim.get("note", "")).startswith("fragmented"):
            suggestions.append("参考图是工程图/多视图，轮廓被碎片化，相似度仅供参考；建议改用单一视图或零件照片作为参考。")
    else:
        suggestions.append("未提供参考图，跳过相似度检测；如提供可存到 models/<名字>.ref.png 再比对。")

    if intent:
        suggestions.append(f"设计意图：{intent}。")
    if features:
        suggestions.append("需求特征：" + "；".join(features) + "。")

    hole_needed = any(("hole" in f.lower() or "孔" in f or "孔径" in f) for f in features)
    if hole_needed and not vals.get("hole_d"):
        suggestions.append("需求提到孔/孔径，但参数没有 hole_d：加一个中央通孔或补上孔径。")
    if vals.get("length") and vals.get("width") and vals.get("height"):
        suggestions.append(
            "当前可用参数：length / width / height"
            + ("" if vals.get("hole_d") else "（缺 hole_d）")
            + "；在分屏参数行可直接修改。"
        )

    advice = {
        "name": stem,
        "mode": "rule-based",
        "suggestions": suggestions,
        "summary": (
            "GEOMETRY OK" if not failed and sim.get("pass") is not False else "NEEDS WORK"
        ),
    }
    if persist:
        (MODELS / f"{stem}.advice.json").write_text(
            json.dumps(advice, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return advice


def cmd_snapshot(stem_or_path: str, views: list[str] | None = None) -> dict:
    stem = _models_stem(stem_or_path)
    shape = _load_gen_step(_models_script(stem))
    verts, tris = _shape_triangles(shape)
    wanted = views or ["iso", "front", "right", "top"]
    images = []
    for view in wanted:
        if view not in {"iso", "front", "right", "top"}:
            raise ValueError(f"bad view: {view!r}")
        try:
            img = cad_view_image(verts, tris, view)
            path = MODELS / f"{stem}.view_{view}.png"
            img.save(path)
            images.append(_rel(path))
        except Exception as exc:
            raise RuntimeError(f"render {view} failed: {exc}") from exc
    return {"ok": True, "name": stem, "images": images}


def cmd_similarity(stem_or_path: str, ref_image: str | None = None) -> dict:
    stem = _models_stem(stem_or_path)
    shape = _load_gen_step(_models_script(stem))
    sim = _similarity_sidecars(stem, shape, ref_image=ref_image, write_views=True)
    return {"ok": True, "name": stem, "similarity": sim}


def cmd_advice(stem_or_path: str) -> dict:
    stem = _models_stem(stem_or_path)
    advice = _advice_for(stem, persist=True)
    return {"ok": True, "name": stem, "advice": advice}


def cmd_preview(stem_or_path: str, ensure_glb: bool = True) -> dict:
    stem = _models_stem(stem_or_path)
    script = _models_script(stem)
    step = MODELS / f"{stem}.step"
    glb = MODELS / f"{stem}.glb"
    # Rebuild a missing or broken GLB so the viewer always has something to show:
    # prefer the parametric script (gen), else import the bare STEP (no script).
    if ensure_glb and _glb_bad(glb):
        from build123d import import_step

        if script.is_file():
            cmd_export(script, ["glb"])
        elif step.is_file():
            if not _export_glb(import_step(str(step)), glb):
                raise RuntimeError(f"export_glb failed for {stem}")
    files = {
        "script": script if script.is_file() else None,
        "step": MODELS / f"{stem}.step",
        "stl": MODELS / f"{stem}.stl",
        "glb": glb,
        "ir": MODELS / f"{stem}.ir.json",
    }
    artifacts = {
        key: _rel(path)
        for key, path in files.items()
        if path is not None and path.is_file()
    }
    if not artifacts:
        raise FileNotFoundError(f"no artifacts for {stem} under models/")
    open_path = files["step"] if files["step"].is_file() else next(
        p for p in files.values() if p is not None and p.is_file()
    )
    viewer = f"{PREVIEW_URL}/?name={stem}"
    return {
        "ok": True,
        "name": stem,
        "artifacts": artifacts,
        "open": viewer,
        "file": _file_url(open_path),
        "hint": "dsh 右侧 CAD 分屏会自动打开。独立预览页：" + viewer,
    }


def _read_json_arg(text: str, stdin: bool) -> object:
    raw = sys.stdin.read() if stdin else text
    if not str(raw).strip():
        raise ValueError("expected JSON payload")
    return json.loads(raw)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="easycad-cad")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_write = sub.add_parser("write-gen")
    p_write.add_argument("--name", required=True)
    p_write.add_argument("--source", default="")
    p_write.add_argument("--source-stdin", action="store_true")
    p_write.add_argument("--expect-size", default="")
    p_write.add_argument("--tol", type=float, default=0.2)
    p_write.add_argument("--ref", default="")

    p_gen = sub.add_parser("gen")
    p_gen.add_argument("script")
    p_gen.add_argument("--expect-size", default="")
    p_gen.add_argument("--tol", type=float, default=0.2)
    p_gen.add_argument("--ref", default="")

    p_ins = sub.add_parser("inspect")
    p_ins.add_argument("script")
    p_ins.add_argument("--expect-size", default="")
    p_ins.add_argument("--tol", type=float, default=0.2)
    p_ins.add_argument("--ref", default="")

    p_qa = sub.add_parser("qa")
    p_qa.add_argument("script")
    p_qa.add_argument("--expect-size", default="")
    p_qa.add_argument("--tol", type=float, default=0.2)
    p_qa.add_argument("--ref", default="")

    p_exp = sub.add_parser("export")
    p_exp.add_argument("script")
    p_exp.add_argument("--format", action="append", default=[])

    p_brief = sub.add_parser("brief")
    p_brief.add_argument("--name", required=True)
    p_brief.add_argument("--json", default="")
    p_brief.add_argument("--json-stdin", action="store_true")

    p_meas = sub.add_parser("measure")
    p_meas.add_argument("script")
    p_meas.add_argument("--json", default="")
    p_meas.add_argument("--json-stdin", action="store_true")
    p_meas.add_argument("--tol", type=float, default=0.2)

    p_prev = sub.add_parser("preview")
    p_prev.add_argument("script")

    p_params = sub.add_parser("params")
    p_params.add_argument("script")

    p_apply = sub.add_parser("apply")
    p_apply.add_argument("--name", default="")
    p_apply.add_argument("script", nargs="?")
    p_apply.add_argument("--json", default="")
    p_apply.add_argument("--json-stdin", action="store_true")

    p_worker = sub.add_parser("worker")

    p_import = sub.add_parser("import")
    p_import.add_argument("--name", default="")
    p_import.add_argument("step")

    p_snap = sub.add_parser("snapshot")
    p_snap.add_argument("script")
    p_snap.add_argument("--view", action="append", default=[])

    p_sim = sub.add_parser("similarity")
    p_sim.add_argument("script")
    p_sim.add_argument("--ref", default="")

    p_adv = sub.add_parser("advice")
    p_adv.add_argument("script")

    args = parser.parse_args(argv)
    if args.cmd == "worker":
        return cmd_worker()
    try:
        expect = _parse_size(getattr(args, "expect_size", "") or "")
        tol = float(getattr(args, "tol", 0.2))
        if args.cmd == "write-gen":
            source = sys.stdin.read() if args.source_stdin else args.source
            if not source.strip():
                raise ValueError("empty source")
            result = cmd_write_gen(args.name, source, expect, tol, args.ref or None)
        elif args.cmd == "gen":
            result = cmd_gen(_models_script(args.script), expect, tol, args.ref or None)
        elif args.cmd == "inspect":
            result = cmd_inspect(_models_script(args.script), expect, tol, args.ref or None)
        elif args.cmd == "qa":
            result = cmd_qa(_models_script(args.script), expect, tol, args.ref or None)
        elif args.cmd == "export":
            result = cmd_export(_models_script(args.script), args.format)
        elif args.cmd == "brief":
            payload = _read_json_arg(args.json, args.json_stdin)
            if not isinstance(payload, dict):
                raise ValueError("brief JSON must be an object")
            result = cmd_brief(args.name, payload)
        elif args.cmd == "measure":
            payload = _read_json_arg(args.json, args.json_stdin)
            if not isinstance(payload, list):
                raise ValueError("measure JSON must be an array of checks")
            result = cmd_measure(_models_script(args.script), payload, tol)
        elif args.cmd == "params":
            result = cmd_params(args.script)
        elif args.cmd == "apply":
            payload = _read_json_arg(args.json, args.json_stdin)
            if not isinstance(payload, dict):
                raise ValueError("apply JSON must be an object")
            name = args.name or args.script or payload.get("name")
            if not name:
                raise ValueError("apply needs a part name")
            result = cmd_apply(str(name), payload.get("params") or payload)
        elif args.cmd == "import":
            result = cmd_import_step(args.name or Path(args.step).stem, Path(args.step))
        elif args.cmd == "snapshot":
            result = cmd_snapshot(args.script, args.view)
        elif args.cmd == "similarity":
            result = cmd_similarity(args.script, args.ref or None)
        elif args.cmd == "advice":
            result = cmd_advice(args.script)
        else:
            result = cmd_preview(args.script)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    if result.get("name") and args.cmd not in {"params"}:
        _publish_latest(result)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
