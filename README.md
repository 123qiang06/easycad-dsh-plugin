# EasyCAD — text-to-CAD plugin for DeepSeek Harness

`easycad` is an **out-of-tree plugin for [DeepSeek Harness](https://github.com/deepseek-ai/DeepSeek-Harness) (dsh)** that turns text prompts into real, parametric CAD. It uses the official dsh plugin contract (host tools + client slot + skill) and does not fork or patch the dsh source.

Ask for a part in plain language → the agent writes build123d Python, exports **STEP + GLB**, runs geometry **QA**, and opens a same-window CAD pane where you can tweak dimensions and re-generate.

![EasyCAD workbench](docs/easycad-hero.png)

## Features

- **Text → parametric CAD.** A `text-to-cad` skill drives brief → model → QA → export, locking `overall_size_mm` and `special_features` from the prompt.
- **CAD as code.** Every part is a `gen_step()` build123d script (`from build123d import *`), not a mesh, with overall dimensions in a `PARAMS` dict so it stays editable.
- **Built-in geometry QA.** `single_body`, `watertight`, and `overall_dimension` (0.2 mm tolerance) checks with pass/fail verdicts.
- **Same-window CAD pane.** A workbench opens on the right of the dsh UI — model tree, shaded 3D view, parameter sidebar, face inspector.
- **Parametric editing.** Change `length` / `width` / `height` / `hole_d` by clicking a face or via `easycad_apply`; STEP + GLB regenerate automatically.
- **Export.** STEP, STL, GLB (3MF planned).
- **Advisory similarity.** CAD-vs-reference-image silhouette IoU plus rule-based AI suggestions from QA + similarity + brief intent.

## Tools

| Tool | What it does |
|---|---|
| `easycad_brief` | Write an engineering IR + brief (envelope, features, `single_body`/`watertight`) |
| `easycad_gen` | Write `gen_step()`, export STEP+GLB, return `facts` + `qa`, open the pane |
| `easycad_inspect` | Re-measure geometry |
| `easycad_qa` | Pass/fail checks only |
| `easycad_measure` | Check named X/Y/Z overall sizes |
| `easycad_export` | STL / GLB from an existing part |
| `easycad_preview` | Show a part in the CAD pane |
| `easycad_params` | Read editable fields (`length`, `width`, `height`, `hole_d`) |
| `easycad_apply` | Apply param edits and regenerate STEP+GLB |
| `easycad_snapshot` | Render ortho view PNGs (iso/front/right/top) |
| `easycad_similarity` | CAD-vs-reference-image silhouette IoU (advisory) |
| `easycad_advice` | Rule-based suggestions from QA + similarity + IR |

Reserved (schema only, not implemented): `easycad_part` · `easycad_assemble` · `easycad_dfam`.

## How it works

```
prompt ─▶ text-to-cad skill
          ├─ easycad_brief   (lock overall_size_mm / special_features)
          ├─ easycad_gen     (build123d → STEP + GLB, facts + qa)
          ├─ [pane opens]    (QA issues, params, face inspector)
          └─ easycad_export  (STL / GLB)
```

Every generated `gen_step()` declares overall dims in a top-level `PARAMS` dict and references `PARAMS[...]` in the body — that's what keeps parts editable in the pane and via `easycad_apply`.

## Requirements

- Node.js 22
- A Python interpreter with `build123d` (and `numpy`; `PIL`/`trimesh` for image features)
- A dsh Web environment with your DeepSeek API key

## Install (native dsh)

The dsh package name is `easycad`; make it resolvable and patch one row.

```bash
# 1. Make the package resolvable
#    $DSH_HOME/profiles/node_modules/easycad  ->  <this-repo>/easycad/dsh-plugin

# 2. Point the CAD interpreter at a build123d-enabled Python
export EASYCAD_PYTHON=/path/to/python-with-build123d

# 3. Patch one row in your dsh patch file
#    - insert: { id: easycad, name: easycad }
```

## License

MIT — see [LICENSE](LICENSE).
