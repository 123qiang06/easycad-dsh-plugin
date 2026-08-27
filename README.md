# EasyCAD — text-to-CAD plugin for DeepSeek Harness

`easycad` is an **out-of-tree plugin for [DeepSeek Harness](https://github.com/deepseek-ai/DeepSeek-Harness) (dsh)** that turns text prompts into real, parametric CAD. It uses the **official dsh plugin contract** (host tools + client slots + skill) and does **not** fork or patch the dsh source.

Ask for a part in plain language → the agent writes build123d Python, exports **STEP + GLB**, runs geometry **QA**, and opens a same-window CAD pane where you can tweak dimensions and re-generate.

![EasyCAD workbench](docs/easycad-hero.png)

## What it gives you

- **Text → parametric CAD.** A `text-to-cad` skill guides the agent through brief → model → QA → export, with `overall_size_mm` and `special_features` locked from the prompt.
- **Real CAD-as-code.** Every part is a `gen_step()` build123d script (`from build123d import *`), not a mesh. All overall dimensions live in a module-level `PARAMS` dict, so the part is editable later.
- **Built-in geometry QA.** `single_body`, `watertight`, and `overall_dimension` (tolerance 0.2 mm) checks with a pass/fail verdict.
- **Same-window CAD pane.** The dsh Web UI opens a workbench on the right (official `shell.overlay` slot) — model tree, shaded 3D view, param sidebar, face inspector.
- **Parametric editing.** Change `length` / `width` / `height` / `hole_d` programmatically (`easycad_apply`) or by clicking a face in the pane; STEP + GLB regenerate automatically.
- **Export for printing / preview.** STEP, STL, GLB. (3MF is planned.)
- **Advisory CAD-vs-image similarity.** Compare a generated part against a reference image via silhouette IoU, plus deterministic AI suggestions from QA + similarity + the brief intent.

## The workbench pane

The in-page pane (right side of the dsh window) gives you:

- **3D 模型** — a model tree of every `models/` part (`.glb` / `.step`).
- **Center view** — shaded orthographic render: 等轴 / 正视 / 侧视 / 右视 / 重置 presets; drag to rotate, click a face to select, or use the param rows.
- **AI 建议** — rule-based suggestions from QA + similarity + IR (deterministic, no model call) behind an "生成零件后自动生成 AI 建议" toggle.
- **Issues** — the QA result: `overall_dimension`, `single_body`, `watertight`.
- **Tree** — feature tree.
- **Parameters** — editable `长度` / `宽度` / `厚度` rows driven by the part's `PARAMS`.
- **选中面** — pick a face in 3D to inspect it.
- **编辑** — 选中面或参数行来改尺寸.
- **Metadata** — links to the GLB / STEP / source.

## Tools

| Tool | What it does |
|---|---|
| `easycad_brief` | Write an engineering IR + brief (envelope, features, `single_body`/`watertight`) |
| `easycad_gen` | Write `gen_step()`, export STEP+GLB, return `facts` + `qa`; opens the pane |
| `easycad_inspect` | Re-measure geometry, fresh `facts` + `qa` |
| `easycad_qa` | Pass/fail checks only |
| `easycad_measure` | Check named X/Y/Z overall sizes (`validation_targets`) |
| `easycad_export` | STL / GLB from an existing part |
| `easycad_preview` | Show a part in the same-window CAD pane |
| `easycad_params` | Read editable fields (`length`, `width`, `height`, `hole_d`) |
| `easycad_apply` | Apply param edits; regenerate STEP+GLB |
| `easycad_snapshot` | Render ortho view PNGs (iso/front/right/top) |
| `easycad_similarity` | CAD-vs-reference-image silhouette IoU (advisory) |
| `easycad_advice` | Rule-based AI suggestions from QA + similarity + IR |

Reserved (schema only, not implemented yet): `easycad_part` · `easycad_assemble` · `easycad_dfam`.

## How it works

```
you prompt ─▶ text-to-cad skill
              ├─ easycad_brief   (lock overall_size_mm / special_features)
              ├─ easycad_gen     (build123d -> STEP + GLB, facts + qa)
              ├─ [pane opens]    (QA issues, param sidebar, face inspector)
              └─ easycad_export  (STL / GLB)
```

Every generated `gen_step()` **must** declare overall dims in a top-level `PARAMS` dict and reference `PARAMS[...]` in the body — that's what makes parts editable in the pane and via `easycad_apply`.

## Requirements

- Node.js 22
- A Python interpreter with `build123d` (and `numpy`; `PIL`/`trimesh` for image features)
- A dsh Web environment (DeepSeek Harness) with your DeepSeek API key

## Install (native dsh)

The dsh package name is `easycad`; make it resolvable and patch one row.

```bash
# 1. Make the package resolvable (link or add a dependency)
#    $DSH_HOME/profiles/node_modules/easycad  ->  <this-repo>/easycad/dsh-plugin

# 2. Point the CAD interpreter at a build123d-enabled Python
export EASYCAD_PYTHON=/path/to/python-with-build123d

# 3. Patch one row in your dsh patch file
#    - insert: { id: easycad, name: easycad }
```

The original EasyCAD workspace drives this with a start script (`scripts/start-dsh-web.ps1`). Then open the dsh Web UI and use the `text-to-cad` skill.

## Declaring this as an "EasyCAD plugin" in the community

Two different mechanisms — do both, in this order.

### 1. Topics on *your* repo (primary, immediate)

Add to the repo **About** section (gear → Add topics; or About → pencil). Up to 20, all lowercase, hyphen-separated, no spaces.

| Purpose | Topics |
|---|---|
| Host / framework | `deepseek-harness`, `dsh` |
| Function | `text-to-cad`, `cad`, `step`, `parametric-cad` |
| Tech stack | `build123d`, `opencascade`, `python` |
| Category | `ai-agents`, `llm-tools`, `plugin`, `easycad` |

Suggested **repo description** (About):
> Out-of-tree plugin for DeepSeek Harness (dsh) that turns text prompts into parametric CAD (STEP/GLB) via build123d.

### 2. Curated topic pages via github/explore (optional, long-term)

[github/explore](https://github.com/github/explore) curates the landing page for a topic itself (`topics/<topic>/index.md` front matter: `topic`, `short_description`, `aliases`, `related`, `wikipedia`, `display_name`, `created_by`, `releases`, plus a logo) — see [`CONTRIBUTING.md`](https://github.com/github/explore/blob/main/CONTRIBUTING.md). It does **not** advertise your repo.

- Skip `easycad` — a brand-new/private topic won't be curated.
- Worth improving there (fork → add/edit `topics/<name>/index.md` → PR): `build123d`, `step`, `cad`, `deepseek-harness`.

## License

MIT — see [LICENSE](LICENSE).
