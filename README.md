# EasyCAD — text-to-CAD plugin for DeepSeek Harness

`easycad` is an **out-of-tree plugin for [DeepSeek Harness](https://github.com/deepseek-ai/DeepSeek-Harness) (dsh)** that turns text prompts into parametric CAD. It uses the **official dsh plugin contract** (host tools + client slots + skill) and does **not** fork or patch dsh source.

It writes real **build123d** Python, exports **STEP + GLB**, runs geometry **QA**, and opens a same-window CAD pane in the dsh Web UI.

## What this is

- **Host layer** — `easycad_*` tools + `/easycad` routes
- **Client layer** — tool-view cards + the `shell.overlay` CAD pane (official dsh slots)
- **Skill layer** — `.dsh/skills/text-to-cad`
- **CAD runtime** — build123d → STEP/GLB via `easycad/runtime/cad_cli.py`

## Declaring this as an "EasyCAD plugin" in the community

A repo is "declared" on GitHub in two different ways. Do both, in this order.

### 1. Topics on *your* repo (primary, immediate)

Add these to the repo **About** section (gear → Add topics; or About → pencil). Up to 20, all lowercase, hyphen-separated, no spaces. Topics make the repo show up in search and on `github.com/topics/<topic>` pages.

| Purpose | Topics |
|---|---|
| Host / framework | `deepseek-harness`, `dsh` |
| Function | `text-to-cad`, `cad`, `step`, `parametric-cad` |
| Tech stack | `build123d`, `opencascade`, `python` |
| Category | `ai-agents`, `llm-tools`, `plugin`, `easycad` |

Suggested **repo description** (About):
> Out-of-tree plugin for DeepSeek Harness (dsh) that turns text prompts into parametric CAD (STEP/GLB) via build123d.

### 2. Curated topic pages via github/explore (optional, long-term)

[github/explore](https://github.com/github/explore) does **not** advertise your repo — it curates the landing page for a *topic* itself (`topics/<topic>/index.md` front matter: `topic`, `short_description`, `aliases`, `related`, `wikipedia`, `display_name`, `created_by`, `releases`, plus a logo). See [`CONTRIBUTING.md`](https://github.com/github/explore/blob/main/CONTRIBUTING.md).

- Skip `easycad` — a brand-new/private topic with few repos won't be curated.
- Worth improving there (fork → add/edit `topics/<name>/index.md` → PR): `build123d`, `step`, `cad`, `deepseek-harness`.

## Requirements

- Node.js 22
- A Python interpreter with `build123d` installed
- A dsh Web environment (DeepSeek Harness) with your DeepSeek API key

## Install (native dsh)

The package name is `easycad`; make it resolvable from dsh and patch one row.

```bash
# 1. Make the package resolvable (link or add a dependency)
#    $DSH_HOME/profiles/node_modules/easycad  ->  <this-repo>/easycad/dsh-plugin

# 2. Point the CAD interpreter at a build123d-enabled Python
export EASYCAD_PYTHON=/path/to/python-with-build123d

# 3. Patch one row in your dsh patch file
#    - insert: { id: easycad, name: easycad }
```

The original repo drives this with a start script; see [`start-dsh-web.ps1`](scripts/start-dsh-web.ps1) in the full EasyCAD workspace.

Open the dsh Web UI and use the `text-to-cad` skill.

## Tools

`easycad_brief` · `easycad_gen` · `easycad_inspect` · `easycad_qa` · `easycad_measure` · `easycad_export` · `easycad_preview` · `easycad_params` · `easycad_apply` · `easycad_snapshot` · `easycad_similarity` · `easycad_advice`

Reserved (schema only, not implemented): `easycad_part` · `easycad_assemble` · `easycad_dfam`

## License

MIT — see [LICENSE](LICENSE).
