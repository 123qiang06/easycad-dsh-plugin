/**
 * EasyCAD out-of-tree dsh plugin. Registers CAD tools; does not live in
 * deepseek-harness / text-to-cad / Multi-Agent-CAD.
 */
import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { homedir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { attachCadRoutes } from './routes.js'

export const name = 'easycad'
export const inject = ['tools', 'webServer']

const PLUGIN_DIR = dirname(fileURLToPath(import.meta.url))
const EASYCAD_ROOT = join(PLUGIN_DIR, '..', '..')
const CLI = join(EASYCAD_ROOT, 'easycad', 'runtime', 'cad_cli.py')

function pythonBin() {
  if (process.env.EASYCAD_PYTHON) return process.env.EASYCAD_PYTHON
  const home = homedir()
  const candidates = [
    join(home, 'anaconda3', 'envs', 'multi_agent_cad', 'python.exe'),
    join(home, 'miniconda3', 'envs', 'multi_agent_cad', 'python.exe'),
  ]
  for (const path of candidates) {
    if (existsSync(path)) return path
  }
  return process.platform === 'win32' ? 'python.exe' : 'python'
}

function runCli(cliArgs, signal, stdinText) {
  return new Promise((resolve, reject) => {
    const child = spawn(pythonBin(), [CLI, ...cliArgs], {
      cwd: EASYCAD_ROOT,
      env: { ...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' },
      windowsHide: true,
    })
    let stdout = ''
    let stderr = ''
    const onAbort = () => {
      child.kill()
    }
    if (signal) {
      if (signal.aborted) child.kill()
      else signal.addEventListener('abort', onAbort, { once: true })
    }
    if (stdinText != null) {
      child.stdin.on('error', () => {})
      child.stdin.end(stdinText, 'utf8')
    } else {
      child.stdin.end()
    }
    child.stdout.on('data', (chunk) => {
      stdout += chunk.toString('utf8')
    })
    child.stderr.on('data', (chunk) => {
      stderr += chunk.toString('utf8')
    })
    child.on('error', reject)
    child.on('close', (code) => {
      if (signal) signal.removeEventListener('abort', onAbort)
      const line = stdout.trim().split(/\r?\n/).filter(Boolean).at(-1) || ''
      try {
        resolve(JSON.parse(line))
      } catch {
        reject(new Error(stderr.trim() || stdout.trim() || `cad_cli exited ${code}`))
      }
    })
  })
}

function textResult(value) {
  return [{ type: 'text', text: JSON.stringify(value, null, 2) }]
}

// ---- persistent build123d worker -------------------------------------------
// `import build123d` (OCP/OpenCASCADE) costs ~14s per process. Spawning a fresh
// cad_cli per op makes apply/gen ~17s; a long-lived worker pays that import once
// and then serves newline-delimited jobs in ~1-2s. The worker is reused across
// calls and respawns if it ever exits.
let workerChild = null
let workerTail = Promise.resolve()

function spawnWorkerChild() {
  workerChild = spawn(pythonBin(), [CLI, 'worker'], {
    cwd: EASYCAD_ROOT,
    env: { ...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' },
    windowsHide: true,
    stdio: ['pipe', 'pipe', 'pipe'],
  })
  /* v8 ignore next -- stderr only carries import warnings; results go to stdout. */
  workerChild.stderr.on('data', () => {})
  return workerChild
}

function workerJob(job, timeoutMs = 180000) {
  const run = () => new Promise((resolve, reject) => {
    let child = workerChild
    if (!child || child.exitCode !== null) child = spawnWorkerChild()
    let buf = ''
    let settled = false
    const cleanup = () => { child.stdout.removeListener('data', onData) }
    const onData = (chunk) => {
      buf += chunk.toString('utf8')
      const nl = buf.indexOf('\n')
      if (nl < 0) return
      const line = buf.slice(0, nl).trim()
      buf = buf.slice(nl + 1)
      if (!settled) { settled = true; cleanup(); clearTimeout(timer); resolve(JSON.parse(line)) }
    }
    const timer = setTimeout(() => {
      if (settled) return
      settled = true; cleanup()
      try { child.kill() } catch {}
      workerChild = null
      reject(new Error('build123d worker timed out'))
    }, timeoutMs)
    child.stdout.on('data', onData)
    child.stdin.write(JSON.stringify(job) + '\n')
  })
  const p = workerTail.then(run, run)
  workerTail = p.catch(() => {})
  return p
}

function unimplemented(name, note) {
  return {
    ok: false,
    unimplemented: true,
    error: `${name} is reserved and not implemented yet. ${note}`,
  }
}

const nameSchema = {
  type: 'string',
  description: 'Part stem. Files live under models/<name>.*',
}

const sizeSchema = {
  type: 'array',
  description: 'Axis-aligned bounding box in mm [X, Y, Z].',
  items: { type: 'number' },
  minItems: 3,
  maxItems: 3,
}

// dsh output schemas reject minItems/maxItems (parameters still allow them).
const sizeOutSchema = {
  type: 'array',
  description: 'Axis-aligned bounding box in mm [X, Y, Z].',
  items: { type: 'number' },
}

const qaSchema = {
  type: 'object',
  description: 'Geometry checks: overall_dimension (if expect_size given), single_body, watertight.',
  additionalProperties: true,
  properties: {
    pass: { type: 'boolean' },
    tolerance_mm: { type: 'number' },
    checks: { type: 'array', items: { type: 'object' } },
  },
}

const factsSchema = {
  type: 'object',
  additionalProperties: true,
  properties: {
    size_mm: sizeOutSchema,
    center_mm: sizeOutSchema,
    volume_mm3: { type: 'number' },
    solid_count: { type: 'integer' },
    is_valid: { type: 'boolean' },
  },
}

function outputSchema(required, properties) {
  return {
    schema: {
      type: 'object',
      additionalProperties: true,
      required,
      properties: { ok: { type: 'boolean' }, error: { type: 'string' }, ...properties },
    },
    render: (_args, value) => textResult(value),
  }
}

function viewerUrl(name) {
  return `/easycad/view?name=${encodeURIComponent(name)}`
}

export function apply(ctx) {
  attachCadRoutes(ctx, {
    modelsDir: join(EASYCAD_ROOT, 'models'),
    previewDir: join(EASYCAD_ROOT, 'easycad', 'preview'),
    pythonBin: pythonBin(),
    cli: CLI,
    easycadRoot: EASYCAD_ROOT,
    workerJob,
  })

  ctx.tools.register({
    name: 'easycad_brief',
    description:
      'Save a structured CAD brief to models/<name>.brief.json before writing code. '
      + 'Use when the user describes a part: lock overall_size_mm and special_features, then call easycad_gen with the same expect_size.',
    parameters: {
      type: 'object',
      additionalProperties: false,
      required: ['name', 'overall_size_mm'],
      properties: {
        name: nameSchema,
        overall_size_mm: sizeSchema,
        units: { type: 'string', description: 'Length unit. Default mm.' },
        single_body: { type: 'boolean', description: 'Whether QA should require exactly one solid. Default true.' },
        watertight: { type: 'boolean', description: 'Whether QA should require a valid closed solid. Default true.' },
        special_features: {
          type: 'array',
          items: { type: 'string' },
          description: 'Intent labels stored on the engineering IR, e.g. "4 holes on PCD 50".',
        },
        intent: { type: 'string', description: 'One-line design intent for the IR.' },
        notes: { type: 'string', description: 'Free-form notes; copied into IR.intent if intent is empty.' },
        task_type: {
          type: 'string',
          enum: ['part', 'edit', 'assembly'],
          description: 'Workflow hint stored on IR meta. Default part.',
        },
        origin: {
          type: 'string',
          description: 'Coordinate origin convention, e.g. "center of top face" or "part center".',
        },
        validation_targets: {
          type: 'array',
          description: 'Optional measure targets for later easycad_measure rounds.',
          items: {
            type: 'object',
            additionalProperties: false,
            required: ['axis', 'expected_mm'],
            properties: {
              id: { type: 'string' },
              axis: { type: 'string', enum: ['X', 'Y', 'Z'] },
              expected_mm: { type: 'number' },
              tol_mm: { type: 'number' },
            },
          },
        },
      },
    },
    output: outputSchema(['ok', 'name', 'brief', 'path'], {
      name: { type: 'string' },
      brief: { type: 'object' },
      ir: { type: 'object' },
      path: { type: 'string' },
      ir_path: { type: 'string' },
    }),
    timeoutMs: 15000,
    async execute(args, exec) {
      const payload = {
        overall_size_mm: args.overall_size_mm,
        units: args.units,
        single_body: args.single_body,
        watertight: args.watertight,
        special_features: args.special_features,
        intent: args.intent,
        notes: args.notes,
        task_type: args.task_type,
        origin: args.origin,
        validation_targets: args.validation_targets,
      }
      return runCli(
        ['brief', '--name', String(args.name), '--json-stdin'],
        exec?.signal,
        JSON.stringify(payload),
      )
    },
  })

  ctx.tools.register({
    name: 'easycad_gen',
    description:
      'Write models/<name>.step.py, run gen_step(), export STEP+GLB, return facts plus qa. The in-page EasyCAD pane opens on the right. '
      + 'ALWAYS pass expect_size from the brief/spec. Do not call easycad_inspect or easycad_qa right after this. '
      + 'source must be complete Python with from build123d import * and def gen_step() returning one solid. Hole(r) is a radius.',
    parameters: {
      type: 'object',
      additionalProperties: false,
      required: ['name', 'source'],
      properties: {
        name: { type: 'string', description: 'Part stem, written to models/<name>.step.py' },
        source: { type: 'string', description: 'Full build123d Python source with gen_step()' },
        expect_size: sizeSchema,
        ref_image: { type: 'string', description: 'Optional path to a reference image for CAD-vs-image similarity (silhouette IoU).' },
      },
    },
    output: outputSchema(['ok', 'script', 'step', 'facts', 'qa'], {
      name: { type: 'string' },
      script: { type: 'string' },
      step: { type: 'string' },
      facts: factsSchema,
      qa: qaSchema,
      similarity: { type: 'object', additionalProperties: true },
      advice: { type: 'object', additionalProperties: true },
      open: { type: 'string' },
    }),
    timeoutMs: 180000,
    async execute(args, exec) {
      const cliArgs = ['write-gen', '--name', String(args.name), '--source-stdin']
      if (Array.isArray(args.expect_size) && args.expect_size.length === 3) {
        cliArgs.push('--expect-size', args.expect_size.map(Number).join(','))
      }
      if (args.ref_image) cliArgs.push('--ref', String(args.ref_image))
      const result = await runCli(cliArgs, exec?.signal, String(args.source))
      result.open = viewerUrl(args.name)
      return result
    },
  })

  ctx.tools.register({
    name: 'easycad_inspect',
    description:
      'Re-measure an existing models/<name>.step.py without rewriting it. Skip after easycad_gen. Use easycad_qa if you only need pass/fail checks.',
    parameters: {
      type: 'object',
      additionalProperties: false,
      required: ['name'],
      properties: {
        name: nameSchema,
        expect_size: sizeSchema,
        ref_image: { type: 'string', description: 'Optional reference image path to compute CAD-vs-image similarity.' },
      },
    },
    output: outputSchema(['ok', 'script', 'facts', 'qa'], {
      name: { type: 'string' },
      script: { type: 'string' },
      facts: factsSchema,
      qa: qaSchema,
      similarity: { type: 'object', additionalProperties: true },
      advice: { type: 'object', additionalProperties: true },
    }),
    timeoutMs: 120000,
    async execute(args, exec) {
      const cliArgs = ['inspect', String(args.name)]
      if (Array.isArray(args.expect_size) && args.expect_size.length === 3) {
        cliArgs.push('--expect-size', args.expect_size.map(Number).join(','))
      }
      if (args.ref_image) cliArgs.push('--ref', String(args.ref_image))
      return runCli(cliArgs, exec?.signal)
    },
  })

  ctx.tools.register({
    name: 'easycad_qa',
    description:
      'Re-check an existing part: overall_dimension (if expect_size set), single_body, watertight. '
      + 'Skip after easycad_gen — that call already returns qa. Use when reviewing a file already on disk.',
    parameters: {
      type: 'object',
      additionalProperties: false,
      required: ['name'],
      properties: {
        name: nameSchema,
        expect_size: sizeSchema,
        ref_image: { type: 'string', description: 'Optional reference image path to compute CAD-vs-image similarity.' },
      },
    },
    output: outputSchema(['ok', 'script', 'facts', 'qa'], {
      name: { type: 'string' },
      script: { type: 'string' },
      facts: factsSchema,
      qa: qaSchema,
      similarity: { type: 'object', additionalProperties: true },
      advice: { type: 'object', additionalProperties: true },
    }),
    timeoutMs: 120000,
    async execute(args, exec) {
      const cliArgs = ['qa', String(args.name)]
      if (Array.isArray(args.expect_size) && args.expect_size.length === 3) {
        cliArgs.push('--expect-size', args.expect_size.map(Number).join(','))
      }
      if (args.ref_image) cliArgs.push('--ref', String(args.ref_image))
      return runCli(cliArgs, exec?.signal)
    },
  })

  ctx.tools.register({
    name: 'easycad_measure',
    description:
      'Measure named overall-axis sizes on an existing part (X/Y/Z bounding-box edges). '
      + 'Use for spec lines like "width 80 mm". Feature-to-feature distances are not supported yet.',
    parameters: {
      type: 'object',
      additionalProperties: false,
      required: ['name', 'checks'],
      properties: {
        name: nameSchema,
        checks: {
          type: 'array',
          minItems: 1,
          items: {
            type: 'object',
            additionalProperties: false,
            required: ['axis', 'expected_mm'],
            properties: {
              id: { type: 'string', description: 'Label, e.g. overall_x' },
              axis: { type: 'string', enum: ['X', 'Y', 'Z'] },
              expected_mm: { type: 'number' },
              tol_mm: { type: 'number', description: 'Override default 0.2 mm' },
            },
          },
        },
      },
    },
    output: outputSchema(['ok', 'script', 'measurements'], {
      name: { type: 'string' },
      script: { type: 'string' },
      facts: factsSchema,
      measurements: { type: 'array', items: { type: 'object' } },
    }),
    timeoutMs: 120000,
    async execute(args, exec) {
      return runCli(
        ['measure', String(args.name), '--json-stdin'],
        exec?.signal,
        JSON.stringify(args.checks),
      )
    },
  })

  ctx.tools.register({
    name: 'easycad_export',
    description:
      'Export an existing models/<name>.step.py to mesh formats. Does not rewrite source. '
      + 'formats: stl (print/DfAM) and/or glb (preview). 3mf is not implemented yet.',
    parameters: {
      type: 'object',
      additionalProperties: false,
      required: ['name'],
      properties: {
        name: nameSchema,
        formats: {
          type: 'array',
          description: 'One or more of stl, glb. Default [stl].',
          items: { type: 'string', enum: ['stl', 'glb'] },
          minItems: 1,
        },
      },
    },
    output: outputSchema(['ok', 'name', 'exports'], {
      name: { type: 'string' },
      script: { type: 'string' },
      exports: {
        type: 'object',
        additionalProperties: true,
        properties: {
          stl: { type: 'string' },
          glb: { type: 'string' },
        },
      },
    }),
    timeoutMs: 180000,
    async execute(args, exec) {
      const cliArgs = ['export', String(args.name)]
      const formats = Array.isArray(args.formats) && args.formats.length ? args.formats : ['stl']
      for (const fmt of formats) cliArgs.push('--format', String(fmt))
      return runCli(cliArgs, exec?.signal)
    },
  })

  ctx.tools.register({
    name: 'easycad_params',
    description:
      'Read editable parametric fields (length, width, height, hole_d) for models/<name>. '
      + 'Use before easycad_apply or when the user asks what can be changed without opening the viewer.',
    parameters: {
      type: 'object',
      additionalProperties: false,
      required: ['name'],
      properties: {
        name: nameSchema,
      },
    },
    output: outputSchema(['ok', 'name', 'values'], {
      name: { type: 'string' },
      values: {
        type: 'object',
        additionalProperties: true,
        properties: {
          length: { type: 'number' },
          width: { type: 'number' },
          height: { type: 'number' },
          hole_d: { type: 'number' },
        },
      },
      fields: { type: 'array', items: { type: 'object' } },
    }),
    timeoutMs: 30000,
    async execute(args, exec) {
      return runCli(['params', String(args.name)], exec?.signal)
    },
  })

  ctx.tools.register({
    name: 'easycad_apply',
    description:
      'Apply parametric edits to models/<name>.step.py, regenerate STEP+GLB, return fresh facts/qa. '
      + 'Same effect as clicking a face in the EasyCAD pane. Pass only keys you want to change.',
    parameters: {
      type: 'object',
      additionalProperties: false,
      required: ['name', 'params'],
      properties: {
        name: nameSchema,
        params: {
          type: 'object',
          additionalProperties: false,
          properties: {
            length: { type: 'number', description: 'Overall X size in mm' },
            width: { type: 'number', description: 'Overall Y size in mm' },
            height: { type: 'number', description: 'Overall Z size in mm' },
            hole_d: { type: 'number', description: 'Center hole diameter in mm' },
          },
        },
      },
    },
    output: outputSchema(['ok', 'name', 'script', 'step', 'facts', 'qa'], {
      name: { type: 'string' },
      script: { type: 'string' },
      step: { type: 'string' },
      facts: factsSchema,
      qa: qaSchema,
      params: { type: 'object', additionalProperties: true },
      open: { type: 'string' },
    }),
    timeoutMs: 180000,
    async execute(args) {
      const result = await workerJob({ cmd: 'apply', name: String(args.name), params: args.params })
      result.open = viewerUrl(args.name)
      return result
    },
  })

  ctx.tools.register({
    name: 'easycad_preview',
    description:
      'Show models/<name> in the in-page EasyCAD pane (right side of the dsh window). '
      + 'Call when the user wants to see or tweak an existing part. easycad_gen already opens that pane.',
    parameters: {
      type: 'object',
      additionalProperties: false,
      required: ['name'],
      properties: {
        name: nameSchema,
      },
    },
    output: outputSchema(['ok', 'name', 'artifacts', 'open'], {
      name: { type: 'string' },
      artifacts: { type: 'object', additionalProperties: true },
      open: { type: 'string' },
      hint: { type: 'string' },
    }),
    timeoutMs: 180000,
    async execute(args, exec) {
      const result = await runCli(['preview', String(args.name)], exec?.signal)
      result.open = viewerUrl(args.name)
      result.hint = 'CAD 分屏在同一 dsh 窗口右侧。点对话里的「分屏查看」或等生成完成后自动打开。'
      return result
    },
  })

  ctx.tools.register({
    name: 'easycad_snapshot',
    description:
      'Render PNG orthographic (iso/front/right/top) views of a part to models/<name>.view_*.png. '
      + 'Used by similarity and for human review; does not rewrite the model.',
    parameters: {
      type: 'object',
      additionalProperties: false,
      required: ['name'],
      properties: {
        name: nameSchema,
        views: {
          type: 'array',
          description: 'Camera views to render. Default [iso, front, right, top].',
          items: { type: 'string', enum: ['iso', 'front', 'right', 'top'] },
        },
      },
    },
    output: outputSchema(['ok', 'name', 'images'], {
      name: { type: 'string' },
      images: { type: 'array', items: { type: 'string' } },
    }),
    timeoutMs: 180000,
    async execute(args) {
      return workerJob({
        cmd: 'snapshot',
        name: String(args.name),
        views: Array.isArray(args.views) ? args.views : [],
      })
    },
  })

  ctx.tools.register({
    name: 'easycad_similarity',
    description:
      'Compare a generated part against a reference image via silhouette IoU (front/right/top). '
      + 'Pass ref_image, or auto-use models/<name>.ref.png. Writes models/<name>.similarity.json and view PNGs. Advisory, never fails geometry QA.',
    parameters: {
      type: 'object',
      additionalProperties: false,
      required: ['name'],
      properties: {
        name: nameSchema,
        ref_image: { type: 'string', description: 'Reference image path (parts photo/view). Optional: falls back to models/<name>.ref.png.' },
      },
    },
    output: outputSchema(['ok', 'name', 'similarity'], {
      name: { type: 'string' },
      similarity: { type: 'object', additionalProperties: true },
    }),
    timeoutMs: 180000,
    async execute(args) {
      return workerJob({
        cmd: 'similarity',
        name: String(args.name),
        ref_image: args.ref_image ? String(args.ref_image) : '',
      })
    },
  })

  ctx.tools.register({
    name: 'easycad_advice',
    description:
      'Generate rule-based AI suggestions from the latest QA + similarity + IR (text prompt intent). '
      + 'Writes models/<name>.advice.json and returns it. Deterministic, no model call.',
    parameters: {
      type: 'object',
      additionalProperties: false,
      required: ['name'],
      properties: {
        name: nameSchema,
      },
    },
    output: outputSchema(['ok', 'name', 'advice'], {
      name: { type: 'string' },
      advice: { type: 'object', additionalProperties: true },
    }),
    timeoutMs: 30000,
    async execute(args, exec) {
      return runCli(['advice', String(args.name)], exec?.signal)
    },
  })

  ctx.tools.register({
    name: 'easycad_part',
    description:
      '[NOT IMPLEMENTED] Search/download a catalog standard part (screw, bearing). Do not call. Model a placeholder with easycad_gen instead.',
    parameters: {
      type: 'object',
      additionalProperties: false,
      required: ['query'],
      properties: {
        query: { type: 'string', description: 'Catalog search, e.g. ISO 4762 M6x20' },
        download: { type: 'boolean', description: 'If true, save STEP under models/ when implemented.' },
      },
    },
    output: outputSchema(['ok'], {
      unimplemented: { type: 'boolean' },
      matches: { type: 'array', items: { type: 'object' } },
    }),
    timeoutMs: 15000,
    async execute() {
      return unimplemented('easycad_part', 'Standard-part catalog is not wired. Placeholder-model with easycad_gen.')
    },
  })

  ctx.tools.register({
    name: 'easycad_assemble',
    description:
      '[NOT IMPLEMENTED] Mate multiple parts (face-to-face, coaxial) and check interference. Do not call. Keep one solid in easycad_gen unless the user insists.',
    parameters: {
      type: 'object',
      additionalProperties: false,
      required: ['name', 'components'],
      properties: {
        name: { type: 'string', description: 'Assembly stem' },
        components: {
          type: 'array',
          minItems: 2,
          items: {
            type: 'object',
            additionalProperties: false,
            required: ['name'],
            properties: {
              name: nameSchema,
              role: { type: 'string', description: 'e.g. base, lid, shaft' },
            },
          },
        },
        mates: {
          type: 'array',
          items: {
            type: 'object',
            additionalProperties: false,
            required: ['kind', 'a', 'b'],
            properties: {
              kind: { type: 'string', enum: ['face_to_face', 'coaxial'] },
              a: { type: 'string', description: 'Component name' },
              b: { type: 'string', description: 'Component name' },
            },
          },
        },
      },
    },
    output: outputSchema(['ok'], {
      unimplemented: { type: 'boolean' },
      step: { type: 'string' },
      interfere: { type: 'boolean' },
    }),
    timeoutMs: 15000,
    async execute() {
      return unimplemented('easycad_assemble', 'AssemblyHelper is not copied yet.')
    },
  })

  ctx.tools.register({
    name: 'easycad_dfam',
    description:
      '[NOT IMPLEMENTED] Design-for-additive-manufacturing check on an STL (overhang, wall thickness). Do not call. Export STL with easycad_export first when this exists.',
    parameters: {
      type: 'object',
      additionalProperties: false,
      required: ['name'],
      properties: {
        name: nameSchema,
        process: {
          type: 'string',
          enum: ['fdm', 'sls', 'sla'],
          description: 'Print process. Default fdm.',
        },
      },
    },
    output: outputSchema(['ok'], {
      unimplemented: { type: 'boolean' },
      findings: { type: 'array', items: { type: 'object' } },
    }),
    timeoutMs: 15000,
    async execute() {
      return unimplemented('easycad_dfam', 'Copy dfam-check into easycad/ before enabling.')
    },
  })

  // Pre-warm the persistent build123d worker so the ~14s OCP import overlaps with
  // server startup instead of the first apply/gen. Errors are non-fatal: workerJob
  // respawns on demand.
  try { spawnWorkerChild() } catch { /* ignore */ }
}
