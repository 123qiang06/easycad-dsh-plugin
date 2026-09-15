"""EasyCAD-owned CAD CLI. Does not import text-to-cad / MAC / dsh source.

Commands:
  write-gen / gen / inspect / qa / export / brief / measure / preview / params / apply
  runner-brief / runner-review / runner-propose / runner-build / runner-qa
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
from policy import (
    consolidate_memory,
    envelope_error,
    fail_ids,
    gate_gen,
    last_events,
    post_gen_progress,
    record_and_consolidate,
    review_ir,
    source_hash,
    stall_verdict,
    validate_ir,
)
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
    skip_publish = bool(result.pop("_skip_publish", False))
    skip_memory = bool(result.get("_skip_memory")) or (
        result.get("ok") is False and not result.get("facts")
    )
    if skip_publish:
        if not skip_memory:
            _write_memory(str(name), result, str(result.get("_memory_kind") or "gen"))
        result.pop("_skip_memory", None)
        result.pop("_memory_kind", None)
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
        ("runner", ".runner.json"),
        ("shot_step", ".shot.step"),
        ("shot_glb", ".shot.glb"),
        ("mold_step", ".mold.step"),
        ("mold_glb", ".mold.glb"),
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
    if not skip_memory:
        _write_memory(stem, result, str(result.get("_memory_kind") or "gen"))
    result.pop("_skip_memory", None)
    result.pop("_memory_kind", None)
    result["dock"] = payload["view"]


def _write_memory(stem: str, result: dict, kind: str = "gen") -> None:
    """Per-part card via event log + dual gates. Session isolation is the plugin map."""
    source = ""
    script = MODELS / f"{stem}.step.py"
    if script.is_file():
        try:
            source = script.read_text(encoding="utf-8")
        except OSError:
            source = ""
    expect = None
    qa = result.get("qa") if isinstance(result.get("qa"), dict) else None
    facts = result.get("facts") if isinstance(result.get("facts"), dict) else None
    for check in (qa or {}).get("checks") or []:
        if isinstance(check, dict) and check.get("id") == "overall_dimension":
            expect = check.get("expected_mm")
            break
    gens = last_events(MODELS, stem, "gen", limit=8)
    prev = next((g for g in reversed(gens) if g.get("kind") == "gen" or g.get("source") is not None), None)
    progressed = post_gen_progress(prev, qa, facts, expect)
    stall = stall_verdict(MODELS, stem, source, expect)
    consecutive = 0 if progressed else int(stall.get("consecutive") or 0)
    if kind == "apply":
        consecutive = 0
    record_and_consolidate(
        MODELS,
        stem,
        kind,
        {
            "source": source if kind == "gen" else None,
            "source_hash": source_hash(source) if source else None,
            "qa": {"pass": bool((qa or {}).get("pass"))} if qa else None,
            "qa_pass": bool((qa or {}).get("pass")) if qa else None,
            "fail_ids": fail_ids(qa),
            "facts": facts,
            "expect_size": expect,
            "envelope_error": envelope_error((facts or {}).get("size_mm") if facts else None, expect),
            "progressed": progressed,
            "consecutive_stalls": consecutive,
            "params": result.get("params"),
        },
    )


def cmd_write_gen(
    name: str,
    source: str,
    expect_size: list[float] | None,
    tol: float,
    ref_image: str | None = None,
    *,
    skip_stall: bool = False,
) -> dict:
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
    stem = _models_stem(name)
    if not skip_stall:
        gated = gate_gen(MODELS, stem, text, expect_size)
        if not gated.get("ok"):
            return gated
        loop_meta = gated.get("loop") or {}
    else:
        loop_meta = {}
    script.write_text(text, encoding="utf-8")
    result = cmd_gen(script, expect_size=expect_size, tol=tol, ref_image=ref_image)
    if loop_meta.get("level") == "guide":
        result["loop"] = loop_meta
        result["hint"] = loop_meta.get("hint")
    return result


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
    defects = validate_ir(ir, MODELS)
    if defects:
        return {
            "ok": False,
            "name": stem,
            "blocked": "ir_invalid",
            "error": "IR 未过闸：" + "；".join(item["message"] for item in defects),
            "defects": defects,
            "hint": "修订尺寸/特征后再次 easycad_brief，不要 easycad_gen。",
            "brief": {},
            "path": "",
            "_skip_memory": True,
        }
    ir_path = MODELS / f"{stem}.ir.json"
    ir_path.write_text(json.dumps(ir, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    brief = brief_view(ir)
    brief_path = MODELS / f"{stem}.brief.json"
    brief_path.write_text(json.dumps(brief, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    record_and_consolidate(MODELS, stem, "brief", {"intent": ir.get("intent"), "envelope": ir.get("envelope")})
    return {
        "ok": True,
        "name": stem,
        "brief": brief,
        "ir": ir,
        "path": _rel(brief_path),
        "ir_path": _rel(ir_path),
        "hint": "接着 easycad_review，通过后再 easycad_gen。",
        "_skip_memory": True,
    }


def cmd_review(stem_or_path: str, user_intent: str = "") -> dict:
    stem = _models_stem(stem_or_path)
    verdict = review_ir(MODELS, stem, user_intent)
    record_and_consolidate(
        MODELS,
        stem,
        "review",
        {"pass": verdict.get("pass"), "defects": verdict.get("defects")},
    )
    verdict["_skip_memory"] = True
    return verdict


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
    from runner import apply_runner_params, is_runner_param_payload

    if is_runner_param_payload(values):
        return _export_runner_result(apply_runner_params(stem, values))
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
    result = cmd_write_gen(stem, script.read_text(encoding="utf-8"), expect, 0.2, skip_stall=True)
    result["params"] = extract_params(next_ir, script.read_text(encoding="utf-8"))
    result["_memory_kind"] = "apply"
    return result


def _req_name(req: dict, default: str | None = None) -> str:
    name = str(req.get("name") or default or "")
    if not name:
        raise ValueError("name required")
    return name


def worker_handle(req: dict) -> dict:
    """Reloadable OCCT job dispatch. cmd_worker() looks this up after
    importlib.reload so new commands pick up without killing the warm process."""
    cmd = req.get("cmd")
    expect = req.get("expect_size")
    tol = float(req.get("tol", 0.2))
    ref = req.get("ref_image") or None
    if cmd == "gen":
        source = req.get("source", "")
        if not str(source).strip():
            raise ValueError("empty source")
        return cmd_write_gen(_req_name(req, "part"), source, expect, tol, ref)
    if cmd == "apply":
        return cmd_apply(_req_name(req), req.get("params") or {})
    if cmd == "import":
        name = _req_name(req)
        step = str(req.get("step") or "")
        if not step:
            raise ValueError("import needs name and step")
        return cmd_import_step(name, Path(step))
    if cmd == "preview":
        return cmd_preview(_req_name(req), ensure_glb=True)
    if cmd == "similarity":
        return cmd_similarity(_req_name(req), ref)
    if cmd == "snapshot":
        return cmd_snapshot(_req_name(req), req.get("views") or [])
    if cmd == "advice":
        return cmd_advice(_req_name(req))
    if cmd == "review":
        return cmd_review(_req_name(req), str(req.get("intent") or ""))
    if cmd == "consolidate":
        stem = _req_name(req)
        return {"ok": True, "name": stem, "memory": consolidate_memory(MODELS, stem), "_skip_memory": True}
    if cmd == "inspect":
        return cmd_inspect(_models_script(_req_name(req)), expect, tol, ref)
    if cmd == "qa":
        return cmd_qa(_models_script(_req_name(req)), expect, tol, ref)
    if cmd == "measure":
        checks = req.get("checks") or []
        return cmd_measure(_models_script(_req_name(req)), checks, tol)
    if cmd == "export":
        formats = req.get("formats") or ["stl"]
        return cmd_export(_models_script(_req_name(req)), formats)
    if cmd == "params":
        return cmd_params(_req_name(req))
    if cmd == "runner-brief":
        return cmd_runner_brief(_req_name(req), req.get("payload") or req)
    if cmd == "runner-review":
        return cmd_runner_review(_req_name(req))
    if cmd == "runner-propose":
        return cmd_runner_propose(_req_name(req))
    if cmd == "runner-build":
        return cmd_runner_build(_req_name(req), req.get("candidate"))
    if cmd == "runner-qa":
        return cmd_runner_qa(_req_name(req))
    if cmd == "runner-mold":
        return cmd_runner_mold(_req_name(req))
    raise ValueError(f"unknown worker cmd: {cmd!r}")


def cmd_worker() -> int:
    """Persistent build123d worker: warm the heavy OCP import once, then serve
    one-shot gen/apply jobs over newline-delimited JSON on stdin/stdout. Makes
    successive CAD operations seconds instead of a ~14s cold import each time.
    Each job reloads this module and calls worker_handle, so command logic and
    the dispatch table pick up without a dsh restart; build123d stays warm."""
    import build123d  # noqa: F401  (warm the import so later jobs reuse it)
    import importlib
    try:
        mod = importlib.import_module('cad_cli')  # this file as a reloadable module
    except Exception:
        mod = None

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
            handle = getattr(mod, "worker_handle", None) if mod is not None else worker_handle
            if handle is None:
                handle = worker_handle
            result = handle(req)
            if result.get("name"):
                pub = getattr(mod, "_publish_latest", _publish_latest) if mod is not None else _publish_latest
                pub(result)
            print(json.dumps(result, ensure_ascii=False))
            sys.stdout.flush()
        except Exception as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
            sys.stdout.flush()
    return 0


def _export_runner_result(prepared: dict) -> dict:
    if not prepared.get("ok"):
        return prepared
    from build123d import export_step

    stem = prepared["name"]
    shot = prepared.pop("shot", None)
    mold = prepared.pop("mold", None)
    prepared.pop("runner", None)
    shot_step = MODELS / f"{stem}.shot.step"
    shot_glb = MODELS / f"{stem}.shot.glb"
    glb_err = None
    if shot is not None:
        try:
            export_step(shot, str(shot_step))
        except Exception as exc:
            if not shot_step.is_file():
                return {**prepared, "ok": False, "error": f"shot STEP 写出失败: {exc}"}
        try:
            _export_glb(shot, shot_glb)
            _write_topology_sidecar(shot, f"{stem}.shot")
        except Exception as exc:
            glb_err = str(exc)
    mold_err = None
    if mold is not None:
        mold_step = MODELS / f"{stem}.mold.step"
        mold_glb = MODELS / f"{stem}.mold.glb"
        try:
            export_step(mold, str(mold_step))
            _export_glb(mold, mold_glb)
            _write_topology_sidecar(mold, f"{stem}.mold")
            prepared["mold_step"] = _rel(mold_step)
            prepared["mold_glb"] = _rel(mold_glb)
        except Exception as exc:
            mold_err = str(exc)
            if not prepared.get("mold_glb"):
                return {**prepared, "ok": False, "error": f"型腔预览写出失败: {exc}", "mold_error": mold_err}
    if shot is None and mold is None:
        return prepared
    prepared["step"] = _rel(shot_step) if shot_step.is_file() else None
    prepared["glb"] = _rel(shot_glb) if shot_glb.is_file() and glb_err is None else None
    prepared["glb_error"] = glb_err
    prepared["mold_error"] = mold_err
    prepared["open"] = f"/easycad/view?name={stem}&work=runner"
    prepared["_memory_kind"] = "runner_build"
    return prepared


def cmd_runner_brief(stem: str, payload: dict) -> dict:
    from runner import cmd_runner_brief as impl

    result = impl(stem, payload if isinstance(payload, dict) else {})
    result["_skip_publish"] = True
    result["_skip_memory"] = True
    return result


def cmd_runner_review(stem: str) -> dict:
    from runner import review_runner

    result = review_runner(stem)
    result["_skip_publish"] = True
    result["_skip_memory"] = True
    return result


def cmd_runner_propose(stem: str) -> dict:
    from runner import cmd_runner_propose as impl

    result = impl(stem)
    result["_skip_memory"] = True
    return result


def _reload_runner():
    import importlib
    import runner

    return importlib.reload(runner)


def cmd_runner_build(stem: str, candidate: str | None = None) -> dict:
    runner = _reload_runner()
    return _export_runner_result(runner.prepare_runner_build(stem, candidate))


def cmd_runner_mold(stem: str) -> dict:
    runner = _reload_runner()
    return _export_runner_result(runner.prepare_runner_mold(stem))


def cmd_runner_qa(stem: str) -> dict:
    from runner import cmd_runner_qa as impl

    result = impl(stem)
    result["_skip_publish"] = True
    result["_skip_memory"] = True
    return result


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


_GRADE_EN = {
    "很高": "very high",
    "较高": "high",
    "中等": "medium",
    "较低": "low",
    "很低": "very low",
}


def _ui_locale() -> str:
    raw = _load_sidecar(MODELS / ".easycad-locale.json")
    return "en" if str(raw.get("locale") or "") == "en" else "zh"


def _advice_item(
    item_id: str,
    lang: str,
    *,
    title_zh: str,
    title_en: str,
    text_zh: str,
    text_en: str,
    prompt_zh: str,
    prompt_en: str,
    severity: str = "warn",
) -> dict:
    return {
        "id": item_id,
        "severity": severity,
        "executable": True,
        "title": title_en if lang == "en" else title_zh,
        "text": text_en if lang == "en" else text_zh,
        "prompt": prompt_en if lang == "en" else prompt_zh,
    }


def _advice_for(stem: str, *, persist: bool = True, locale: str | None = None) -> dict:
    """Executable findings from QA + similarity + missing params. No informational dumps."""
    lang = "en" if (locale or _ui_locale()) == "en" else "zh"
    qa = _load_sidecar(MODELS / f"{stem}.qa.json")
    sim = _load_sidecar(MODELS / f"{stem}.similarity.json")
    ir = _load_sidecar(MODELS / f"{stem}.ir.json")
    features = [
        str(item.get("text") or item.get("id") or "")
        for item in (ir.get("features") or [])
        if isinstance(item, dict)
    ]
    script = _models_script(stem)
    src = script.read_text(encoding="utf-8") if script.is_file() else ""
    extracted = extract_params(ir, src) if script.is_file() else {}
    vals = extracted.get("values") or {}
    axis_key = {"X": "length", "Y": "width", "Z": "height"}

    items: list[dict] = []
    checks = qa.get("checks") if isinstance(qa.get("checks"), list) else []
    failed = [c for c in checks if c.get("pass") is False and c.get("id") != "single_body"]
    for c in checks:
        cid = c.get("id")
        if c.get("pass") is False:
            if cid == "overall_dimension":
                for m in c.get("mismatches") or []:
                    axis = str(m.get("axis") or "").upper()
                    key = axis_key.get(axis, "length")
                    got = m.get("got_mm")
                    expect = m.get("expected_mm")
                    delta = m.get("delta_mm")
                    items.append(_advice_item(
                        f"overall_{axis or key}",
                        lang,
                        severity="error",
                        title_zh=f"把 {axis or key} 向外形改到 {expect} mm",
                        title_en=f"Set {axis or key} envelope to {expect} mm",
                        text_zh=f"{axis} 轴现在 {got} mm，期望 {expect} mm，相差 {delta} mm。",
                        text_en=f"{axis}-axis is {got} mm, expected {expect} mm (delta {delta} mm).",
                        prompt_zh=(
                            f"零件 {stem}：{axis} 向外形 {got} mm，IR 期望 {expect} mm。"
                            f"请立刻 easycad_apply，把 PARAMS 的 {key} 设为 {expect}。"
                            f"不要换零件名，不要改无关特征。"
                        ),
                        prompt_en=(
                            f"Part {stem}: {axis}-axis is {got} mm, IR expects {expect} mm. "
                            f"Call easycad_apply now and set PARAMS {key} to {expect}. "
                            f"Do not change the part name or unrelated features."
                        ),
                    ))
            elif cid == "watertight":
                items.append(_advice_item(
                    "watertight",
                    lang,
                    severity="error",
                    title_zh="修复水密/开面",
                    title_en="Fix watertight / open faces",
                    text_zh="QA watertight 未过：可能有开面、自交或空实体。",
                    text_en="QA watertight failed: open faces, self-intersections, or empty solids.",
                    prompt_zh=(
                        f"零件 {stem} 的 QA watertight 未过。"
                        f"请检查 gen_step 的开面/自交/空实体，修好后 easycad_gen 同一 name。"
                        f"不要换零件名。"
                    ),
                    prompt_en=(
                        f"Part {stem} failed QA watertight. Fix open faces, self-intersections, or empty solids "
                        f"in gen_step, then easycad_gen the same name. Do not change the stem."
                    ),
                ))

    hole_needed = any(("hole" in f.lower() or "孔" in f or "孔径" in f) for f in features)
    if hole_needed and not vals.get("hole_d"):
        items.append(_advice_item(
            "hole_d",
            lang,
            severity="warn",
            title_zh="补上孔径参数并打孔",
            title_en="Add hole_d and cut a hole",
            text_zh="需求提到孔，但 PARAMS 没有 hole_d。",
            text_en="The spec mentions a hole, but PARAMS has no hole_d.",
            prompt_zh=(
                f"零件 {stem} 需求有孔，但 PARAMS 没有 hole_d。"
                f"请在 PARAMS 增加 hole_d，gen_step 用 Hole(PARAMS['hole_d']/2) 打通孔，"
                f"然后 easycad_gen 同一 name。"
            ),
            prompt_en=(
                f"Part {stem} needs a hole, but PARAMS has no hole_d. "
                f"Add hole_d to PARAMS, cut Hole(PARAMS['hole_d']/2) in gen_step, "
                f"then easycad_gen the same name."
            ),
        ))

    if sim.get("present"):
        score = float(sim.get("score") or 0)
        view = sim.get("best_view") or "iso"
        if score < 0.80:
            items.append(_advice_item(
                "similarity",
                lang,
                severity="warn" if score >= 0.50 else "error",
                title_zh=f"按 {view} 视图贴合参考图",
                title_en=f"Match the reference on the {view} view",
                text_zh=f"与参考图贴合度 {score:.2f}，最佳 {view} 视图。",
                text_en=f"Silhouette IoU {score:.2f}, best view {view}.",
                prompt_zh=(
                    f"零件 {stem} 与参考图贴合度 {score:.2f}（最佳 {view}）。"
                    f"请对照 {view} 视图改轮廓、孔位或倒角，easycad_gen 同一 name，"
                    f"不要换零件。改完可再看相似度。"
                ),
                prompt_en=(
                    f"Part {stem} silhouette IoU is {score:.2f} (best {view}). "
                    f"Adjust outline, holes, or fillets against the {view} view, "
                    f"easycad_gen the same name, do not switch parts."
                ),
            ))

    if not failed and sim.get("pass") is not False:
        items.append(_advice_item(
            "export_stl",
            lang,
            severity="ok",
            title_zh="导出 STL",
            title_en="Export STL",
            text_zh="几何校验已通过，可导出打印网格。",
            text_en="Geometry checks passed. Export a print mesh.",
            prompt_zh=f"零件 {stem} 几何已通过。请 easycad_export 导出 STL，不要改几何，不要换零件名。",
            prompt_en=f"Part {stem} passed geometry checks. Call easycad_export for STL. Do not change geometry or the part name.",
        ))

    advice = {
        "name": stem,
        "locale": lang,
        "mode": "rule-based",
        "items": items,
        "suggestions": [
            f"{it['title']}：{it['text']}" if lang == "zh" else f"{it['title']}: {it['text']}"
            for it in items
        ],
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
    for key, suffix in (
        ("shot_step", ".shot.step"),
        ("shot_glb", ".shot.glb"),
        ("mold_step", ".mold.step"),
        ("mold_glb", ".mold.glb"),
        ("runner", ".runner.json"),
    ):
        extra = MODELS / f"{stem}{suffix}"
        if extra.is_file():
            artifacts[key] = _rel(extra)
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

    p_rev = sub.add_parser("review")
    p_rev.add_argument("script")
    p_rev.add_argument("--intent", default="")

    p_cons = sub.add_parser("consolidate")
    p_cons.add_argument("script")

    p_rbrief = sub.add_parser("runner-brief")
    p_rbrief.add_argument("--name", required=True)
    p_rbrief.add_argument("--json", default="")
    p_rbrief.add_argument("--json-stdin", action="store_true")

    p_rrev = sub.add_parser("runner-review")
    p_rrev.add_argument("script")

    p_rprop = sub.add_parser("runner-propose")
    p_rprop.add_argument("script")

    p_rbuild = sub.add_parser("runner-build")
    p_rbuild.add_argument("script")
    p_rbuild.add_argument("--candidate", default="")

    p_rqa = sub.add_parser("runner-qa")
    p_rqa.add_argument("script")

    p_rmold = sub.add_parser("runner-mold")
    p_rmold.add_argument("script")

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
        elif args.cmd == "review":
            result = cmd_review(args.script, args.intent or "")
        elif args.cmd == "consolidate":
            stem = _models_stem(args.script)
            result = {"ok": True, "name": stem, "memory": consolidate_memory(MODELS, stem), "_skip_memory": True}
        elif args.cmd == "runner-brief":
            payload = _read_json_arg(args.json, args.json_stdin) if (args.json or args.json_stdin) else {}
            if payload and not isinstance(payload, dict):
                raise ValueError("runner-brief JSON must be an object")
            result = cmd_runner_brief(args.name, payload or {})
        elif args.cmd == "runner-review":
            result = cmd_runner_review(_models_stem(args.script))
        elif args.cmd == "runner-propose":
            result = cmd_runner_propose(_models_stem(args.script))
        elif args.cmd == "runner-build":
            result = cmd_runner_build(_models_stem(args.script), args.candidate or None)
        elif args.cmd == "runner-qa":
            result = cmd_runner_qa(_models_stem(args.script))
        elif args.cmd == "runner-mold":
            result = cmd_runner_mold(_models_stem(args.script))
        else:
            result = cmd_preview(args.script)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    if result.get("name") and args.cmd not in {"params", "review", "consolidate", "runner-review", "runner-qa"}:
        _publish_latest(result)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
