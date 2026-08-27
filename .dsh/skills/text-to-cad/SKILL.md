---
name: text-to-cad
description: Generate and check STEP parts in EasyCAD via easycad_brief / easycad_gen / easycad_export. Use when the user asks for CAD, STEP, flanges, plates, brackets, holes, or 3D printable mechanical parts.
---

# EasyCAD text-to-cad

Host is DeepSeek Harness. CAD runtime is EasyCAD-owned (`easycad/runtime/cad_cli.py`). Do not edit or import `text-to-cad/`, `Multi-Agent-CAD/`, `cad-viewer/`, or `deepseek-harness/`.

## Tools

**Use**

- `easycad_brief` — write engineering IR to `models/<name>.ir.json` (plus a short `.brief.json`). Optional: `task_type`, `origin`, `validation_targets`.
- `easycad_gen` — write `gen_step()`, export STEP+GLB, return `facts` + `qa`. The in-page CAD pane opens on the right.
- `easycad_params` — read editable fields (`length`, `width`, `height`, `hole_d`) without opening the viewer.
- `easycad_apply` — change params programmatically; regenerates STEP+GLB like clicking a face in the pane.
- `easycad_export` — STL / GLB from an existing part
- `easycad_preview` — show an existing part in the same-window CAD pane
- `easycad_measure` — check named X/Y/Z overall sizes (use `validation_targets` from brief when present)
- `easycad_inspect` — re-measure geometry + return fresh `facts` and `qa` (skip right after `easycad_gen`)
- `easycad_qa` — pass/fail checks only; skip right after `easycad_gen`
- `easycad_snapshot` — render PNG orthographic (iso/front/right/top) views to `models/<name>.view_*.png`
- `easycad_similarity` — CAD-vs-reference-image silhouette IoU; pass `ref_image` (or auto-use `models/<name>.ref.png`); writes `models/<name>.similarity.json`
- `easycad_advice` — rule-based AI suggestions from QA + similarity + IR; writes `models/<name>.advice.json`

**Do not call** (schema reserved, not implemented): `easycad_part`, `easycad_assemble`, `easycad_dfam`

## Similarity & reference image

When the user supplies a part image (photo / single view) alongside the text prompt, compare the generated CAD against it:

1. Persist the reference as `models/<name>.ref.png` (the agent stores the uploaded image there, or the `/easycad/ref` route/`?path=` accepts a workspace file). Then either pass `ref_image` to `easycad_similarity` / `easycad_gen`, or let it auto-find `models/<name>.ref.png`.
2. `easycad_similarity` renders the part to front/right/top silhouette masks (aspect-preserving), thresholds the reference into its foreground blob, and reports the best IoU + grade + best view.
3. It is ADVISORY: low similarity never fails `qa.pass`; read it as guidance. A multi-view engineering drawing is auto-detected as "fragmented" and reported at lower confidence — prefer a clean single view or photo.
4. `easycad_advice` combines QA + similarity + the brief's text intent/features into deterministic suggestions; the pane shows them under "AI 建议".

## Workflow

1. Call `easycad_brief` with `name`, `overall_size_mm`, and `special_features`. Add `origin` when the user specifies a datum; add `validation_targets` for spec lines you will measure later.
2. Write complete build123d Python: `from build123d import *` and `def gen_step()` returning one solid. The source **must** declare overall dimensions in a module-level `PARAMS = {...}` dict and reference `PARAMS[...]` in the body (see "MANDATORY: parameterize every generated part").
3. Call **only** `easycad_gen` with the same `name`, full `source`, and `expect_size` = brief `overall_size_mm`.
4. Read `qa.pass` and `qa.checks` from that result. Do not follow with inspect/qa (another ~25s Python start).
5. If `qa.pass` is false, change the smallest source section and call `easycad_gen` again.
6. After `easycad_gen`, the same dsh window opens the EasyCAD pane on the right (workbench: view presets, param sidebar, face inspector). Tell the user they can click a face or use param rows to edit size. Do not send them to another port.
7. For small param tweaks without rewriting source yourself, prefer `easycad_apply` or let the user edit in the pane.
8. Reply with `models/<name>.step`, `facts.size_mm`, and whether QA passed.

This is parametric CAD-as-Code (build123d → STEP), not a mesh generator. The IR is a v0.1 constraint card: envelope, labeled features, single_body/watertight. It is not a feature-history kernel.

## inspect vs qa

- `easycad_qa` — when you only need pass/fail on an existing part.
- `easycad_inspect` — when you also need fresh bounding-box `facts` after a manual edit outside the agent loop.

Neither is needed immediately after `easycad_gen`.

## Modeling defaults

- Units: millimeters. Origin at the part center unless the user says otherwise (record in brief `origin`).
- `Hole(r)` is a **radius**; diameter `D` means `Hole(D/2)`.
- Prefer `BuildPart`, `Box`, `Cylinder`, `Hole`, `fillet`, boolean cut/union.
- One solid unless they ask for an assembly.
- `expect_size` / `overall_size_mm` is the axis-aligned bounding box, not a hole diameter.

### MANDATORY: parameterize every generated part

Every `gen_step()` source written by `easycad_gen` **must** declare its overall dimensions in a top-level `PARAMS = {...}` dict, and every dimension used in the body must reference `PARAMS[...]` (never a bare literal or a local variable like `L = 190.0`). This is what lets the in-pane editor and `easycad_apply` offer editable fields (`length` / `width` / `height` / `hole_d`).

Required shape:

```python
from build123d import *

PARAMS = {
    "length": 50.0,   # X overall (mm)
    "width": 100.0,   # Y overall (mm)
    "height": 12.0,   # Z overall (mm)
    "hole_d": 20.0,   # optional: through-hole diameter (mm)
}

def gen_step():
    with BuildPart() as part:
        Box(PARAMS["length"], PARAMS["width"], PARAMS["height"])
        Hole(PARAMS["hole_d"] / 2)          # omit this line if there is no hole
    return part.part
```

Rules:
- Always include `length`, `width`, `height`; include `hole_d` only when the part has a central through-hole.
- Every `Box(...)`, `Cylinder(...)`, translate, or sketch size that is a design dimension must come from `PARAMS[...]`, so the editable fields actually drive the geometry.
- Keep the constant `PARAMS` block at module scope (before `def gen_step()`), exactly three keys minimum.
- If the model is repeated geometry (pads, studs, teeth, ribs), also expose the repeat count and per-unit sizes in `PARAMS` (e.g. `n_pads`, `pad_len`) so the count/size stay editable.

This guarantees every part is editable in the pane and via `easycad_apply`. Never return a `gen_step()` that hard-codes dimensions with bare numbers or local variables; that produces a non-editable part (the pane then reports "该零件未参数化").

## QA

`qa.checks` always includes `single_body` and `watertight`. `overall_dimension` appears when `expect_size` is set (tolerance 0.2 mm). `special_features` are not auto-measured — use `easycad_measure` with brief `validation_targets` when needed.

## Goal / loop

If the user wants “keep going until it matches the spec”, create a same-session goal. Each round is one `easycad_gen` with `expect_size`. Stop when `qa.pass` is true.
