"""CAD policy gates: IR quality, gen stall, event-sourced part memory.

Lives in EasyCAD runtime (not dsh / Multi-Agent-CAD). Geometry facts on disk
are the only high-trust memory; chat prose never writes sizes.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

from params import extract_params, load_ir

JACCARD_STALL = 0.92
STALL_GUIDE_AT = 1  # first empty spin vs previous gen: allow + hint
STALL_BLOCK_AT = 2  # second consecutive empty spin: refuse gen
ENVELOPE_PROGRESS = 0.05  # relative drop in bbox error counts as progress

_HOLE_WORD = re.compile(r"孔|孔径|通孔|\bholes?\b|\bbore\b|\bdiameter\b|直径|Ø", re.I)
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_HOLE_AS_DIAMETER = re.compile(r'Hole\(\s*PARAMS\[\s*[\'"]hole_d[\'"]\s*\]\s*\)')
_TOKEN = re.compile(r"[A-Za-z_]\w+|\d+(?:\.\d+)?")
_PARAM_KEYS = ("length", "width", "height", "hole_d")
_SHELL_WRITE = re.compile(
    r"(?:[>]{1,2}|out-file|set-content|add-content|tee-object).{0,80}\.step\.py"
    r"|new-item.{0,80}\.step\.py"
    r"|copy-item.{0,80}\.step\.py",
    re.I | re.S,
)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _read_json(path: Path, fallback):
    if not path.is_file():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return fallback


def events_path(models: Path, stem: str) -> Path:
    return models / f"{stem}.events.jsonl"


def memory_path(models: Path, stem: str) -> Path:
    return models / f"{stem}.memory.json"


def tokenize_source(source: str) -> list[str]:
    return [m.group(0).lower() for m in _TOKEN.finditer(source or "")]


def jaccard(a: list[str], b: list[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    union = sa | sb
    return len(sa & sb) / len(union) if union else 1.0


def source_hash(source: str) -> str:
    return hashlib.sha256((source or "").encode("utf-8")).hexdigest()[:16]


def envelope_error(size_mm: list | None, expect: list | None) -> float | None:
    if not isinstance(size_mm, list) or len(size_mm) != 3:
        return None
    if not isinstance(expect, list) or len(expect) != 3:
        return None
    try:
        return sum(abs(float(g) - float(e)) for g, e in zip(size_mm, expect, strict=True))
    except (TypeError, ValueError):
        return None


def fail_ids(qa: dict | None) -> list[str]:
    if not isinstance(qa, dict):
        return []
    out = []
    for item in qa.get("checks") or []:
        if not isinstance(item, dict):
            continue
        if item.get("advisory"):
            continue
        if item.get("id") == "single_body":
            continue
        if not item.get("pass"):
            out.append(str(item.get("id") or "check"))
    return sorted(out)


def feature_texts(ir: dict) -> list[str]:
    out = []
    for feat in ir.get("features") or []:
        if isinstance(feat, str):
            out.append(feat)
        elif isinstance(feat, dict):
            out.append(str(feat.get("text") or feat.get("note") or feat.get("id") or ""))
    return [t for t in out if t]


def mentions_hole(texts: list[str]) -> bool:
    return any(_HOLE_WORD.search(t) for t in texts)


def validate_ir(ir: dict, models: Path | None = None) -> list[dict]:
    """Hard defects on a brief/IR card. Empty list means gen may proceed (after review)."""
    defects: list[dict] = []
    stem = str(ir.get("name") or "")
    env = (ir.get("envelope") or {}).get("size_mm")
    if not isinstance(env, list) or len(env) != 3:
        defects.append({"id": "envelope", "message": "IR 缺少 overall_size_mm / envelope.size_mm [X,Y,Z]"})
        env_ok = []
    else:
        env_ok = []
        for axis, raw in zip("XYZ", env, strict=True):
            try:
                val = float(raw)
            except (TypeError, ValueError):
                defects.append({"id": "envelope", "message": f"{axis} 尺寸不是数字"})
                continue
            env_ok.append(val)
            if val <= 0:
                defects.append({"id": "envelope", "message": f"{axis} 尺寸必须 > 0 mm"})
    texts = feature_texts(ir)
    intent = str(ir.get("intent") or "")
    if not intent.strip() and not texts:
        defects.append({"id": "intent", "message": "缺少 intent / special_features，无法验证设计意图"})
    if mentions_hole(texts + [intent]) and not any(_NUMBER.search(t) for t in texts if _HOLE_WORD.search(t)):
        defects.append({
            "id": "hole_undimensioned",
            "message": "提到孔/孔径但特征文本没有数字。写成直径 mm，gen 时 Hole(r) 用半径（直径/2）",
        })
    params = ir.get("params") if isinstance(ir.get("params"), dict) else {}
    hole_d = params.get("hole_d")
    try:
        hole_n = float(hole_d) if hole_d is not None else None
    except (TypeError, ValueError):
        hole_n = None
    if hole_n is not None and env_ok and len(env_ok) >= 2 and hole_n >= min(env_ok[0], env_ok[1]):
        defects.append({"id": "hole_too_big", "message": "hole_d 必须小于 length 和 width"})
    meta = ir.get("meta") if isinstance(ir.get("meta"), dict) else {}
    task = str(meta.get("task_type") or ir.get("kind") or "part")
    if task == "assembly":
        defects.append({"id": "assembly", "message": "assembly 未实现；用单个 solid 的 task_type=part"})
    if task == "edit" and models is not None and stem:
        has = any((models / f"{stem}{ext}").is_file() for ext in (".step.py", ".step", ".glb"))
        if not has:
            defects.append({"id": "edit_missing", "message": f"task_type=edit 但 models/{stem} 不存在，应改用 part 和新 stem"})
    return defects


def review_ir(models: Path, stem: str, user_intent: str = "") -> dict:
    """Read-only semantic verdict from IR + on-disk facts. Does not write geometry."""
    ir = load_ir(models / f"{stem}.ir.json")
    if not ir:
        return {
            "ok": True,
            "pass": False,
            "name": stem,
            "defects": [{"id": "no_ir", "message": "没有 IR。先 easycad_brief，不要 easycad_gen"}],
            "revisions": ["Call easycad_brief with overall_size_mm and special_features."],
            "hint": "Fix the IR with easycad_brief; do not gen until review.pass is true.",
        }
    defects = validate_ir(ir, models)
    qa = _read_json(models / f"{stem}.qa.json", None)
    facts = (qa or {}).get("size_mm")
    stored = _read_json(memory_path(models, stem), {}) or {}
    if not facts and isinstance(stored.get("facts"), dict):
        facts = stored["facts"].get("size_mm")
    expect = (ir.get("envelope") or {}).get("size_mm")
    err = envelope_error(facts if isinstance(facts, list) else None, expect)
    if facts and expect and err is not None and err > float((ir.get("constraints") or [{}])[0].get("tol_mm") or 0.2) * 3:
        # only when a previous gen exists — first review before gen has no facts
        if (models / f"{stem}.step").is_file() or (models / f"{stem}.step.py").is_file():
            defects.append({
                "id": "envelope_mismatch",
                "message": f"磁盘外形 {facts} 与 IR 包络 {expect} 差 {round(err, 3)} mm，先改 IR 或 apply 参数",
            })
    texts = feature_texts(ir)
    intent = str(ir.get("intent") or user_intent or "")
    script = models / f"{stem}.step.py"
    source = script.read_text(encoding="utf-8") if script.is_file() else ""
    extracted = extract_params(ir, source)
    vals = extracted.get("values") or {}
    if mentions_hole(texts + [intent]) and not vals.get("hole_d"):
        defects.append({"id": "hole_missing", "message": "意图有孔，但 IR/PARAMS 没有 hole_d"})
    if user_intent and intent and user_intent.strip() and intent.strip():
        # cheap semantic: shared tokens; independent of the chat transcript
        ju = jaccard(tokenize_source(user_intent), tokenize_source(intent + " " + " ".join(texts)))
        if ju < 0.08 and len(tokenize_source(user_intent)) >= 4:
            defects.append({
                "id": "intent_drift",
                "message": "IR.intent 与当前用户描述几乎没有共同词，修订 IR 后再 brief",
            })
    revisions = [d["message"] for d in defects]
    passed = not defects
    return {
        "ok": True,
        "pass": passed,
        "name": stem,
        "mode": "review",
        "defects": defects,
        "revisions": revisions,
        "intent": intent,
        "envelope": ir.get("envelope"),
        "hint": (
            "IR 通过。用同一 name 和 expect_size=overall_size_mm 调用 easycad_gen。"
            if passed
            else "只改 IR（easycad_brief），不要 easycad_gen。"
        ),
    }


def last_events(models: Path, stem: str, kind: str | None = None, limit: int = 40) -> list[dict]:
    path = events_path(models, stem)
    if not path.is_file():
        return []
    rows = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if kind and row.get("kind") != kind:
                continue
            rows.append(row)
    except OSError:
        return []
    return rows[-limit:]


def append_event(models: Path, stem: str, kind: str, payload: dict) -> dict:
    models.mkdir(parents=True, exist_ok=True)
    event = {"ts": _now_ms(), "name": stem, "kind": kind, **payload}
    with events_path(models, stem).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    return event


def stall_verdict(models: Path, stem: str, source: str, expect: list | None) -> dict:
    """Dual-condition empty-spin: source Jaccard high AND envelope/QA not closer."""
    gens = last_events(models, stem, "gen", limit=12)
    last = next((g for g in reversed(gens) if g.get("source")), None)
    if not last:
        return {"stall": False, "consecutive": 0, "jaccard": None, "progress": None}
    jac = jaccard(tokenize_source(last.get("source") or ""), tokenize_source(source))
    similar = jac >= JACCARD_STALL
    prev_err = last.get("envelope_error")
    # progress unknown until this gen runs; compare fail set + whether source change is tiny
    prev_fails = list(last.get("fail_ids") or [])
    consecutive = int(last.get("consecutive_stalls") or 0)
    no_progress = similar  # source-level; envelope checked after using previous error stickiness
    if similar and prev_fails:
        no_progress = True
    stall = similar and no_progress
    nxt = consecutive + 1 if stall else 0
    level = None
    if stall and nxt >= STALL_BLOCK_AT:
        level = "block"
    elif stall and nxt >= STALL_GUIDE_AT:
        level = "guide"
    return {
        "stall": stall,
        "consecutive": nxt if stall else 0,
        "jaccard": round(jac, 4),
        "similar": similar,
        "prev_envelope_error": prev_err,
        "prev_fail_ids": prev_fails,
        "level": level,
        "hint": (
            "源码几乎没变且 QA 无进展：改 PARAMS 或特征（孔用半径），不要再贴同一段 gen_step。"
            if level == "guide"
            else (
                "连续空转，已拦截 easycad_gen。用 easycad_apply 改参数，或缩小特征后换写法。"
                if level == "block"
                else None
            )
        ),
    }


def post_gen_progress(prev: dict | None, qa: dict | None, facts: dict | None, expect: list | None) -> bool:
    if not prev:
        return True
    err = envelope_error((facts or {}).get("size_mm"), expect or prev.get("expect_size"))
    prev_err = prev.get("envelope_error")
    if err is not None and prev_err is not None and float(prev_err) > 0:
        if err < float(prev_err) * (1.0 - ENVELOPE_PROGRESS):
            return True
        if err + 1e-9 < float(prev_err):
            return True
    fails = fail_ids(qa)
    prev_fails = list(prev.get("fail_ids") or [])
    if fails != prev_fails:
        return True
    if qa and qa.get("pass") and not prev.get("qa_pass"):
        return True
    return False


def gate_gen(models: Path, stem: str, source: str, expect: list | None) -> dict:
    ir = load_ir(models / f"{stem}.ir.json")
    if not ir:
        return {
            "ok": False,
            "blocked": "ir_required",
            "error": "先 easycad_brief 写 IR，再 easycad_gen。不要无 brief 生成。",
            "name": stem,
            "script": None,
            "step": None,
            "facts": {},
            "qa": {"pass": False, "checks": []},
        }
    defects = validate_ir(ir, models)
    if defects:
        return {
            "ok": False,
            "blocked": "ir_invalid",
            "error": "IR 未过闸：" + "；".join(d["message"] for d in defects),
            "defects": defects,
            "name": stem,
            "hint": "只改 IR（easycad_brief），不要 gen。",
            "script": None,
            "step": None,
            "facts": {},
            "qa": {"pass": False, "checks": []},
        }
    if _HOLE_AS_DIAMETER.search(source or ""):
        return {
            "ok": False,
            "blocked": "hole_radius",
            "error": 'Hole(r) 是半径。写成 Hole(PARAMS["hole_d"] / 2)，不要 Hole(PARAMS["hole_d"])。',
            "name": stem,
            "script": None,
            "step": None,
            "facts": {},
            "qa": {"pass": False, "checks": []},
        }
    stall = stall_verdict(models, stem, source, expect)
    if stall.get("level") == "block":
        return {
            "ok": False,
            "blocked": "loop_stall",
            "error": stall["hint"],
            "loop": stall,
            "name": stem,
            "script": None,
            "step": None,
            "facts": {},
            "qa": {"pass": False, "checks": []},
        }
    return {"ok": True, "name": stem, "loop": stall, "ir_ok": True}


def allowed_memory_keys(ir: dict, source: str, facts: dict | None) -> set[str]:
    """Counterfactual gate: a key may enter memory only if IR/PARAMS/facts already carry it."""
    allowed = {"length", "width", "height"}
    extracted = extract_params(ir, source)
    for key in _PARAM_KEYS:
        if key in (extracted.get("values") or {}):
            allowed.add(key)
    params = ir.get("params") if isinstance(ir.get("params"), dict) else {}
    for key in _PARAM_KEYS:
        if params.get(key) is not None:
            allowed.add(key)
    if isinstance(facts, dict) and facts.get("size_mm"):
        allowed.update(("length", "width", "height"))
    texts = feature_texts(ir) + [str(ir.get("intent") or "")]
    if mentions_hole(texts):
        allowed.add("hole_d")
    return allowed


def consolidate_memory(models: Path, stem: str) -> dict:
    """Rebuild memory.json from events + IR/QA. Dual gates drop advice and invented sizes."""
    ir = load_ir(models / f"{stem}.ir.json")
    qa_file = _read_json(models / f"{stem}.qa.json", None)
    script = models / f"{stem}.step.py"
    source = script.read_text(encoding="utf-8") if script.is_file() else ""
    events = last_events(models, stem, limit=80)
    last_geo = None
    for row in reversed(events):
        if row.get("kind") in {"gen", "apply", "inspect"} and row.get("facts"):
            last_geo = row
            break
    facts = (last_geo or {}).get("facts")
    if not isinstance(facts, dict) and isinstance(qa_file, dict) and qa_file.get("size_mm"):
        facts = {"size_mm": qa_file.get("size_mm")}
    qa = (last_geo or {}).get("qa") or qa_file
    extracted = extract_params(ir, source) if (ir or source) else {"values": {}}
    raw_params = dict(extracted.get("values") or {})
    allowed = allowed_memory_keys(ir, source, facts if isinstance(facts, dict) else None)
    params = {k: v for k, v in raw_params.items() if k in allowed}
    # QA gate: facts only from tool rows / qa.json, never advice
    high = {}
    if isinstance(facts, dict) and facts.get("size_mm"):
        high["facts"] = {"size_mm": facts.get("size_mm"), "solid_count": facts.get("solid_count"), "is_valid": facts.get("is_valid")}
        if "volume_mm3" in facts:
            high["facts"]["volume_mm3"] = facts["volume_mm3"]
    if ir.get("envelope"):
        high["envelope"] = ir["envelope"]
    if params:
        high["params"] = params
    if isinstance(qa, dict) and "pass" in qa:
        high["qa"] = {"pass": bool(qa.get("pass")), "checks": fail_ids(qa)}
    medium = {
        "intent": ir.get("intent") or "",
        "features": ir.get("features") or [],
    }
    advice_file = models / f"{stem}.advice.json"
    low = {}
    if advice_file.is_file():
        low["advice_path"] = f"models/{stem}.advice.json"
    memory = {
        "name": stem,
        "updatedAt": _now_ms(),
        "trust": {"high": high, "medium": medium, "low": low},
        "intent": medium["intent"],
        "envelope": high.get("envelope"),
        "params": high.get("params"),
        "features": medium["features"],
        "qa": high.get("qa"),
        "facts": high.get("facts"),
        "source": f"models/{stem}.step.py" if script.is_file() else None,
        "step": f"models/{stem}.step" if (models / f"{stem}.step").is_file() else None,
        "events_path": f"models/{stem}.events.jsonl",
        "inject": {
            "high": high,
            "medium": medium,
            "skip": ["advice", "similarity", "chat_sizes"],
        },
    }
    memory_path(models, stem).write_text(json.dumps(memory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return memory


def record_and_consolidate(models: Path, stem: str, kind: str, payload: dict) -> dict:
    append_event(models, stem, kind, payload)
    return consolidate_memory(models, stem)


def looks_like_step_script_write(name: str, args: dict) -> str | None:
    """Host-side: bash/pwsh/write/edit targeting *.step.py bypasses PARAMS/worker."""
    tool = str(name or "")
    if tool in {"easycad_part", "easycad_assemble", "easycad_dfam"}:
        return f"{tool} 未实现，执行层已拒绝。用 easycad_gen 建占位实体。"
    path = str(args.get("file_path") or args.get("path") or "")
    if tool in {"write", "edit", "str_replace_editor", "str-replace-editor"} and path.lower().endswith(".step.py"):
        return "不要用文件系统改 *.step.py。用 easycad_gen / easycad_apply。"
    command = str(args.get("command") or args.get("script") or "")
    if tool in {"bash", "pwsh"} and command:
        lower = command.lower()
        if ".step.py" in lower and _SHELL_WRITE.search(command):
            return "不要用 shell 改写 *.step.py。用 easycad_gen / easycad_apply。"
        if re.search(r"models[/\\][^\"']+\.step\.py", lower) and re.search(
            r">|set-content|out-file|add-content|tee-object|copy-item|move-item", lower
        ):
            return "不要用 shell 改写 models/*.step.py。用 easycad_gen / easycad_apply。"
    return None
