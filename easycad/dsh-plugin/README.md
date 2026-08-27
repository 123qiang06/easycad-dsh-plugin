# EasyCAD dsh plugin

Official DeepSeek Harness plugin contract: host tools + client slots + skill.
Does not fork or patch dsh source.

- Host: `easycad_*` tools and `/easycad` routes
- Client: `tool.call.toolview` cards and `shell.overlay` CAD pane
- Skill: `.dsh/skills/text-to-cad`

## Native dsh

1. Make package name `easycad` resolvable from `$DSH_HOME/profiles` (add a dependency, or a link in `$DSH_HOME/profiles/node_modules/easycad`).
2. Patch one row: `{ id: easycad, name: easycad }`.
3. Point `EASYCAD_PYTHON` at an interpreter with build123d.

EasyCAD's start script does the local junction + `--patch` for this repo:

```powershell
powershell -File scripts\start-dsh-web.ps1
```

Open http://127.0.0.1:3080
