/**
 * Host-side CAD policy: session binding, reserved tools, shell/fs bypass.
 * Geometry gates live in easycad/runtime/policy.py.
 */
import { readSessionMap } from './routes.js'

const WRITE_TOOLS = new Set([
  'easycad_gen',
  'easycad_apply',
  'easycad_brief',
  'easycad_import',
  'easycad_runner_brief',
  'easycad_runner_propose',
  'easycad_runner_build',
])
const EXPORT_TOOLS = new Set(['easycad_export'])
const RESERVED = new Set(['easycad_part', 'easycad_assemble', 'easycad_dfam'])
const SHELL = new Set(['bash', 'pwsh'])
const FS_WRITE = new Set(['write', 'edit', 'str_replace_editor'])

function agentSessionId(exec) {
  try {
    return (exec && exec.agent && exec.agent.id) || null
  } catch {
    return null
  }
}

function toolNameOf(exec) {
  return String((exec && exec.name) || '')
}

function argsOf(exec) {
  const raw = exec && exec.arguments
  return raw && typeof raw === 'object' ? raw : {}
}

function isStepPyPath(value) {
  return /\.step\.py$/i.test(String(value || '').replace(/\\/g, '/'))
}

function isRunnerPath(value) {
  return /\.runner\.json$/i.test(String(value || '').replace(/\\/g, '/'))
}

function shellWritesStepPy(command) {
  const text = String(command || '')
  if (!/\.step\.py/i.test(text)) return false
  return />|out-file|set-content|add-content|tee-object|copy-item|move-item|new-item/i.test(text)
}

export function cadPreDecision(exec, modelsDir) {
  const name = toolNameOf(exec)
  const args = argsOf(exec)
  if (RESERVED.has(name)) {
    return {
      kind: 'deny',
      reason: `${name} 未实现，执行层已拒绝。用 easycad_gen 建占位实体，不要走 catalog/assemble/dfam。`,
    }
  }
  if (FS_WRITE.has(name) && isStepPyPath(args.file_path || args.path)) {
    return {
      kind: 'deny',
      reason: '不要用文件系统改 *.step.py。用 easycad_gen / easycad_apply，避免绕过 PARAMS 与 worker。',
    }
  }
  if (FS_WRITE.has(name) && isRunnerPath(args.file_path || args.path)) {
    return {
      kind: 'deny',
      reason: '不要用文件系统改 *.runner.json。用 easycad_runner_brief / propose / build 或分屏流道面板。',
    }
  }
  if (SHELL.has(name) && shellWritesStepPy(args.command || args.script)) {
    return {
      kind: 'deny',
      reason: '不要用 shell 改写 *.step.py。用 easycad_gen / easycad_apply。',
    }
  }
  const part = args.name ? String(args.name) : ''
  if (part && (WRITE_TOOLS.has(name) || EXPORT_TOOLS.has(name))) {
    const sid = agentSessionId(exec)
    if (sid) {
      const bound = readSessionMap(modelsDir).bySession[sid]
      if (bound && bound !== part) {
        return {
          kind: 'deny',
          reason: `此会话已绑定零件 ${bound}，不能对 ${part} 调用 ${name}。请切换到该零件对话或新开会话。`,
        }
      }
    }
  }
  return null
}

export function attachCadPolicy(ctx, modelsDir, workerJob) {
  ctx.tools.guard((exec) => {
    const decision = cadPreDecision(exec, modelsDir)
    return decision ? decision.reason : undefined
  })
  ctx.on('tools/pre-execute', async (exec, next) => {
    const decision = cadPreDecision(exec, modelsDir)
    if (decision) return decision
    return next()
  })
  let consolidateTimer = null
  let pendingName = null
  ctx.on('tools/result', (exec) => {
    const tool = toolNameOf(exec)
    if (!/^easycad_(gen|apply|brief|review|import|runner_brief|runner_propose|runner_build)$/.test(tool)) return
    const part = String(argsOf(exec).name || '')
    if (!part || !workerJob) return
    pendingName = part
    clearTimeout(consolidateTimer)
    consolidateTimer = setTimeout(() => {
      const name = pendingName
      pendingName = null
      if (!name) return
      workerJob({ cmd: 'consolidate', name }).catch(() => {})
    }, 2000)
  })
}
