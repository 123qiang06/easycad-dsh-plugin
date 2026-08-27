window.__ModuleLoader__.load({
  id: 'easycad',
  factory: (require) => {
    const module = { exports: {} }
    const React = require('react')
    const e = React.createElement

    const TOOLS = [
      'easycad_brief', 'easycad_gen', 'easycad_inspect', 'easycad_qa',
      'easycad_measure', 'easycad_export', 'easycad_preview',
      'easycad_params', 'easycad_apply',
      'easycad_snapshot', 'easycad_similarity', 'easycad_advice',
    ]

    // ---- persisted layout state ----
    const PANE_KEY = 'easycad:pane-width'
    const TREE_KEY = 'easycad:tree-width'
    const TREE_OPEN_KEY = 'easycad:tree-open'
    const DEFAULT_PANE = 900
    const MIN_PANE = 520
    const MAX_PANE = 1280
    const DEFAULT_TREE = 190
    const MIN_TREE = 130
    const MAX_TREE = 360
    const TREE_RAIL = 40

    function readNumber(key, fallback, min, max) {
      const value = Number(localStorage.getItem(key))
      if (!Number.isFinite(value)) return fallback
      return Math.min(max, Math.max(min, Math.round(value)))
    }

    function readBool(key, fallback) {
      const raw = localStorage.getItem(key)
      if (raw === null) return fallback
      return raw === '1'
    }

    function writeBool(key, value) {
      localStorage.setItem(key, value ? '1' : '0')
    }

    function applyPaneWidth(width) {
      document.documentElement.style.setProperty('--easycad-w', `${width}px`)
    }

    function getAppFrame() {
      const layer = document.querySelector('[data-shell-overlay]')
      return layer ? layer.parentElement : null
    }

    function setPaneLayoutOpen(open) {
      document.body.classList.toggle('easycad-open', open)
      const frame = getAppFrame()
      if (frame) frame.classList.toggle('easycad-frame-open', open)
    }

    applyPaneWidth(readNumber(PANE_KEY, DEFAULT_PANE, MIN_PANE, MAX_PANE))

    const css = [
      ':root{--easycad-w:900px}',
      '.easycad-frame-open>div:nth-child(2),.easycad-frame-open>div:nth-child(3){padding-right:var(--easycad-w);transition:padding-right .12s ease;box-sizing:border-box}',
      '.ec-overlay{position:absolute;top:0;right:0;bottom:0;width:var(--easycad-w);display:flex;flex-direction:column;background:#fff;border-left:1px solid #e6e8eb;pointer-events:auto;box-shadow:-10px 0 24px rgb(16 24 40 / 8%)}',
      '.ec-overlay[hidden]{display:none}',
      '.ec-resize{position:absolute;left:-3px;top:0;bottom:0;width:6px;cursor:col-resize;touch-action:none;z-index:2}',
      '.ec-resize::after{content:"";position:absolute;left:2px;top:0;bottom:0;width:2px;background:transparent;transition:background .12s ease}',
      '.ec-resize:hover::after,.ec-resize[data-dragging=true]::after{background:#4c7fd4}',
      '.ec-head{display:flex;align-items:center;gap:8px;min-height:38px;padding:0 10px;border-bottom:1px solid #e6e8eb;background:#fff;flex-shrink:0;font:13px/1.4 ui-sans-serif,system-ui,sans-serif;color:#1a1d21}',
      '.ec-head strong{font-size:13px}',
      '.ec-head .ec-part{color:#5c6570;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}',
      '.ec-head button{border:1px solid #d5d9de;background:#fff;border-radius:6px;padding:3px 8px;cursor:pointer;font:12px/1.3 ui-sans-serif,system-ui,sans-serif;color:#1a1d21}',
      '.ec-head button:hover{background:#f4f6f8}',
      '.ec-hint{padding:6px 12px;font:12px/1.5 ui-sans-serif,system-ui,sans-serif;color:#b45309;background:#fef3c7;border-bottom:1px solid #f3e3b0;flex-shrink:0}',
      '.ec-cols{display:flex;flex:1;min-height:0}',
      '.ec-tree{display:flex;flex-direction:column;flex-shrink:0;background:#fff;overflow:hidden}',
      '.ec-tree-head{display:flex;align-items:center;justify-content:space-between;gap:6px;padding:6px 8px 6px 10px;border-bottom:1px solid #e6e8eb;font-size:11px;font-weight:600;letter-spacing:.05em;text-transform:uppercase;color:#6b7280;flex-shrink:0}',
      '.ec-tree-head-actions{display:flex;align-items:center;gap:4px}',
      '.ec-tree-head button{border:0;background:transparent;color:#6b7280;cursor:pointer;font-size:12px;padding:2px 4px;border-radius:4px}',
      '.ec-tree-head button:hover{background:#eef1f4;color:#2563eb}',
      '.ec-tree-body{flex:1;overflow:auto;padding:4px 0 8px;font-size:12px}',
      '.ec-trow{display:flex;align-items:center;gap:4px;min-height:26px;padding:2px 8px 2px 0;cursor:pointer;color:#111827;white-space:nowrap}',
      '.ec-trow:hover{background:#f4f5f7}',
      '.ec-trow.ec-tactive{background:#eff6ff;box-shadow:inset 3px 0 0 #2563eb}',
      '.ec-tcaret{width:14px;flex-shrink:0;color:#6b7280;font-size:10px;text-align:center}',
      '.ec-ticon{width:18px;flex-shrink:0;display:flex;align-items:center;justify-content:center;color:#9aa3af}',
      '.ec-tlabel{min-width:0;overflow:hidden;text-overflow:ellipsis;font-size:11px;color:#374151}',
      '.ec-tlabel.ec-tdir{font-weight:600;color:#111827}',
      '.ec-tchildren{margin-left:10px;border-left:1px solid #e3e6ea}',
      '.ec-empty{padding:10px;color:#6b7280;font-size:11px;line-height:1.5}',
      '.ec-colsep{width:16px;flex-shrink:0;position:relative;background:#fff;border-left:1px solid #e6e8eb;border-right:1px solid #e6e8eb;cursor:col-resize;transition:background .12s ease}',
      '.ec-colsep:hover{background:#f4f6f8}',
      '.ec-tree-head .ec-tree-toggle{display:inline-flex;align-items:center;justify-content:center;width:26px;height:26px;padding:0;box-sizing:border-box;border:0;border-radius:6px;background:transparent;color:#5c6570;cursor:pointer;flex-shrink:0}',
      '.ec-tree-head .ec-tree-toggle:hover{background:#eef1f4;color:#2563eb}',
      '.ec-tree-head .ec-tree-toggle svg{display:block}',
      '.ec-tree-collapsed .ec-tree-head{justify-content:center;padding:6px 7px}',
      '.ec-tree-collapsed .ec-tree-head-label{display:none}',
      '.ec-view{flex:1;min-width:0;display:flex;flex-direction:column;background:#eceff3}',
      '.ec-frame{flex:1;border:0;width:100%;background:#f3f5f7}',
      '.ec-card{border:1px solid #e6e8eb;border-radius:10px;padding:8px 10px;margin:4px 0;background:#fff;font:13px/1.4 ui-sans-serif,system-ui,sans-serif}',
      '.ec-card[data-state=running]{opacity:.75}',
      '.ec-row{display:flex;align-items:center;gap:8px;flex-wrap:wrap}',
      '.ec-qa{border-radius:999px;padding:1px 8px;font-size:11px;background:#f1f3f5;color:#5c6570}',
      '.ec-qa[data-pass=true]{background:#e6f6ec;color:#1b7f46}',
      '.ec-qa[data-pass=false]{background:#fdecea;color:#c62828}',
      '.ec-card button{border:1px solid #d5d9de;background:#fff;border-radius:6px;padding:3px 8px;cursor:pointer}',
      '.ec-foot-btn{display:flex;align-items:center;justify-content:center;gap:6px;width:100%;min-height:36px;border:1px solid #d5d9de;background:#fff;border-radius:8px;padding:6px 10px;font:13px/1.3 ui-sans-serif,system-ui,sans-serif;color:#1a1d21;cursor:pointer}',
      '.ec-foot-btn[data-wide=false]{padding:8px 4px;font-size:10px;font-weight:650;letter-spacing:.01em}',
      '.ec-foot-btn:hover{background:#f4f6f8}',
      '.ec-foot-btn:disabled{opacity:.6;cursor:wait}',
      '.ec-head-btn{display:inline-flex;align-items:center;gap:6px;border:1px solid #d5d9de;background:#fff;border-radius:8px;padding:4px 10px;font:12px/1.3 ui-sans-serif,system-ui,sans-serif;color:#1a1d21;cursor:pointer}',
      '.ec-head-btn:hover{background:#f4f6f8}',
      '.ec-head-btn:disabled{opacity:.6;cursor:wait}',
      '.ec-ctx-backdrop{position:fixed;inset:0;z-index:999;background:transparent}',
      '.ec-ctx{position:fixed;z-index:1000;min-width:128px;background:#fff;border:1px solid #e6e8eb;border-radius:8px;box-shadow:0 6px 24px rgb(16 24 40 / 14%);padding:4px;font:12px/1.4 ui-sans-serif,system-ui,sans-serif}',
      '.ec-ctx-item{padding:6px 12px;border-radius:5px;cursor:pointer;color:#1a1d21;white-space:nowrap}',
      '.ec-ctx-item:hover{background:#f4f5f7}',
      '.ec-ctx-item.ec-ctx-danger{color:#c62828}',
      '.ec-ctx-item.ec-ctx-danger:hover{background:#fdecea}',
      'body.easycad-resizing{user-select:none;cursor:col-resize}',
    ].join('')

    if (typeof document !== 'undefined' && !document.querySelector('style[data-plugin-css="easycad"]')) {
      const tag = document.createElement('style')
      tag.dataset.pluginCss = 'easycad'
      tag.textContent = css
      document.head.appendChild(tag)
    }

    function resultJson(block) {
      if (!block || !('kind' in block)) return null
      const text = (block.content || []).map((item) => item.type === 'text' ? item.text : '').join('\n').trim()
      if (!text) return null
      try { return JSON.parse(text) } catch { return null }
    }

    function callArgs(block) {
      if (!block) return {}
      const raw = ('kind' in block ? (block.call && block.call.argsRaw) : block.argsRaw) || ''
      if (!raw) return {}
      try { return JSON.parse(raw) } catch { return {} }
    }

    function openPart(name) {
      if (!name) return
      window.dispatchEvent(new CustomEvent('easycad:open', { detail: { name } }))
    }

    async function fetchJson(url) {
      try {
        const res = await fetch(url, { cache: 'no-store' })
        if (!res.ok) return null
        return await res.json()
      } catch { return null }
    }

    async function fetchLatestName() {
      const latest = await fetchJson('/easycad/latest')
      return latest && latest.name ? String(latest.name) : null
    }

    async function fetchPartsRows() {
      const body = await fetchJson('/easycad/parts')
      const names = Array.isArray(body && body.names) ? body.names : []
      let parts = Array.isArray(body && body.parts) ? body.parts : []
      // Older server routes return only "names"; synthesize metadata-less rows so the list still renders.
      if (!parts.length && names.length) {
        parts = names.map((name) => ({ name, hasScript: false, hasStep: false, hasGlb: false, hasIr: false }))
      }
      return parts
    }

    async function fetchFileTree() {
      const body = await fetchJson('/easycad/tree')
      if (body && Array.isArray(body.tree) && body.tree.length) return body.tree
      // Fallback for a server without /easycad/tree: group the flat part list under
      // the models directory, shown by its minimal (leaf) name. The absolute path is
      // recorded by the backend; the UI renders only the leaf foldername.
      const partsBody = await fetchJson('/easycad/parts')
      const names = Array.isArray(partsBody && partsBody.names) ? partsBody.names : []
      if (!names.length) return []
      return [{
        kind: 'dir',
        name: 'models',
        path: 'models',
        children: names.map((n) => ({ kind: 'file', name: n, path: n, stem: n, preview: true })),
      }]
    }

    async function resolvePartName() {
      const body = await fetchJson('/easycad/parts')
      const names = Array.isArray(body && body.names) ? body.names : []
      const rows = Array.isArray(body && body.parts) ? body.parts : []
      if (!names.length) return null
      const latest = await fetchLatestName()
      if (latest && names.includes(latest)) return latest
      for (let i = names.length - 1; i >= 0; i -= 1) {
        const row = rows.find((item) => item.name === names[i])
        if (row && (row.hasGlb || row.hasScript || row.hasStep)) return names[i]
      }
      return names[names.length - 1]
    }

    function useLatestPartName() {
      const [name, setName] = React.useState('')
      React.useEffect(() => {
        let alive = true
        const tick = async () => {
          const next = await fetchLatestName()
          if (alive && next) setName(next)
        }
        tick()
        const id = setInterval(tick, 5000)
        return () => {
          alive = false
          clearInterval(id)
        }
      }, [])
      return name
    }

    function usePaneResize(setPaneWidth) {
      const dragging = React.useRef(false)
      const startX = React.useRef(0)
      const startW = React.useRef(DEFAULT_PANE)

      const onPointerDown = (event) => {
        dragging.current = true
        startX.current = event.clientX
        startW.current = readNumber(PANE_KEY, DEFAULT_PANE, MIN_PANE, MAX_PANE)
        document.body.classList.add('easycad-resizing')
        event.currentTarget.dataset.dragging = 'true'
        event.currentTarget.setPointerCapture(event.pointerId)
        event.preventDefault()
      }

      const onPointerMove = (event) => {
        if (!dragging.current) return
        const next = Math.min(MAX_PANE, Math.max(MIN_PANE, startW.current + (startX.current - event.clientX)))
        setPaneWidth(next)
        localStorage.setItem(PANE_KEY, String(next))
      }

      const endDrag = (event) => {
        if (!dragging.current) return
        dragging.current = false
        document.body.classList.remove('easycad-resizing')
        if (event.currentTarget.dataset) event.currentTarget.dataset.dragging = 'false'
        try { event.currentTarget.releasePointerCapture(event.pointerId) } catch {}
      }

      return { onPointerDown, onPointerMove, onPointerUp: endDrag, onPointerCancel: endDrag }
    }

    function useColResize(active, getWidth, setWidth, min, max) {
      const dragging = React.useRef(false)
      const startX = React.useRef(0)
      const startW = React.useRef(DEFAULT_TREE)

      const onPointerDown = (event) => {
        if (!active) return
        dragging.current = true
        startX.current = event.clientX
        startW.current = getWidth()
        document.body.classList.add('easycad-resizing')
        event.currentTarget.dataset.dragging = 'true'
        event.currentTarget.setPointerCapture(event.pointerId)
        event.preventDefault()
      }

      const onPointerMove = (event) => {
        if (!dragging.current) return
        const next = Math.min(max, Math.max(min, startW.current + (event.clientX - startX.current)))
        setWidth(next)
      }

      const endDrag = (event) => {
        if (!dragging.current) return
        dragging.current = false
        document.body.classList.remove('easycad-resizing')
        if (event.currentTarget.dataset) event.currentTarget.dataset.dragging = 'false'
        try { event.currentTarget.releasePointerCapture(event.pointerId) } catch {}
      }

      return { onPointerDown, onPointerMove, onPointerUp: endDrag, onPointerCancel: endDrag }
    }

    function CadOpenButton({ wide, compact }) {
      const latest = useLatestPartName()
      const [busy, setBusy] = React.useState(false)
      const onClick = async () => {
        setBusy(true)
        try {
          const name = await resolvePartName()
          if (name) openPart(name)
        } finally {
          setBusy(false)
        }
      }
      const title = latest ? (`打开 EasyCAD：${latest}`) : '打开 EasyCAD 分屏'
      const className = compact ? 'ec-head-btn' : 'ec-foot-btn'
      return e('button', {
        type: 'button',
        className,
        'data-wide': compact ? undefined : (wide ? 'true' : 'false'),
        disabled: busy,
        title,
        onClick,
      }, 'EasyCAD')
    }

    function CadHeaderButton() {
      return e(CadOpenButton, { compact: true })
    }

    function CadRow({ block, toolName }) {
      const running = !block || !('kind' in block)
      const data = resultJson(block) || {}
      const name = data.name || callArgs(block).name || ''
      React.useEffect(() => {
        if (!running && name) openPart(name)
      }, [running, name])
      const qa = data.qa
      const size = data.facts && data.facts.size_mm
      return e('div', { className: 'ec-card', 'data-state': running ? 'running' : (block.isError ? 'error' : 'ok') },
        e('div', { className: 'ec-row' },
          e('strong', null, 'EasyCAD'),
          e('span', null, running ? (toolName + '…') : (name || toolName)),
          qa ? e('span', { className: 'ec-qa', 'data-pass': String(Boolean(qa.pass)) }, qa.pass ? 'QA 通过' : 'QA 未过') : null,
          size ? e('span', null, size.join(' × ') + ' mm') : null,
          name ? e('button', { type: 'button', onClick: () => openPart(name) }, '分屏查看') : null,
        ),
      )
    }

    // dsh-style tree icons (matching ui-primitives): a filled folder (blue when
    // it holds the active part, gray otherwise) and a small dot-grid glyph for
    // non-folder entries. Inline SVG so the plugin needs no icon dependency.
    function FolderSvg({ active }) {
      const color = active ? '#3b82f6' : '#9aa3af'
      return e('svg', { viewBox: '0 0 24 24', width: 16, height: 16, 'aria-hidden': true, fill: 'none' },
        e('path', {
          d: 'M3.5 7.5a2 2 0 0 1 2-2h4.2l1.6 2h7.2a2 2 0 0 1 2 2v6.5a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2Z',
          fill: color,
        }),
      )
    }

    function DotsSvg() {
      const c = '#94a3b8'
      const dots = []
      for (let y = 0; y < 3; y += 1) {
        for (let x = 0; x < 3; x += 1) {
          dots.push(e('circle', { key: x + '-' + y, cx: 4 + x * 6, cy: 4 + y * 6, r: 1.7, fill: c }))
        }
      }
      return e('svg', { viewBox: '0 0 20 20', width: 15, height: 15, 'aria-hidden': true, fill: 'none' }, dots)
    }

    // Locate the part whose stem matches `stem` inside the file tree. Returns
    // the containing directory path ('' = models root) and the node's own
    // model-relative path, or null when the tree has not loaded it yet.
    function locatePart(tree, stem) {
      let dirPath = null
      let filePath = null
      const walk = (nodes, ancestor) => {
        for (const node of nodes) {
          if (node.kind === 'file') {
            if (node.stem === stem) { dirPath = ancestor; filePath = node.path; return true }
          } else if (node.kind === 'dir') {
            if (walk(node.children || [], node.path)) return true
          }
        }
        return false
      }
      walk(tree, '')
      return { dirPath, filePath }
    }

    // The ancestor directories on the way to a model-relative path, in order.
    // '' (models root) yields [] because the root is always visible.
    function expandDirPathParts(dirPath) {
      if (!dirPath) return []
      const parts = []
      let acc = ''
      for (const seg of dirPath.split('/')) {
        acc = acc ? `${acc}/${seg}` : seg
        parts.push(acc)
      }
      return parts
    }

    // Whether a dir subtree (recursively) holds a previewable part instance.
    function dirContainsPart(node, stem) {
      const children = node.children || []
      return children.some((child) => (
        child.kind === 'file'
          ? (child.preview && child.stem === stem)
          : dirContainsPart(child, stem)
      ))
    }

    function TreeNode({ node, depth, current, expanded, selectedRow, onToggleDir, onOpenFile, onContextMenu }) {
      if (node.kind === 'dir') {
        const open = expanded.has(node.path)
        const activeDir = dirContainsPart(node, current)
        return e('div', { className: 'ec-tnode' },
          e('div', {
            className: 'ec-trow',
            style: { paddingLeft: 6 + depth * 10 },
            role: 'button',
            title: node.path,
            onClick: () => onToggleDir(node.path),
          },
            e('span', { className: 'ec-tcaret' }, open ? '▾' : '▸'),
            e('span', { className: 'ec-ticon' }, e(FolderSvg, { active: activeDir })),
            e('span', { className: 'ec-tlabel ec-tdir' }, node.name),
          ),
          open ? e('div', { className: 'ec-tchildren' },
            (node.children || []).map((child, i) => e(TreeNode, {
              key: child.path || (node.path + '/' + i),
              node: child,
              depth: depth + 1,
              current,
              expanded,
              selectedRow,
              onToggleDir,
              onOpenFile,
              onContextMenu,
            })),
          ) : null,
        )
      }
      // Highlight exactly the row the user selected (STEP or GLB).
      const active = node.preview && node.path === selectedRow
      const clickable = Boolean(node.preview)
      return e('div', { className: 'ec-tnode' },
        e('div', {
          className: 'ec-trow' + (active ? ' ec-tactive' : ''),
          style: { paddingLeft: 18 + depth * 10 },
          role: 'button',
          title: node.path,
          onClick: clickable ? () => onOpenFile(node) : undefined,
          onContextMenu: onContextMenu ? (event) => onContextMenu(event, node) : undefined,
        },
          e('span', { className: 'ec-ticon' }, e(DotsSvg)),
          e('span', { className: 'ec-tlabel' }, node.name),
        ),
      )
    }

    function FileTree({ fileTree, current, expanded, selectedRow, onToggleDir, onOpenFile, onContextMenu }) {
      return e('div', { className: 'ec-tree-body' },
        fileTree.length
          ? fileTree.map((node, i) => e(TreeNode, {
              key: node.path || i,
              node,
              depth: 0,
              current,
              expanded,
              selectedRow,
              onToggleDir,
              onOpenFile,
              onContextMenu,
            }))
          : e('div', { className: 'ec-empty' }, '工作区没有可预览的 STEP/GLB。'),
      )
    }

    function CadOverlay() {
      const [open, setOpen] = React.useState(false)
      const [name, setName] = React.useState('')
      const [paneWidth, setPaneWidth] = React.useState(() => readNumber(PANE_KEY, DEFAULT_PANE, MIN_PANE, MAX_PANE))
      const [treeWidth, setTreeWidth] = React.useState(() => readNumber(TREE_KEY, DEFAULT_TREE, MIN_TREE, MAX_TREE))
      const [treeOpen, setTreeOpenState] = React.useState(() => readBool(TREE_OPEN_KEY, true))
      const [fileTree, setFileTree] = React.useState([])
      const [expandedDirs, setExpandedDirs] = React.useState(() => new Set(['']))
      const [ctxMenu, setCtxMenu] = React.useState(null)
      const knownStemsRef = React.useRef(new Set())
      // The tree row (path) of the currently-selected/opened file. Highlighting
      // follows whatever row the user clicked, so GLB and STEP are both selectable.
      const [selectedRow, setSelectedRow] = React.useState('')
      // Bumped by the 刷新 button to force the 3D iframe to reload.
      const [reloadKey, setReloadKey] = React.useState(0)

      // Resolve the row path (GLB preferred, else STEP) for a part stem.
      const resolveRowPath = (nodes, stem) => {
        let glb = ''
        let step = ''
        const walk = (list) => {
          for (const n of list) {
            if (n.kind === 'file' && n.stem === stem) {
              if (n.ext === '.glb') glb = n.path
              else step = n.path
            } else if (n.kind === 'dir') walk(n.children || [])
          }
        }
        walk(nodes)
        return glb || step || ''
      }

      const stemOfRow = (nodes, path) => {
        let stem = ''
        const walk = (list) => {
          for (const n of list) {
            if (n.path === path) { stem = n.stem; return }
            if (n.kind === 'dir') walk(n.children || [])
          }
        }
        walk(nodes)
        return stem
      }

      // Keep the tree's highlight in sync with the open part. A row the user
      // just clicked is kept; switching to another part adopts its row.
      React.useEffect(() => {
        if (!name || !fileTree.length) return
        setSelectedRow((prev) => {
          const auto = resolveRowPath(fileTree, name)
          if (!auto) return prev
          if (prev === '' || stemOfRow(fileTree, prev) !== name) return auto
          return prev
        })
      }, [name, fileTree])

      // Open a part from the auto-scanned workspace tree: a stem already in
      // models/ opens straight from its GLB; anything else is imported by path.
      const openPartNode = async (node) => {
        setSelectedRow(node.path)
        if (knownStemsRef.current.has(node.stem)) {
          setName(node.stem)
          setOpen(true)
          setPaneLayoutOpen(true)
          return
        }
        try {
          const isGlb = node.ext === '.glb'
          const res = await fetch(
            '/easycad/import?name=' + encodeURIComponent(node.stem) + (isGlb ? '&kind=glb' : '') + '&path=' + encodeURIComponent(node.abs),
            { method: 'POST' },
          )
          const data = await res.json()
          if (data && data.name) {
            setName(data.name)
            setOpen(true)
            setPaneLayoutOpen(true)
          }
        } catch {}
      }

      const paneResize = usePaneResize(setPaneWidth)
      const treeResize = useColResize(treeOpen, () => treeWidth, setTreeWidth, MIN_TREE, MAX_TREE)

      const setTreeOpen = (value) => {
        setTreeOpenState(value)
        writeBool(TREE_OPEN_KEY, value)
      }

      const persistTreeWidth = () => localStorage.setItem(TREE_KEY, String(treeWidth))

      const closePane = () => {
        setOpen(false)
        setPaneLayoutOpen(false)
      }

      const seededTree = React.useRef(false)

      const refreshLists = React.useCallback(async () => {
        try {
          const [nextTree, partsRes] = await Promise.all([
            fetchFileTree(),
            fetch('/easycad/parts', { cache: 'no-store' }),
          ])
          setFileTree(nextTree)
          try {
            const data = await partsRes.json()
            knownStemsRef.current = new Set(Array.isArray(data.names) ? data.names : [])
          } catch {
            knownStemsRef.current = new Set()
          }
          if (!seededTree.current) {
            const tops = nextTree.filter((n) => n.kind === 'dir').map((n) => n.path)
            if (tops.length) {
              seededTree.current = true
              setExpandedDirs((prev) => { const next = new Set(prev); tops.forEach((p) => next.add(p)); return next })
            }
          }
          return nextTree
        } catch {
          // Keep the current view if a refresh fails; never throw from here.
          return null
        }
      }, [])

      // Manual refresh: re-scan the tree AND reload the 3D view for the open part.
      const doRefresh = React.useCallback(async () => {
        await refreshLists()
        setReloadKey((k) => k + 1)
      }, [refreshLists])

      React.useEffect(() => {
        applyPaneWidth(paneWidth)
      }, [paneWidth])

      React.useEffect(() => {
        persistTreeWidth()
      }, [treeWidth])

      // Reveal the folder that holds the open part: expand every ancestor
      // directory along its model-relative path so the selection is visible.
      React.useEffect(() => {
        if (!fileTree.length || !name) return
        const located = locatePart(fileTree, name)
        if (!located || !located.dirPath) return
        const parts = expandDirPathParts(located.dirPath)
        setExpandedDirs((prev) => {
          let changed = false
          const next = new Set(prev)
          for (const part of parts) {
            if (!next.has(part)) { next.add(part); changed = true }
          }
          return changed ? next : prev
        })
      }, [fileTree, name])

      React.useEffect(() => {
        const onOpen = (event) => {
          if (event.detail && event.detail.name) {
            setName(event.detail.name)
            setOpen(true)
            setPaneLayoutOpen(true)
          }
        }
        window.addEventListener('easycad:open', onOpen)
        let last = 0
        const tick = async () => {
          try {
            const res = await fetch('/easycad/latest', { cache: 'no-store' })
            if (!res.ok) return
            const latest = await res.json()
            const stamp = Number(latest.updatedAt) || 0
            if (latest.name && stamp && stamp !== last) {
              last = stamp
              setName(latest.name)
              setOpen(true)
              setPaneLayoutOpen(true)
            }
          } catch {}
        }
        tick()
        const id = setInterval(tick, 2500)
        return () => {
          window.removeEventListener('easycad:open', onOpen)
          clearInterval(id)
          setPaneLayoutOpen(false)
        }
      }, [])

      React.useEffect(() => {
        if (!open) return
        refreshLists()
        const id = setInterval(refreshLists, 3000)
        return () => clearInterval(id)
      }, [open, refreshLists])

      React.useEffect(() => {
        setPaneLayoutOpen(open && Boolean(name))
        if (!open || !name) document.body.classList.remove('easycad-resizing')
      }, [open, name])

      if (!open || !name) return null

      const locatedPart = locatePart(fileTree, name)
      const partLabel = locatedPart
        ? (locatedPart.dirPath ? `${locatedPart.dirPath}/` : '') + name
        : name
      // Always render the built-in in-page preview. The external cad-viewer
      // (scripts\start-cad-viewer.ps1) is currently disabled; its source is
      // kept under easycad/cad-viewer for a future re-enable.
      const src = '/easycad/view?name=' + encodeURIComponent(name) + '&embed=1&panel=1'
      const toggleDir = (dir) => {
        const next = new Set(expandedDirs)
        if (next.has(dir)) next.delete(dir)
        else next.add(dir)
        setExpandedDirs(next)
      }
      const closeCtx = () => setCtxMenu(null)
      const onRowContextMenu = (event, node) => {
        event.preventDefault()
        event.stopPropagation()
        setCtxMenu({ x: event.clientX, y: event.clientY, node })
      }
      const doDownload = (node) => {
        if (!node) return
        setCtxMenu(null)
        const a = document.createElement('a')
        a.href = '/easycad/download?path=' + encodeURIComponent(node.path)
        a.download = node.name || node.path
        document.body.appendChild(a)
        a.click()
        document.body.removeChild(a)
      }
      const doDelete = async (node) => {
        if (!node) return
        setCtxMenu(null)
        const label = node.name || node.path
        if (!window.confirm('确定删除文件「' + label + '」吗？此操作不可撤销。')) return
        try {
          const res = await fetch('/easycad/delete?path=' + encodeURIComponent(node.path), { method: 'POST' })
          const data = await res.json().catch(() => ({}))
          if (!res.ok || !data.ok) {
            window.alert((data && data.error) || '删除失败')
            return
          }
          const nextTree = await refreshLists()
          if (nextTree && name && !resolveRowPath(nextTree, name)) {
            // The open part no longer has a previewable row; close the pane.
            setName('')
            setOpen(false)
            setPaneLayoutOpen(false)
          }
        } catch (err) {
          window.alert('删除失败：' + (err && err.message ? err.message : String(err)))
        }
      }

      return e('aside', {
        className: 'ec-overlay',
        'data-shell-overlay-entry': 'easycad',
        style: { width: `${paneWidth}px` },
      },
        e('div', { className: 'ec-resize', title: '拖动调整分屏宽度', ...paneResize }),
        e('div', { className: 'ec-head' },
          e('strong', null, 'EasyCAD'),
          e('span', { className: 'ec-part', title: partLabel }, partLabel),
          e('div', { style: { flex: 1 } }),
          e('button', { type: 'button', onClick: doRefresh }, '刷新'),
          e('button', { type: 'button', onClick: closePane }, '收起'),
        ),
        e('div', { className: 'ec-cols' },
          e('section', {
            className: 'ec-tree' + (treeOpen ? '' : ' ec-tree-collapsed'),
            style: { width: treeOpen ? `${treeWidth}px` : `${TREE_RAIL}px` },
          },
            e('div', { className: 'ec-tree-head' },
              treeOpen ? e('span', { className: 'ec-tree-head-label' }, '3D 模型') : null,
              e('div', { className: 'ec-tree-head-actions' },
                e('button', {
                  type: 'button',
                  className: 'ec-tree-toggle',
                  title: treeOpen ? '收起文件树' : '展开文件树',
                  'aria-label': treeOpen ? '收起文件树' : '展开文件树',
                  onClick: () => setTreeOpen(!treeOpen),
                },
                  e('svg', { width: 16, height: 16, viewBox: '0 0 16 16', fill: 'none', xmlns: 'http://www.w3.org/2000/svg', 'aria-hidden': 'true' },
                    e('rect', { x: 1.5, y: 2.5, width: 13, height: 11, rx: 2.5, stroke: 'currentColor', strokeWidth: 1.2 }),
                    e('line', { x1: 6.5, y1: 2.5, x2: 6.5, y2: 13.5, stroke: 'currentColor', strokeWidth: 1.2 }),
                    e('rect', { x: 2.5, y: 3.9, width: 2.6, height: 8.2, rx: 1, fill: 'currentColor' }),
                  ),
                ),
              ),
            ),
            treeOpen ? e(FileTree, {
              fileTree,
              current: name,
              expanded: expandedDirs,
              selectedRow,
              onToggleDir: toggleDir,
              onOpenFile: openPartNode,
              onContextMenu: onRowContextMenu,
            }) : null,
          ),
          treeOpen ? e('div', {
            className: 'ec-colsep',
            title: '拖动调整文件树宽度',
            ...treeResize,
          }) : null,
          e('section', { className: 'ec-view' },
            e('iframe', { className: 'ec-frame', title: 'EasyCAD 3D', src, key: `${name}__${reloadKey}` }),
          ),
        ),
        ctxMenu ? e('div', {
          className: 'ec-ctx-backdrop',
          onClick: closeCtx,
          onContextMenu: (ev) => { ev.preventDefault(); closeCtx() },
        }) : null,
        ctxMenu ? e('div', {
          className: 'ec-ctx',
          style: {
            left: Math.max(0, Math.min(ctxMenu.x, window.innerWidth - 132)),
            top: Math.max(0, Math.min(ctxMenu.y, window.innerHeight - 80)),
          },
          onContextMenu: (ev) => ev.preventDefault(),
        },
          e('div', { className: 'ec-ctx-item', onClick: () => doDownload(ctxMenu.node) }, '下载'),
          e('div', { className: 'ec-ctx-item ec-ctx-danger', onClick: () => doDelete(ctxMenu.node) }, '删除'),
        ) : null,
      )
    }

    const inject = ['slots']
    function apply(ctx) {
      for (const key of TOOLS) {
        ctx.slots.inject('tool.call.toolview', () => ctx.slots.register({
          name: 'tool.call.toolview',
          key,
        }, CadRow))
      }
      ctx.slots.inject('shell.overlay', () => ctx.slots.register({
        name: 'shell.overlay',
        id: 'easycad-pane',
      }, CadOverlay))
      ctx.slots.inject('sidebar.footer.action', () => ctx.slots.register({
        name: 'sidebar.footer.action',
        id: 'easycad-open',
        order: 50,
        label: 'EasyCAD',
      }, CadOpenButton))
      ctx.slots.inject('conversation.session.header.actions', () => ctx.slots.register({
        name: 'conversation.session.header.actions',
        id: 'easycad-open',
        order: 40,
        label: 'EasyCAD',
      }, CadHeaderButton))
    }

    module.exports.apply = apply
    module.exports.inject = inject
    return module.exports
  },
})
