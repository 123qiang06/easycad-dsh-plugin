/**
 * Same-origin CAD assets on the dsh webServer. Official plugin API, not a UI slot.
 */
import { spawn } from 'node:child_process'
import { createReadStream, existsSync, readdirSync, readFileSync, statSync, unlinkSync, writeFileSync } from 'node:fs'
import { basename, extname, join, normalize, sep } from 'node:path'

const MIME = {
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.html': 'text/html; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.py': 'text/plain; charset=utf-8',
  '.glb': 'model/gltf-binary',
  '.step': 'application/octet-stream',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.webp': 'image/webp',
  '.gif': 'image/gif',
  '.svg': 'image/svg+xml',
}

function send(res, status, type, body) {
  res.writeHead(status, {
    'content-type': type,
    'cache-control': 'no-store',
    'access-control-allow-origin': '*',
  })
  res.end(body)
}

function safeJoin(root, rel) {
  const target = normalize(join(root, rel))
  const base = normalize(root)
  if (target !== base && !target.startsWith(base + sep)) return null
  return existsSync(target) ? target : null
}

// Resolve a path for reading a workspace file: absolute paths are used as-is,
// relative paths anchor to the workspace root.
function resolveBrowsePath(root, raw) {
  if (!raw) return normalize(root)
  const isAbs = raw.startsWith('/') || /^[A-Za-z]:[\\/]/.test(raw) || raw.startsWith('\\\\')
  return isAbs ? normalize(raw) : normalize(join(root, raw))
}

function sendFile(res, filePath, type, downloadName) {
  const ctype = type || MIME[extname(filePath).toLowerCase()] || 'application/octet-stream'
  const stat = statSync(filePath)
  const headers = {
    'content-type': ctype,
    'content-length': stat.size,
    'cache-control': 'no-store',
    'access-control-allow-origin': '*',
  }
  if (downloadName) {
    const encoded = encodeURIComponent(downloadName)
    headers['content-disposition'] = `attachment; filename="${encoded}"; filename*=UTF-8''${encoded}`
  }
  res.writeHead(200, headers)
  createReadStream(filePath).pipe(res)
}

// Files the model tree may expose via delete/download. Matches the workspace
// 3D tree so right-click actions are limited to real CAD assets, not arbitrary
// workspace files.
function isPreviewable3d(filePath) {
  const lower = String(filePath).toLowerCase()
  return lower.endsWith('.glb') || lower.endsWith('.step') || lower.endsWith('.stp') || lower.endsWith('.step.py')
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = []
    req.on('data', (chunk) => chunks.push(chunk))
    req.on('end', () => resolve(Buffer.concat(chunks)))
    req.on('error', reject)
  })
}

function runCadCli(pythonBin, cli, root, args, stdinText) {
  return new Promise((resolve, reject) => {
    const child = spawn(pythonBin, [cli, ...args], {
      cwd: root,
      env: { ...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' },
      windowsHide: true,
    })
    let stdout = ''
    let stderr = ''
    if (stdinText != null) {
      child.stdin.on('error', () => {})
      child.stdin.end(stdinText, 'utf8')
    } else child.stdin.end()
    child.stdout.on('data', (chunk) => { stdout += chunk.toString('utf8') })
    child.stderr.on('data', (chunk) => { stderr += chunk.toString('utf8') })
    child.on('error', reject)
    child.on('close', () => {
      const line = stdout.trim().split(/\r?\n/).filter(Boolean).at(-1) || ''
      try { resolve(JSON.parse(line)) } catch {
        reject(new Error(stderr.trim() || stdout.trim() || 'cad_cli failed'))
      }
    })
  })
}

function stemFromFile(fileName) {
  if (fileName.endsWith('.step.py')) return fileName.slice(0, -'.step.py'.length)
  return fileName.replace(/\.(ir|brief)\.json$/i, '').replace(/\.(glb|step|stp|stl)$/i, '')
}

function sidecarOnly(fileName) {
  return /\.(memory|qa|advice|similarity|topology|runner|runner\.qa)\.json$/i.test(fileName)
    || /\.(shot|mold)\.(step|stp|glb)$/i.test(fileName)
}

function isShotArtifact(fileName) {
  return /\.(shot|mold)\.(step|stp|glb)$/i.test(fileName)
}

function scanModelFiles(modelsDir, relDir, byName) {
  if (!existsSync(modelsDir)) return
  for (const entry of readdirSync(modelsDir, { withFileTypes: true })) {
    if (entry.name.startsWith('.')) continue
    const relPath = relDir ? `${relDir}/${entry.name}` : entry.name
    if (entry.isDirectory()) {
      scanModelFiles(join(modelsDir, entry.name), relPath, byName)
      continue
    }
    if (sidecarOnly(entry.name) || isShotArtifact(entry.name)) continue
    const stem = stemFromFile(entry.name)
    if (!stem) continue
    const row = byName.get(stem) || {
      name: stem,
      hasScript: false,
      hasStep: false,
      hasGlb: false,
      hasIr: false,
    }
    if (entry.name.endsWith('.step.py')) row.hasScript = true
    if (/\.(step|stp)$/i.test(entry.name)) row.hasStep = true
    if (/\.glb$/i.test(entry.name)) {
      row.hasGlb = true
      row.glbPath = relPath
    }
    if (entry.name.endsWith('.ir.json')) row.hasIr = true
    byName.set(stem, row)
  }
}

function buildModelsTree(modelsDir, relDir = '') {
  if (!existsSync(modelsDir)) return []
  const nodes = []
  for (const entry of readdirSync(modelsDir, { withFileTypes: true })) {
    if (entry.name.startsWith('.')) continue
    const relPath = relDir ? `${relDir}/${entry.name}` : entry.name
    if (entry.isDirectory()) {
      const children = buildModelsTree(join(modelsDir, entry.name), relPath)
      nodes.push({ kind: 'dir', name: entry.name, path: relPath, abs: join(modelsDir, relPath), children })
      continue
    }
    if (sidecarOnly(entry.name)) continue
    const ext = extname(entry.name).toLowerCase()
    const stem = stemFromFile(entry.name)
    nodes.push({
      kind: 'file',
      name: entry.name,
      path: relPath,
      abs: join(modelsDir, relPath),
      ext,
      stem,
      preview: Boolean(stem && (entry.name.endsWith('.step.py') || /\.(step|stp|glb)$/i.test(entry.name))),
    })
  }
  nodes.sort((a, b) => {
    if (a.kind !== b.kind) return a.kind === 'dir' ? -1 : 1
    return a.name.localeCompare(b.name)
  })
  return nodes
}

function listParts(modelsDir) {
  if (!existsSync(modelsDir)) return { names: [], parts: [] }
  const byName = new Map()
  scanModelFiles(modelsDir, '', byName)
  const parts = [...byName.values()].sort((a, b) => a.name.localeCompare(b.name))
  return { names: parts.map((p) => p.name), parts }
}

const SESSIONS_FILE = '.easycad-sessions.json'
const LOCALE_FILE = '.easycad-locale.json'

export function replyInject(locale) {
  return locale === 'en'
    ? 'Reply in English. Chat, QA commentary, and AI suggestions must all be English. Do not mix Chinese into user-facing text.'
    : '请用简体中文回复。对话、质检说明和 AI 建议都用中文，不要中英混写。'
}

export function readUiLocale(modelsDir) {
  const raw = readJsonFile(join(modelsDir, LOCALE_FILE), { locale: 'zh' })
  return raw && raw.locale === 'en' ? 'en' : 'zh'
}

export function writeUiLocale(modelsDir, locale) {
  const next = locale === 'en' ? 'en' : 'zh'
  writeFileSync(join(modelsDir, LOCALE_FILE), `${JSON.stringify({ locale: next }, null, 2)}\n`, 'utf8')
  return next
}

function emptySessionMap() {
  return { byName: {}, bySession: {} }
}

function readJsonFile(filePath, fallback) {
  if (!existsSync(filePath)) return fallback
  try {
    return JSON.parse(readFileSync(filePath, 'utf8'))
  } catch {
    return fallback
  }
}

export function readSessionMap(modelsDir) {
  const raw = readJsonFile(join(modelsDir, SESSIONS_FILE), emptySessionMap())
  return {
    byName: raw && typeof raw.byName === 'object' && raw.byName ? raw.byName : {},
    bySession: raw && typeof raw.bySession === 'object' && raw.bySession ? raw.bySession : {},
  }
}

function writeSessionMap(modelsDir, map) {
  writeFileSync(join(modelsDir, SESSIONS_FILE), `${JSON.stringify(map, null, 2)}\n`, 'utf8')
  return map
}

export function bindSession(modelsDir, name, sessionId) {
  const stem = String(name || '').trim()
  const sid = String(sessionId || '').trim()
  if (!stem || !sid) throw new Error('name and sessionId required')
  const map = readSessionMap(modelsDir)
  const prevSid = map.byName[stem] && map.byName[stem].sessionId
  if (prevSid && map.bySession[prevSid] === stem) delete map.bySession[prevSid]
  const prevName = map.bySession[sid]
  if (prevName && map.byName[prevName]) delete map.byName[prevName]
  map.byName[stem] = { sessionId: sid, updatedAt: Date.now() }
  map.bySession[sid] = stem
  return writeSessionMap(modelsDir, map)
}

export function unbindSession(modelsDir, name) {
  const stem = String(name || '').trim()
  const map = readSessionMap(modelsDir)
  const row = stem && map.byName[stem]
  if (!row) return map
  delete map.byName[stem]
  if (row.sessionId && map.bySession[row.sessionId] === stem) delete map.bySession[row.sessionId]
  return writeSessionMap(modelsDir, map)
}

export function pruneSessions(modelsDir, deadIds) {
  const ids = Array.isArray(deadIds) ? deadIds.map((id) => String(id || '').trim()).filter(Boolean) : []
  if (!ids.length) return readSessionMap(modelsDir)
  const map = readSessionMap(modelsDir)
  let changed = false
  for (const sid of ids) {
    const stem = map.bySession[sid]
    if (!stem) continue
    delete map.bySession[sid]
    if (map.byName[stem] && map.byName[stem].sessionId === sid) delete map.byName[stem]
    changed = true
  }
  return changed ? writeSessionMap(modelsDir, map) : map
}

function partStillOnDisk(modelsDir, stem) {
  if (!stem) return false
  return ['.step.py', '.step', '.stp', '.glb'].some((ext) => existsSync(join(modelsDir, `${stem}${ext}`)))
}

export function buildPartMemory(modelsDir, name) {
  const stem = String(name || '').trim()
  if (!stem) throw new Error('name required')
  const script = join(modelsDir, `${stem}.step.py`)
  const step = join(modelsDir, `${stem}.step`)
  const exists = existsSync(script) || existsSync(step) || existsSync(join(modelsDir, `${stem}.glb`))
  const stored = readJsonFile(join(modelsDir, `${stem}.memory.json`), null)
  const brief = readJsonFile(join(modelsDir, `${stem}.brief.json`), null)
  const ir = readJsonFile(join(modelsDir, `${stem}.ir.json`), null)
  const qa = readJsonFile(join(modelsDir, `${stem}.qa.json`), null)
  const sessions = readSessionMap(modelsDir)
  const locale = readUiLocale(modelsDir)
  const artifacts = {}
  for (const [key, suffix] of [
    ['script', '.step.py'],
    ['step', '.step'],
    ['glb', '.glb'],
    ['ir', '.ir.json'],
    ['brief', '.brief.json'],
    ['runner', '.runner.json'],
    ['shot_step', '.shot.step'],
    ['shot_glb', '.shot.glb'],
    ['mold_step', '.mold.step'],
    ['mold_glb', '.mold.glb'],
  ]) {
    if (existsSync(join(modelsDir, `${stem}${suffix}`))) artifacts[key] = `models/${stem}${suffix}`
  }
  const runner = readJsonFile(join(modelsDir, `${stem}.runner.json`), null)
  const card = {
    ok: true,
    name: stem,
    exists,
    mode: exists ? 'edit' : 'create',
    sessionId: sessions.byName[stem] ? sessions.byName[stem].sessionId : null,
    intent: (ir && ir.intent) || (brief && brief.notes) || (stored && stored.intent) || '',
    envelope: (ir && ir.envelope) || (stored && stored.envelope) || (stored && stored.trust && stored.trust.high && stored.trust.high.envelope) || null,
    params: (ir && ir.params) || (stored && stored.params) || (stored && stored.trust && stored.trust.high && stored.trust.high.params) || null,
    features: (ir && ir.features) || (brief && brief.special_features) || [],
    runner: runner || null,
    qa: qa || (stored && stored.qa) || (stored && stored.trust && stored.trust.high && stored.trust.high.qa) || null,
    facts: (stored && stored.facts) || (stored && stored.trust && stored.trust.high && stored.trust.high.facts) || null,
    artifacts,
    memory_path: existsSync(join(modelsDir, `${stem}.memory.json`)) ? `models/${stem}.memory.json` : null,
    events_path: existsSync(join(modelsDir, `${stem}.events.jsonl`)) ? `models/${stem}.events.jsonl` : null,
    trust: (stored && stored.trust) || null,
    locale,
    reply_in: locale,
    inject: {
      ...((stored && stored.inject) || {
        high: {
          facts: stored && stored.facts ? stored.facts : null,
          envelope: (ir && ir.envelope) || (stored && stored.envelope) || null,
          params: (ir && ir.params) || (stored && stored.params) || null,
          qa: qa || (stored && stored.qa) || null,
          runner: runner ? {
            parting_dir: runner.brief && runner.brief.parting_dir,
            selected: runner.selected || null,
            gate_face_ids: (runner.built && runner.built.gate_face_ids) || (runner.brief && runner.brief.gate_face_ids) || [],
            qa_pass: runner.built && runner.built.qa ? Boolean(runner.built.qa.pass) : null,
          } : null,
        },
        medium: {
          intent: (ir && ir.intent) || (stored && stored.intent) || '',
          features: (ir && ir.features) || [],
        },
        skip: ['advice', 'similarity', 'chat_sizes'],
      }),
      locale,
      reply: replyInject(locale),
    },
    hint: exists
      ? (locale === 'en'
        ? 'Existing part. Stay on this session. Trust inject.high for sizes. Follow inject.reply for language.'
        : '已有零件，留在本会话。尺寸以 inject.high 为准。回复语言以 inject.reply 为准。')
      : (locale === 'en'
        ? 'New part. Call easycad_brief then easycad_review then easycad_gen. Follow inject.reply for language.'
        : '新零件：先 brief，再 review，再 gen。回复语言以 inject.reply 为准。'),
  }
  if (runner && card.inject) {
    card.inject.high = {
      ...(card.inject.high || {}),
      runner: {
        parting_dir: runner.brief && runner.brief.parting_dir,
        selected: runner.selected || null,
        gate_face_ids: (runner.built && runner.built.gate_face_ids) || (runner.brief && runner.brief.gate_face_ids) || [],
        qa_pass: runner.built && runner.built.qa ? Boolean(runner.built.qa.pass) : null,
      },
    }
  }
  return card
}

// Workspace subdirectories to skip when auto-scanning for 3D parts: reference
// projects, dependency/vendor trees, and build caches. A part under one of
// these is not a user CAD model.
const WORKSPACE_SKIP_DIRS = new Set([
  'node_modules', '.git', '.cursor', '.dsh', '.dsh-modules',
  '__pycache__', '_pycache_', '__cadgen__', '_cadgen_', 'vendor', 'benchmark',
  'scripts', 'website', 'deepseek-harness', 'text-to-cad', 'Multi-Agent-CAD', 'cad-viewer',
])

// Auto-discover the workspace's 3D files (STEP/STP/GLB) as a nested folder tree.
// Each part's STEP and GLB appear as separate rows so the step is visible; only
// temp/cache files (leading "__") and non-3D files are omitted.
function buildWorkspace3dTree(root) {
  const items = []
  const walk = (dir, relDir) => {
    let entries
    try { entries = readdirSync(dir, { withFileTypes: true }) } catch { return }
    for (const entry of entries) {
      if (entry.name.startsWith('.')) continue
      const rel = relDir ? `${relDir}/${entry.name}` : entry.name
      if (entry.isDirectory()) {
        if (WORKSPACE_SKIP_DIRS.has(entry.name)) continue
        walk(join(dir, entry.name), rel)
      } else {
        const ext = extname(entry.name).toLowerCase()
        if (ext !== '.glb' && ext !== '.step' && ext !== '.stp') continue
        if (/^__/.test(entry.name)) continue
        if (isShotArtifact(entry.name)) continue
        const stem = stemFromFile(entry.name)
        if (!stem) continue
        items.push({ name: entry.name, rel, abs: join(dir, entry.name), stem, ext })
      }
    }
  }
  walk(root, '')

  const dirs = new Map([['', { kind: 'dir', name: '', path: '', children: [] }]])
  for (const item of items.sort((a, b) => a.rel.localeCompare(b.rel))) {
    const dirPart = item.rel.includes('/') ? item.rel.slice(0, item.rel.lastIndexOf('/')) : ''
    let cur = dirs.get('')
    let curPath = ''
    for (const seg of (dirPart ? dirPart.split('/') : [])) {
      curPath = curPath ? `${curPath}/${seg}` : seg
      let node = dirs.get(curPath)
      if (!node) {
        node = { kind: 'dir', name: seg, path: curPath, children: [] }
        dirs.set(curPath, node)
        cur.children.push(node)
      }
      cur = node
    }
    cur.children.push({ kind: 'file', name: item.name, path: item.rel, rel: item.rel, abs: item.abs, stem: item.stem, ext: item.ext, preview: true })
  }
  const sortNodes = (nodes) => {
    nodes.sort((a, b) => {
      if (a.kind !== b.kind) return a.kind === 'dir' ? -1 : 1
      return a.name.localeCompare(b.name)
    })
    for (const n of nodes) if (n.kind === 'dir') sortNodes(n.children)
  }
  sortNodes(dirs.get('').children)
  return dirs.get('').children
}

export async function handleRequest(paths, req, res) {
  const { modelsDir, previewDir, pythonBin, cli, easycadRoot, workerJob } = paths
  const url = new URL(req.url || '/', 'http://127.0.0.1')
  const path = url.pathname
  if (path === '/easycad' || path === '/easycad/' || path === '/easycad/view') {
    sendFile(res, join(previewDir, 'index.html'), 'text/html; charset=utf-8')
    return
  }
  if (path === '/easycad/locale') {
    if (req.method === 'POST') {
      try {
        const payload = JSON.parse((await readBody(req)).toString('utf8') || '{}')
        const locale = writeUiLocale(modelsDir, payload.locale)
        const name = String(payload.name || '').trim()
        let advice = null
        if (name && workerJob) {
          advice = await workerJob({ cmd: 'advice', name })
        }
        send(res, 200, 'application/json; charset=utf-8', `${JSON.stringify({ ok: true, locale, advice })}\n`)
      } catch (error) {
        send(res, 400, 'application/json; charset=utf-8', `${JSON.stringify({ ok: false, error: String(error.message || error) })}\n`)
      }
      return
    }
    send(res, 200, 'application/json; charset=utf-8', `${JSON.stringify({ locale: readUiLocale(modelsDir) })}\n`)
    return
  }
  if (path === '/easycad/latest') {
    const file = join(modelsDir, '.easycad-latest.json')
    if (!existsSync(file)) {
      send(res, 200, 'application/json; charset=utf-8', '{"name":null}\n')
      return
    }
    sendFile(res, file, 'application/json; charset=utf-8')
    return
  }
  if (path === '/easycad/parts') {
    send(res, 200, 'application/json; charset=utf-8', `${JSON.stringify(listParts(modelsDir))}\n`)
    return
  }
  if (path === '/easycad/sessions' && req.method !== 'POST') {
    send(res, 200, 'application/json; charset=utf-8', `${JSON.stringify(readSessionMap(modelsDir))}\n`)
    return
  }
  if (path === '/easycad/sessions/bind' && req.method === 'POST') {
    try {
      const payload = JSON.parse((await readBody(req)).toString('utf8') || '{}')
      const map = bindSession(modelsDir, payload.name, payload.sessionId)
      send(res, 200, 'application/json; charset=utf-8', `${JSON.stringify({ ok: true, ...map })}\n`)
    } catch (error) {
      send(res, 400, 'application/json; charset=utf-8', `${JSON.stringify({ ok: false, error: String(error.message || error) })}\n`)
    }
    return
  }
  if (path === '/easycad/sessions/unbind' && req.method === 'POST') {
    try {
      const payload = JSON.parse((await readBody(req)).toString('utf8') || '{}')
      const map = unbindSession(modelsDir, payload.name)
      send(res, 200, 'application/json; charset=utf-8', `${JSON.stringify({ ok: true, ...map })}\n`)
    } catch (error) {
      send(res, 400, 'application/json; charset=utf-8', `${JSON.stringify({ ok: false, error: String(error.message || error) })}\n`)
    }
    return
  }
  if (path === '/easycad/sessions/prune' && req.method === 'POST') {
    try {
      const payload = JSON.parse((await readBody(req)).toString('utf8') || '{}')
      const map = pruneSessions(modelsDir, payload.deadIds || payload.sessionIds)
      send(res, 200, 'application/json; charset=utf-8', `${JSON.stringify({ ok: true, ...map })}\n`)
    } catch (error) {
      send(res, 400, 'application/json; charset=utf-8', `${JSON.stringify({ ok: false, error: String(error.message || error) })}\n`)
    }
    return
  }
  if (path === '/easycad/memory') {
    const name = url.searchParams.get('name') || ''
    try {
      send(res, 200, 'application/json; charset=utf-8', `${JSON.stringify(buildPartMemory(modelsDir, name))}\n`)
    } catch (error) {
      send(res, 400, 'application/json; charset=utf-8', `${JSON.stringify({ ok: false, error: String(error.message || error) })}\n`)
    }
    return
  }
  if (path === '/easycad/modelsdir') {
    const dir = String(modelsDir).replace(/\\/g, '/')
    send(res, 200, 'application/json; charset=utf-8', `${JSON.stringify({ dir })}\n`)
    return
  }
  if (path === '/easycad/tree') {
    send(res, 200, 'application/json; charset=utf-8', `${JSON.stringify({
      root: 'EasyCAD',
      tree: buildWorkspace3dTree(easycadRoot),
    })}\n`)
    return
  }
  if (path === '/easycad/ensure-glb') {
    const name = url.searchParams.get('name') || ''
    if (!name) {
      send(res, 400, 'application/json; charset=utf-8', '{"ok":false,"error":"name required"}\n')
      return
    }
    try {
      // Rebuild a missing/broken GLB through the warm build123d worker so a
      // one-off preview rebuild is seconds, not a ~14s cold import.
      const result = workerJob
        ? await workerJob({ cmd: 'preview', name })
        : await runCadCli(pythonBin, cli, easycadRoot, ['preview', name])
      send(res, result.ok === false ? 400 : 200, 'application/json; charset=utf-8', `${JSON.stringify(result)}\n`)
    } catch (error) {
      send(res, 500, 'application/json; charset=utf-8', `${JSON.stringify({ ok: false, error: String(error.message || error) })}\n`)
    }
    return
  }
  if (path === '/easycad/params') {
    const name = url.searchParams.get('name') || ''
    try {
      const result = await runCadCli(pythonBin, cli, easycadRoot, ['params', name || 'loop_demo'])
      send(res, 200, 'application/json; charset=utf-8', `${JSON.stringify(result)}\n`)
    } catch (error) {
      send(res, 500, 'application/json; charset=utf-8', `${JSON.stringify({ ok: false, error: String(error.message || error) })}\n`)
    }
    return
  }
  if (path === '/easycad/runner') {
    const name = (url.searchParams.get('name') || '').trim()
    if (req.method === 'POST') {
      try {
        const payload = JSON.parse((await readBody(req)).toString('utf8') || '{}')
        const stem = String(payload.name || name || '').trim()
        if (!stem) {
          send(res, 400, 'application/json; charset=utf-8', '{"ok":false,"error":"name required"}\n')
          return
        }
        const action = String(payload.cmd || payload.action || 'review')
        let job
        if (action === 'apply') {
          job = { cmd: 'apply', name: stem, params: payload.params || payload }
        } else if (action === 'brief') {
          job = { cmd: 'runner-brief', name: stem, payload }
        } else if (action === 'review') {
          job = { cmd: 'runner-review', name: stem }
        } else if (action === 'propose') {
          job = { cmd: 'runner-propose', name: stem }
        } else if (action === 'build') {
          job = { cmd: 'runner-build', name: stem, candidate: payload.candidate || '' }
        } else if (action === 'qa') {
          job = { cmd: 'runner-qa', name: stem }
        } else if (action === 'mold') {
          job = { cmd: 'runner-mold', name: stem }
        } else {
          send(res, 400, 'application/json; charset=utf-8', `${JSON.stringify({ ok: false, error: `unknown runner cmd: ${action}` })}\n`)
          return
        }
        const result = workerJob
          ? await workerJob(job)
          : await runCadCli(pythonBin, cli, easycadRoot, job.cmd === 'apply'
            ? ['apply', '--name', stem, '--json-stdin']
            : job.cmd === 'runner-brief'
              ? ['runner-brief', '--name', stem, '--json-stdin']
              : job.cmd === 'runner-build'
                ? ['runner-build', stem, '--candidate', String(job.candidate || '')]
                : [job.cmd, stem],
          job.cmd === 'apply' || job.cmd === 'runner-brief' ? JSON.stringify(payload.params || payload) : undefined)
        send(res, result.ok === false ? 400 : 200, 'application/json; charset=utf-8', `${JSON.stringify(result)}\n`)
      } catch (error) {
        send(res, 500, 'application/json; charset=utf-8', `${JSON.stringify({ ok: false, error: String(error.message || error) })}\n`)
      }
      return
    }
    if (!name) {
      send(res, 400, 'application/json; charset=utf-8', '{"ok":false,"error":"name required"}\n')
      return
    }
    const data = readJsonFile(join(modelsDir, `${name}.runner.json`), {})
    send(res, 200, 'application/json; charset=utf-8', `${JSON.stringify({
      ok: true,
      ...(data && typeof data === 'object' ? data : {}),
      name,
      hasShot: existsSync(join(modelsDir, `${name}.shot.glb`)),
      hasMold: existsSync(join(modelsDir, `${name}.mold.glb`)),
    })}\n`)
    return
  }
  if (path === '/easycad/apply' && req.method === 'POST') {
    try {
      const rawBody = await readBody(req)
      const payload = JSON.parse(rawBody.toString('utf8'))
      const result = workerJob
        ? await workerJob({ cmd: 'apply', name: payload.name, params: payload.params || payload })
        : await runCadCli(pythonBin, cli, easycadRoot, ['apply', '--json-stdin'], rawBody)
      send(res, result.ok === false ? 400 : 200, 'application/json; charset=utf-8', `${JSON.stringify(result)}\n`)
    } catch (error) {
      send(res, 500, 'application/json; charset=utf-8', `${JSON.stringify({ ok: false, error: String(error.message || error) })}\n`)
    }
    return
  }
  if (path === '/easycad/import' && req.method === 'POST') {
    const name = (url.searchParams.get('name') || '').trim()
    const kind = (url.searchParams.get('kind') || '').toLowerCase()
    const srcPath = url.searchParams.get('path') || ''
    if (!name) {
      send(res, 400, 'application/json; charset=utf-8', '{"ok":false,"error":"name required"}\n')
      return
    }
    try {
      // `path` = a server-side workspace file to open; otherwise the POST
      // body holds the uploaded file bytes.
      const body = srcPath ? readFileSync(resolveBrowsePath(easycadRoot, srcPath)) : await readBody(req)
      // A GLB is already renderable: copy it into models/ and open it. A
      // STEP/STP is imported (STEP -> GLB) so the viewer can show it.
      if (kind === 'glb') {
        writeFileSync(join(modelsDir, `${name}.glb`), body)
        send(res, 200, 'application/json; charset=utf-8', `${JSON.stringify({ ok: true, name, imported: true })}\n`)
        return
      }
      const tmp = join(modelsDir, `__import_${name}.step`)
      writeFileSync(tmp, body, 'utf8')
      const result = workerJob
        ? await workerJob({ cmd: 'import', name, step: tmp })
        : await runCadCli(pythonBin, cli, easycadRoot, ['import', '--name', name, tmp])
      send(res, result.ok === false ? 400 : 200, 'application/json; charset=utf-8', `${JSON.stringify(result)}\n`)
    } catch (error) {
      send(res, 500, 'application/json; charset=utf-8', `${JSON.stringify({ ok: false, error: String(error.message || error) })}\n`)
    }
    return
  }
  if (path === '/easycad/ref' && req.method === 'POST') {
    const name = (url.searchParams.get('name') || '').trim()
    const srcPath = url.searchParams.get('path') || ''
    if (!name) {
      send(res, 400, 'application/json; charset=utf-8', '{"ok":false,"error":"name required"}\n')
      return
    }
    try {
      // `path` = a server-side workspace file to use as the reference; otherwise
      // the POST body holds the uploaded image bytes (saved raw so the image is
      // exact, including alpha/transparency used by the mask extractor).
      const body = srcPath ? readFileSync(resolveBrowsePath(easycadRoot, srcPath)) : await readBody(req)
      const out = join(modelsDir, `${name}.ref.png`)
      writeFileSync(out, body)
      send(res, 200, 'application/json; charset=utf-8', `${JSON.stringify({ ok: true, name, ref: `models/${name}.ref.png` })}\n`)
    } catch (error) {
      send(res, 500, 'application/json; charset=utf-8', `${JSON.stringify({ ok: false, error: String(error.message || error) })}\n`)
    }
    return
  }
  if (path === '/easycad/download') {
    const rel = url.searchParams.get('path') || ''
    const file = safeJoin(easycadRoot, rel)
    if (!file || !isPreviewable3d(file) || !statSync(file).isFile()) {
      send(res, 404, 'text/plain; charset=utf-8', 'not found')
      return
    }
    sendFile(res, file, undefined, basename(file))
    return
  }
  if (path === '/easycad/delete' && req.method === 'POST') {
    const rel = url.searchParams.get('path') || ''
    const file = safeJoin(easycadRoot, rel)
    if (!file || !isPreviewable3d(file) || !statSync(file).isFile()) {
      send(res, 404, 'application/json; charset=utf-8', '{"ok":false,"error":"not found"}\n')
      return
    }
    try {
      const stem = stemFromFile(basename(file))
      unlinkSync(file)
      if (stem && !partStillOnDisk(modelsDir, stem)) unbindSession(modelsDir, stem)
      send(res, 200, 'application/json; charset=utf-8', '{"ok":true}\n')
    } catch (error) {
      send(res, 500, 'application/json; charset=utf-8', `${JSON.stringify({ ok: false, error: String(error.message || error) })}\n`)
    }
    return
  }
  if (path.startsWith('/easycad/models/')) {
    const rel = decodeURIComponent(path.slice('/easycad/models/'.length))
    const file = safeJoin(modelsDir, rel)
    if (!file || !statSync(file).isFile()) {
      send(res, 404, 'text/plain; charset=utf-8', 'not found')
      return
    }
    sendFile(res, file)
    return
  }
  send(res, 404, 'text/plain; charset=utf-8', 'not found')
}

export function attachCadRoutes(ctx, paths) {
  const web = ctx.webServer
  if (!web) return
  ctx.effect(() => web.register({
    kind: 'prefix',
    path: '/easycad',
    async handler(req, res) {
      // Cache-busting re-import: each request re-evaluates routes.js, so edits
      // to this module take effect on the next request — no dsh restart. If the
      // query-string import is unsupported, fall back to the loaded instance.
      let fresh
      try {
        fresh = await import(`./routes.js?t=${Date.now()}`)
      } catch {
        fresh = null
      }
      await (fresh ? fresh.handleRequest(paths, req, res) : handleRequest(paths, req, res))
    },
  }), 'easycad: routes')
}
