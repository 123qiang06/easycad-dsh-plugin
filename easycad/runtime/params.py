"""Editable part parameters inferred from IR + build123d source."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

FIELDS = (
    {"id": "length", "label": "长度", "unit": "mm"},
    {"id": "width", "label": "宽度", "unit": "mm"},
    {"id": "height", "label": "厚度", "unit": "mm"},
    {"id": "hole_d", "label": "中心孔直径", "unit": "mm"},
)

_BOX = re.compile(r"Box\(\s*([0-9]+(?:\.[0-9]+)?)\s*,\s*([0-9]+(?:\.[0-9]+)?)\s*,\s*([0-9]+(?:\.[0-9]+)?)\s*\)")
_HOLE = re.compile(r"Hole\(\s*([0-9]+(?:\.[0-9]+)?)\s*\)")
_HOLE_NOTE = re.compile(r"d\s*([0-9]+(?:\.[0-9]+)?)", re.I)
_PARAMS = re.compile(r"PARAMS\s*=\s*(\{[^}]*\})", re.S)


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _params_from_source(source: str) -> dict[str, float]:
    out: dict[str, float] = {}
    block = _PARAMS.search(source)
    if block:
        try:
            parsed = ast.literal_eval(block.group(1))
        except (SyntaxError, ValueError):
            parsed = {}
        if isinstance(parsed, dict):
            for key in ("length", "width", "height", "hole_d"):
                number = _num(parsed.get(key))
                if number is not None:
                    out[key] = number
    if len(out) < 3:
        box = _BOX.search(source)
        if box:
            out.setdefault("length", float(box.group(1)))
            out.setdefault("width", float(box.group(2)))
            out.setdefault("height", float(box.group(3)))
    if "hole_d" not in out:
        hole = _HOLE.search(source)
        if hole:
            out["hole_d"] = float(hole.group(1)) * 2.0
    return out


def _params_from_ir(ir: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    stored = ir.get("params")
    if isinstance(stored, dict):
        for key in ("length", "width", "height", "hole_d"):
            number = _num(stored.get(key))
            if number is not None:
                out[key] = number
    size = (ir.get("envelope") or {}).get("size_mm")
    if isinstance(size, list) and len(size) == 3:
        out.setdefault("length", float(size[0]))
        out.setdefault("width", float(size[1]))
        out.setdefault("height", float(size[2]))
    for feat in ir.get("features") or []:
        if not isinstance(feat, dict):
            continue
        text = str(feat.get("text") or feat.get("note") or "")
        match = _HOLE_NOTE.search(text)
        if match:
            out.setdefault("hole_d", float(match.group(1)))
            break
    return out


def extract_params(ir: dict[str, Any] | None, source: str) -> dict[str, Any]:
    values = _params_from_ir(ir or {})
    values.update(_params_from_source(source or ""))
    fields = []
    for spec in FIELDS:
        number = values.get(spec["id"])
        fields.append({**spec, "value": number})
    return {
        "values": values,
        "fields": fields,
        "intent": (ir or {}).get("intent") or "",
        "editable": bool(values.get("length") and values.get("width") and values.get("height")),
    }


def render_source(values: dict[str, float]) -> str:
    hole = values.get("hole_d")
    hole_line = "        Hole(PARAMS[\"hole_d\"] / 2)\n" if hole else ""
    hole_item = f'    "hole_d": {values["hole_d"]},\n' if hole else ""
    return (
        "from build123d import *\n\n"
        "PARAMS = {\n"
        f'    "length": {values["length"]},\n'
        f'    "width": {values["width"]},\n'
        f'    "height": {values["height"]},\n'
        f"{hole_item}"
        "}\n\n"
        "def gen_step():\n"
        "    with BuildPart() as part:\n"
        '        Box(PARAMS["length"], PARAMS["width"], PARAMS["height"])\n'
        f"{hole_line}"
        "    return part.part\n"
    )


def update_source(source: str, values: dict[str, float]) -> str:
    text = source.replace("\r\n", "\n")
    if _PARAMS.search(text):
        block = (
            "PARAMS = {\n"
            f'    "length": {values["length"]},\n'
            f'    "width": {values["width"]},\n'
            f'    "height": {values["height"]},\n'
        )
        if values.get("hole_d"):
            block += f'    "hole_d": {values["hole_d"]},\n'
        block += "}"
        return _PARAMS.sub(block, text, count=1)
    if text.strip() and not _BOX.search(text):
        raise ValueError("当前源码没有可改的 Box(...)，请在对话里改模型")
    return render_source(values)


def update_ir(ir: dict[str, Any], name: str, values: dict[str, float]) -> dict[str, Any]:
    size = [values["length"], values["width"], values["height"]]
    next_ir = dict(ir or {})
    next_ir.setdefault("ir_version", "0.1")
    next_ir.setdefault("kind", "part")
    next_ir["name"] = name
    next_ir.setdefault("units", "mm")
    next_ir["envelope"] = {"size_mm": size}
    next_ir["params"] = {key: values[key] for key in values}
    features = list(next_ir.get("features") or [])
    if values.get("hole_d"):
        note = f"center through hole d{values['hole_d']:g}"
        replaced = False
        for index, feat in enumerate(features):
            if isinstance(feat, dict) and _HOLE_NOTE.search(str(feat.get("text") or "")):
                features[index] = {**feat, "text": note}
                replaced = True
                break
        if not replaced:
            features.append({"id": "f1", "type": "note", "text": note})
        next_ir["features"] = features
    constraints = []
    for item in next_ir.get("constraints") or []:
        if isinstance(item, dict) and item.get("kind") == "overall_dimension":
            constraints.append({**item, "size_mm": size})
        elif isinstance(item, dict):
            constraints.append(item)
    if not any(isinstance(item, dict) and item.get("kind") == "overall_dimension" for item in constraints):
        constraints.insert(0, {"id": "envelope", "kind": "overall_dimension", "size_mm": size, "tol_mm": 0.2})
    next_ir["constraints"] = constraints
    return next_ir


def validate_parameterized_source(source: str) -> tuple[bool, list[str]]:
    """Enforce the "every generated part is parameterized" rule.

    A part is only editable in the pane / via easycad_apply when its source
    declares a module-level ``PARAMS`` dict carrying at least ``length``,
    ``width`` and ``height``, AND the body of ``gen_step()`` actually reads one
    of those keys. Returns (ok, issues) where issues are human-readable reasons.

    This is a hard gate: ``cmd_write_gen`` refuses to build a part whose source
    does not pass, so no non-editable part can be produced by easycad_gen.
    """
    issues: list[str] = []
    text = source.replace("\r\n", "\n")
    parsed: dict | None = None
    block = _PARAMS.search(text)
    if block:
        try:
            candidate = ast.literal_eval(block.group(1))
            parsed = candidate if isinstance(candidate, dict) else None
        except (SyntaxError, ValueError):
            parsed = None
    if parsed is None:
        issues.append("缺少模块级 PARAMS = {...} 参数字典（每个生成零件必须有可编辑参数）")
    else:
        for key in ("length", "width", "height"):
            if parsed.get(key, None) is None:
                issues.append(f'PARAMS 字典缺少可编辑的 {key}（PARAMS["{key}"]=…）')
    body = text.split("def gen_step", 1)[1] if "def gen_step" in text else ""
    if body and "PARAMS[" not in body:
        issues.append("gen_step() 正文未引用 PARAMS[...]，改参数不会影响几何")
    return (not issues), issues


def load_ir(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))

