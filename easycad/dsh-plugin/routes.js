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

function scanModelFiles(modelsDir, relDir, byName) {
  if (!existsSync(modelsDir)) return
  for (const entry of readdirSync(modelsDir, { withFileTypes: true })) {
    if (entry.name.startsWith('.')) continue
    const relPath = relDir ? `${relDir}/${entry.name}` : entry.name
    if (entry.isDirectory()) {
      scanModelFiles(join(modelsDir, entry.name), relPath, byName)
      continue
    }
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
      unlinkSync(file)
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
