"""EasyCAD engineering IR v0.1 — constraint language, not a mesh dump.

This is a first demo of an intermediate representation between
natural-language intent and CAD-as-Code (build123d). It is not a
feature-history kernel and does not claim manufacturability.
"""

from __future__ import annotations

from typing import Any


IR_VERSION = "0.1"


def build_ir(name: str, payload: dict[str, Any]) -> dict[str, Any]:
    size = payload.get("overall_size_mm") or payload.get("envelope", {}).get("size_mm")
    if not isinstance(size, list) or len(size) != 3:
        raise ValueError("IR requires overall_size_mm / envelope.size_mm as [X, Y, Z]")
    size_mm = [float(v) for v in size]
    tol = float(payload.get("tol_mm") or 0.2)
    intent = str(payload.get("intent") or payload.get("notes") or "")
    features = _features(payload)
    ir = {
        "ir_version": IR_VERSION,
        "kind": payload.get("kind") or "part",
        "name": name,
        "units": payload.get("units") or "mm",
        "intent": intent,
        "envelope": {"size_mm": size_mm},
        "features": features,
        "constraints": [
            {
                "id": "envelope",
                "kind": "overall_dimension",
                "size_mm": size_mm,
                "tol_mm": tol,
                "note": "Axis-aligned bounding box. Feature sizes are not this field.",
            },
            {
                "id": "watertight",
                "kind": "watertight",
                "required": bool(payload.get("watertight", True)),
            },
        ],
        "manufacturing": {
            "process": payload.get("process") or "unspecified",
            "notes": list(payload.get("manufacturing_notes") or []),
        },
        "trace": {
            "source": f"models/{name}.step.py",
            "step": f"models/{name}.step",
            "glb": f"models/{name}.glb",
            "brief": f"models/{name}.brief.json",
        },
        "limitations": [
            "Geometry-valid is not engineering-valid (stress, tolerance, mates).",
            "IR v0.1 has no feature-history / rollback graph.",
            "Named features are intent labels until measure/selectors exist.",
        ],
    }
    meta = {}
    if payload.get("task_type"):
        meta["task_type"] = str(payload["task_type"])
    if payload.get("origin"):
        meta["origin"] = str(payload["origin"])
    targets = payload.get("validation_targets")
    if isinstance(targets, list) and targets:
        meta["validation_targets"] = targets
    if meta:
        ir["meta"] = meta
    return ir


def _features(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw = payload.get("features")
    if isinstance(raw, list) and raw:
        out = []
        for index, item in enumerate(raw):
            if isinstance(item, str):
                out.append({"id": f"f{index + 1}", "type": "note", "text": item})
            elif isinstance(item, dict):
                feat = dict(item)
                feat.setdefault("id", f"f{index + 1}")
                feat.setdefault("type", "note")
                out.append(feat)
            else:
                raise ValueError("features entries must be objects or strings")
        return out
    notes = payload.get("special_features") or []
    if not isinstance(notes, list):
        raise ValueError("special_features must be an array")
    return [{"id": f"f{i + 1}", "type": "note", "text": str(text)} for i, text in enumerate(notes)]


def brief_view(ir: dict[str, Any]) -> dict[str, Any]:
    view = {
        "name": ir["name"],
        "units": ir.get("units") or "mm",
        "overall_size_mm": ir["envelope"]["size_mm"],
        "watertight": True,
        "special_features": [
            item.get("text") or item.get("id")
            for item in ir.get("features") or []
            if isinstance(item, dict)
        ],
        "notes": ir.get("intent") or "",
        "ir_path": f"models/{ir['name']}.ir.json",
    }
    meta = ir.get("meta")
    if isinstance(meta, dict):
        for key in ("task_type", "origin", "validation_targets"):
            if key in meta:
                view[key] = meta[key]
    return view
