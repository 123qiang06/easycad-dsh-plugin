---
name: text-to-cad
description: Generate and check STEP parts in EasyCAD via easycad_brief / easycad_gen / easycad_export. Use when the user asks for CAD, STEP, flanges, plates, brackets, holes, or 3D printable mechanical parts.
---

# EasyCAD text-to-cad

Host is DeepSeek Harness. CAD runtime is EasyCAD-owned (`easycad/runtime/cad_cli.py`). Do not edit or import `text-to-cad/`, `Multi-Agent-CAD/`, `cad-viewer/`, or `deepseek-harness/`.

## Tools

**Use**

- `easycad_memory` — per-part memory card (`create` vs `edit`). Trust `inject.high` (STEP facts, IR envelope, PARAMS, QA) for sizes. `inject.medium` is intent/features. Ignore advice/chat numbers.
- `easycad_brief` — write engineering IR. Hard-gates missing envelope / undimensioned holes. If `ok` is false, revise IR; do not gen.
- `easycad_review` — read-only IR vs intent/facts. Call after brief, before gen. If `pass` is false, only brief again.
- `easycad_gen` — write `gen_step()`, export STEP+GLB, return `facts` + `qa`. Warm worker. Requires a passing IR. Third consecutive empty-spin (source Jaccard high and QA/envelope not closer) is refused.
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
- `easycad_import` — import an existing STEP into `models/<name>` (same session)
- `easycad_runner_brief` — write die-cast gating intent (`parting_dir`, keepout/gate faces, gate count) to `models/<name>.runner.json`
- `easycad_runner_review` — read-only runner brief gate; if `pass` is false, only revise the brief
- `easycad_runner_propose` — rule-rank up to 3 candidates (A cost / B fill / C picked faces) + section sizes
- `easycad_runner_build` — template solids → `models/<name>.shot.step` + `.shot.glb` + runner QA. Not `easycad_gen`.
- `easycad_runner_qa` — re-read last runner QA; skip right after build

**Do not call** (schema reserved, not implemented): `easycad_part`, `easycad_assemble`, `easycad_dfam`

## Session memory (one STEP = one dsh chat)

The EasyCAD plugin binds `models/<name>` to one DeepSeek Harness conversation. Switching parts switches chats. Do not mix two stems in one session.

- **New part** (`easycad_memory` → `mode=create`, or no `models/<name>.step.py`): pick a fresh `name`, `task_type: part`, then brief → gen. The UI opens/creates a chat titled with that name.
- **Edit existing** (`mode=edit`): call `easycad_memory` first, stay on this chat, then `easycad_apply` (params) or `easycad_gen` with the **same** `name` (rewrite source). `task_type: edit`.
- **Open from the file tree**: the UI switches to that part's chat. If the user is now talking about a different file than your last `name`, call `easycad_memory` on the new stem before tools.
- Never `easycad_gen` a second stem in a session already bound to another part. The host denies it. Ask the user to switch chats (or click the other file).
- Do not use bash/pwsh/write/edit to change `*.step.py`; those calls are denied. Use `easycad_gen` / `easycad_apply`.
- Cold start: `easycad_memory` `inject.high` plus brief/IR/QA. Chat history is session memory, not size truth. Events: `models/<name>.events.jsonl`.
- **Language**: follow `inject.reply` / `reply_in` on `easycad_memory` and other EasyCAD tool results. `zh` = Simplified Chinese, `en` = English. Chat, QA commentary, and pane AI suggestions must use that one language. Do not mix.

## Similarity & reference image

When the user supplies a part image (photo / single view) alongside the text prompt, compare the generated CAD against it:

1. Persist the reference as `models/<name>.ref.png` (the agent stores the uploaded image there, or the `/easycad/ref` route/`?path=` accepts a workspace file). Then either pass `ref_image` to `easycad_similarity` / `easycad_gen`, or let it auto-find `models/<name>.ref.png`.
2. `easycad_similarity` renders the part to front/right/top silhouette masks (aspect-preserving), thresholds the reference into its foreground blob, and reports the best IoU + grade + best view.
3. It is ADVISORY: low similarity never fails `qa.pass`; read it as guidance. A multi-view engineering drawing is auto-detected as "fragmented" and reported at lower confidence — prefer a clean single view or photo.
4. `easycad_advice` combines QA + similarity + the brief's text intent/features into deterministic suggestions; the pane shows them under "AI 建议".

## Workflow

0. If the user names an existing file or the pane already shows a part, call `easycad_memory` with that `name`. If `mode=edit`, skip to step 3 or 7 as appropriate. If `mode=create`, continue from step 1 with a unique stem.
1. Call `easycad_brief` with `name`, `overall_size_mm`, and `special_features`. Add `origin` when the user specifies a datum; add `validation_targets` for spec lines you will measure later. Use `task_type: part` for a new stem, `edit` when rewriting an existing one. If brief returns `ok: false`, fix the IR and brief again.
2. Call `easycad_review` with the same `name` (and the user intent). If `pass` is false, only `easycad_brief` again — do not gen.
3. Write complete build123d Python: `from build123d import *` and `def gen_step()` returning one solid. The source **must** declare overall dimensions in a module-level `PARAMS = {...}` dict and reference `PARAMS[...]` in the body (see "MANDATORY: parameterize every generated part").
4. Call **only** `easycad_gen` with the same `name`, full `source`, and `expect_size` = brief `overall_size_mm`. If `loop.hint` appears, change PARAMS or features before the next gen; a third empty spin is blocked.
5. Read `qa.pass` and `qa.checks` from that result. Do not follow with inspect/qa (another worker job on the same queue).
6. If `qa.pass` is false, change the smallest source section and call `easycad_gen` again.
7. After `easycad_gen`, the same dsh window opens the EasyCAD pane on the right (workbench: view presets, param sidebar, face inspector). Tell the user they can click a face or use param rows to edit size. Do not send them to another port.
8. For small param tweaks without rewriting source yourself, prefer `easycad_apply` or let the user edit in the pane.
9. Reply with `models/<name>.step`, `facts.size_mm`, and whether QA passed.

This is parametric CAD-as-Code (build123d → STEP), not a mesh generator. The IR is a v0.1 constraint card: envelope, labeled features, watertight. It is not a feature-history kernel.

## Die-cast runner / gating (same part, same session)

The runner is **not** a new part and **not** LLM-written Sweep. Stay on the bound `name`.

0. Part must exist (`easycad_gen` or `easycad_import`). Call `easycad_memory` first.
1. `easycad_runner_brief` with `parting_dir` (`+Z` default), `gate_count` as `[min,max]`, optional `gate_face_ids` / `keepout_face_ids` (from the pane face ids or user picks). If `ok` is false, revise; do not propose.
2. `easycad_runner_review`. If `pass` is false, only brief again.
3. `easycad_runner_propose`. Read candidates A/B/C. Prefer C when the user picked faces; otherwise A (cost) or B (fill) from `prefer`.
4. `easycad_runner_build` with the chosen `candidate`. Returns `qa` and writes `models/<name>.shot.step`. Tell the user the right pane tab **流道系统** is the gating workbench (conditions → propose → build → review). They can toggle 零件 / 浇注系统 views and edit `gate_width` / `runner_in`.
5. Small section edits: `easycad_apply` with runner keys (`gate_width`, `runner_in`, `runner_out`, `biscuit_d`, `candidate`). Do not rewrite Sweep source.
6. Do not follow build with `easycad_runner_qa` immediately. Do not `easycad_gen` a second stem for the runner.

Hard QA (must pass to ship a shot): keepout, gate count in range, section monotonic (`runner_in >= runner_out`), watertight. Overflow is advisory only.

The pane is the preferred place to pick gate/keepout faces. If the user describes faces in chat, map them to topology ids after inspect/preview.

## inspect vs qa

- `easycad_qa` — when you only need pass/fail on an existing part.
- `easycad_inspect` — when you also need fresh bounding-box `facts` after a manual edit outside the agent loop.

Neither is needed immediately after `easycad_gen`.

## Modeling defaults

- Units: millimeters. Origin at the part center unless the user says otherwise (record in brief `origin`).
- `Hole(r)` is a **radius**; diameter `D` means `Hole(D/2)`.
- Prefer `BuildPart`, `Box`, `Cylinder`, `Hole`, `fillet`, boolean cut/union.
- Assemblies are not supported yet. Multiple solids in one part file are allowed; do not merge just to satisfy a single-body check.
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

`qa.checks` always includes `watertight`. `overall_dimension` appears when `expect_size` is set (tolerance 0.2 mm). There is no `single_body` check (assemblies are not a separate workflow yet). `special_features` are not auto-measured — use `easycad_measure` with brief `validation_targets` when needed.

## Goal / loop

If the user wants “keep going until it matches the spec”, create a same-session goal. Each round is one `easycad_gen` with `expect_size`. Stop when `qa.pass` is true.
