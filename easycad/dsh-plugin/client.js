window.__ModuleLoader__.load({
  id: 'easycad',
  factory: (require) => {
    const module = { exports: {} }
    const React = require('react')
    const e = React.createElement

    const TOOLS = [
      'easycad_brief', 'easycad_review', 'easycad_gen', 'easycad_inspect', 'easycad_qa',
      'easycad_measure', 'easycad_export', 'easycad_preview',
      'easycad_params', 'easycad_apply', 'easycad_memory',
      'easycad_snapshot', 'easycad_similarity', 'easycad_advice',
      'easycad_import',
      'easycad_runner_brief', 'easycad_runner_review', 'easycad_runner_propose',
      'easycad_runner_build', 'easycad_runner_qa',
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
    const LOCALE_KEY = 'easycad:locale'
    const LOCALE_EVENT = 'easycad:locale'

    const COPY = {
      zh: {
        openPane: '打开 EasyCAD 分屏',
        openPart: '打开 EasyCAD：{name}',
        qaPass: 'QA 通过',
        qaFail: 'QA 未过',
        openSplit: '分屏查看',
        emptyTree: '工作区没有可预览的 STEP/GLB。',
        confirmDelete: '确定删除文件「{name}」吗？此操作不可撤销。',
        deleteFailed: '删除失败',
        deleteFailedDetail: '删除失败：{error}',
        resizePane: '拖动调整分屏宽度',
        memory: '记忆',
        memoryTitle: '此对话已绑定该零件，换文件会换对话',
        refresh: '刷新',
        collapse: '收起',
        models: '3D 模型',
        collapseTree: '收起文件树',
        expandTree: '展开文件树',
        resizeTree: '拖动调整文件树宽度',
        download: '下载',
        delete: '删除',
        langTitle: '切换为 English',
        iframeTitle: 'EasyCAD 3D',
        runAdviceFail: '无法发送到当前对话',
      },
      en: {
        openPane: 'Open EasyCAD pane',
        openPart: 'Open EasyCAD: {name}',
        qaPass: 'QA passed',
        qaFail: 'QA failed',
        openSplit: 'Open pane',
        emptyTree: 'No previewable STEP/GLB in this workspace.',
        confirmDelete: 'Delete “{name}”? This cannot be undone.',
        deleteFailed: 'Delete failed',
        deleteFailedDetail: 'Delete failed: {error}',
        resizePane: 'Drag to resize the pane',
        memory: 'Memory',
        memoryTitle: 'This chat is bound to the part. Opening another file switches the chat.',
        refresh: 'Refresh',
        collapse: 'Hide',
        models: '3D Models',
        collapseTree: 'Collapse file tree',
        expandTree: 'Expand file tree',
        resizeTree: 'Drag to resize the file tree',
        download: 'Download',
        delete: 'Delete',
        langTitle: 'Switch to 中文',
        iframeTitle: 'EasyCAD 3D',
        runAdviceFail: 'Could not send to the current chat',
      },
    }

    function readLocale() {
      try {
        return localStorage.getItem(LOCALE_KEY) === 'en' ? 'en' : 'zh'
      } catch {
        return 'zh'
      }
    }

    function writeLocale(locale, partName) {
      const next = locale === 'en' ? 'en' : 'zh'
      try { localStorage.setItem(LOCALE_KEY, next) } catch {}
      window.dispatchEvent(new CustomEvent(LOCALE_EVENT, { detail: next }))
      const notifyFrame = () => {
        const frame = document.querySelector('.ec-frame')
        try {
          if (frame && frame.contentWindow) {
            frame.contentWindow.postMessage({ type: 'easycad:locale', locale: next }, '*')
          }
        } catch {}
      }
      const stem = String(partName || '').split(/[/\\]/).pop()
      fetch('/easycad/locale', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ locale: next, name: stem || '' }),
      }).then(notifyFrame).catch(notifyFrame)
    }

    function t(locale, key, vars) {
      let text = (COPY[locale] && COPY[locale][key]) || COPY.zh[key] || key
      if (vars) {
        Object.keys(vars).forEach((name) => {
          text = text.split('{' + name + '}').join(String(vars[name]))
        })
      }
      return text
    }

    function useLocale() {
      const [locale, setLocale] = React.useState(readLocale)
      React.useEffect(() => {
        const onCustom = (event) => setLocale(event.detail === 'en' ? 'en' : 'zh')
        const onStorage = (event) => {
          if (event.key === LOCALE_KEY) setLocale(event.newValue === 'en' ? 'en' : 'zh')
        }
        window.addEventListener(LOCALE_EVENT, onCustom)
        window.addEventListener('storage', onStorage)
        return () => {
          window.removeEventListener(LOCALE_EVENT, onCustom)
          window.removeEventListener('storage', onStorage)
        }
      }, [])
      return locale
    }

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
      const sidebarSafe = 300
      const room = typeof window !== 'undefined' ? Math.max(MIN_PANE, window.innerWidth - sidebarSafe) : width
      const next = Math.min(MAX_PANE, Math.max(MIN_PANE, Math.min(width, room)))
      document.documentElement.style.setProperty('--easycad-w', `${next}px`)
      return next
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
      '.ec-overlay{position:absolute;top:0;right:0;bottom:0;width:min(var(--easycad-w), calc(100% - 300px));max-width:calc(100vw - 300px);display:flex;flex-direction:column;background:#fff;border-left:1px solid #e6e8eb;pointer-events:auto;box-shadow:-10px 0 24px rgb(16 24 40 / 8%)}',
      '.ec-overlay[hidden]{display:none}',
      '.ec-resize{position:absolute;left:-3px;top:0;bottom:0;width:6px;cursor:col-resize;touch-action:none;z-index:2}',
      '.ec-resize::after{content:"";position:absolute;left:2px;top:0;bottom:0;width:2px;background:transparent;transition:background .12s ease}',
      '.ec-resize:hover::after,.ec-resize[data-dragging=true]::after{background:#4c7fd4}',
      '.ec-head{display:flex;align-items:center;gap:8px;min-height:38px;padding:0 10px;border-bottom:1px solid #e6e8eb;background:#fff;flex-shrink:0;font:13px/1.4 ui-sans-serif,system-ui,sans-serif;color:#1a1d21}',
      '.ec-head strong{font-size:13px}',
      '.ec-head .ec-part{color:#5c6570;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}',
      '.ec-head .ec-mem{flex-shrink:0;border-radius:999px;padding:1px 8px;font-size:11px;background:#edf4ff;color:#1f6feb}',
      '.ec-head button{border:1px solid #d5d9de;background:#fff;border-radius:6px;padding:3px 8px;cursor:pointer;font:12px/1.3 ui-sans-serif,system-ui,sans-serif;color:#1a1d21}',
      '.ec-head button:hover{background:#f4f6f8}',
      '.ec-head button.ec-lang{display:inline-flex;align-items:stretch;padding:0;overflow:hidden;gap:0}',
      '.ec-head button.ec-lang:hover{background:#fff}',
      '.ec-head button.ec-lang span{padding:3px 7px}',
      '.ec-head button.ec-lang span[data-on="true"]{background:#edf4ff;color:#1f6feb}',
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

    async function promptCurrentSession(text) {
      if (!text || !sessionsApi) return false
      const sid = currentSessionId()
      if (!sid) return false
      try {
        const face = sessionsApi.binding(sid) && sessionsApi.binding(sid).session
        if (!face || typeof face.prompt !== 'function') return false
        const result = await face.prompt([{ type: 'text', text: String(text) }], 'queue')
        if (result && result.ok === false) return false
        return true
      } catch {
        return false
      }
    }

    function openPart(name) {
      if (!name) return
      window.dispatchEvent(new CustomEvent('easycad:open', { detail: { name } }))
    }

    let sessionsApi = null
    let workspacesApi = null

    function currentSessionId() {
      try {
        return sessionsApi && sessionsApi.list ? sessionsApi.list.getSnapshot().current : null
      } catch {
        return null
      }
    }

    function sessionSummary(id) {
      if (!id || !sessionsApi) return null
      try {
        return sessionsApi.list.getSnapshot().byId[id] || null
      } catch {
        return null
      }
    }

    async function fetchSessionMap() {
      const body = await fetchJson('/easycad/sessions')
      return {
        byName: (body && body.byName) || {},
        bySession: (body && body.bySession) || {},
      }
    }

    async function bindNameToSession(name, sessionId) {
      if (!name || !sessionId) return
      await fetch('/easycad/sessions/bind', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ name, sessionId }),
      })
    }

    async function pruneDeadSessions() {
      if (!sessionsApi) return
      const map = await fetchSessionMap()
      const deadIds = Object.keys(map.bySession).filter((sid) => !sessionSummary(sid))
      if (!deadIds.length) return
      await fetch('/easycad/sessions/prune', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ deadIds }),
      })
    }

    let sessionLock = Promise.resolve()
    function withSessionLock(fn) {
      const next = sessionLock.then(fn, fn)
      sessionLock = next.then(() => {}, () => {})
      return next
    }

    async function renameSession(sessionId, title) {
      if (!sessionsApi || !sessionId || !title) return
      try {
        const face = sessionsApi.binding(sessionId) && sessionsApi.binding(sessionId).session
        if (face && typeof face.rename === 'function') await face.rename(String(title))
      } catch {}
    }

    async function createBoundSessionUnlocked(name) {
      const wsSnap = workspacesApi && workspacesApi.list ? workspacesApi.list.getSnapshot() : { items: [], recentWorkspaceId: undefined }
      const current = currentSessionId()
      let workspaceId = wsSnap.recentWorkspaceId
      if (current && Array.isArray(wsSnap.items)) {
        const hit = wsSnap.items.find((item) => Array.isArray(item.sessionIds) && item.sessionIds.includes(current))
        if (hit) workspaceId = hit.workspaceId
      }
      const sessionId = await sessionsApi.create(workspaceId ? { workspaceId } : {})
      await bindNameToSession(name, sessionId)
      sessionsApi.open(sessionId)
      await renameSession(sessionId, name)
      return sessionId
    }

    async function ensureSessionForUnlocked(name) {
      if (!name || !sessionsApi) return null
      const map = await fetchSessionMap()
      const boundId = map.byName[name] && map.byName[name].sessionId
      if (boundId && sessionSummary(boundId)) {
        if (currentSessionId() !== boundId) sessionsApi.open(boundId)
        return boundId
      }
      const current = currentSessionId()
      const currentBound = current && map.bySession[current]
      const summary = current && sessionSummary(current)
      if (current && summary && !currentBound && summary.blank) {
        await bindNameToSession(name, current)
        await renameSession(current, name)
        return current
      }
      return createBoundSessionUnlocked(name)
    }

    function ensureSessionFor(name) {
      return withSessionLock(() => ensureSessionForUnlocked(name))
    }

    async function openPartInSession(name) {
      if (!name) return
      try { await ensureSessionFor(name) } catch {}
      openPart(name)
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
      const locale = useLocale()
      const latest = useLatestPartName()
      const [busy, setBusy] = React.useState(false)
      const onClick = async () => {
        setBusy(true)
        try {
          const name = await resolvePartName()
          if (name) openPartInSession(name)
        } finally {
          setBusy(false)
        }
      }
      const title = latest ? t(locale, 'openPart', { name: latest }) : t(locale, 'openPane')
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

    function CadRow({ block, toolName, sessionId }) {
      const locale = useLocale()
      const running = !block || !('kind' in block)
      const data = resultJson(block) || {}
      const name = data.name || callArgs(block).name || ''
      React.useEffect(() => {
        if (running || !name || !sessionId) return
        let cancelled = false
        const bind = async () => {
          try {
            await withSessionLock(async () => {
              if (cancelled) return
              const map = await fetchSessionMap()
              if (cancelled) return
              const currentBound = map.bySession[sessionId]
              if (!currentBound) {
                await bindNameToSession(name, sessionId)
                await renameSession(sessionId, name)
              }
            })
          } catch {}
          // Never sessionsApi.open here: that steals the sidebar click.
          if (!cancelled && currentSessionId() === sessionId) openPart(name)
        }
        bind()
        return () => { cancelled = true }
      }, [running, name, sessionId])
      const qa = data.qa
      const size = data.facts && data.facts.size_mm
      return e('div', { className: 'ec-card', 'data-state': running ? 'running' : (block.isError ? 'error' : 'ok') },
        e('div', { className: 'ec-row' },
          e('strong', null, 'EasyCAD'),
          e('span', null, running ? (toolName + '…') : (name || toolName)),
          qa ? e('span', { className: 'ec-qa', 'data-pass': String(Boolean(qa.pass)) }, qa.pass ? t(locale, 'qaPass') : t(locale, 'qaFail')) : null,
          size ? e('span', null, size.join(' × ') + ' mm') : null,
          name ? e('button', { type: 'button', onClick: () => openPart(name) }, t(locale, 'openSplit')) : null,
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
    function partHasRunner(tree, stem) {
      const needle = `${stem}.runner.json`
      const walk = (nodes) => {
        for (const node of nodes || []) {
          if (node.kind === 'file' && (node.name === needle || String(node.path || '').endsWith(needle))) return true
          if (node.kind === 'dir' && walk(node.children || [])) return true
        }
        return false
      }
      return walk(tree)
    }

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

    function FileTree({ fileTree, current, expanded, selectedRow, onToggleDir, onOpenFile, onContextMenu, emptyLabel }) {
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
          : e('div', { className: 'ec-empty' }, emptyLabel || COPY.zh.emptyTree),
      )
    }

    function CadOverlay({ useSessions }) {
      const locale = useLocale()
      const hookedSession = useSessions ? useSessions((state) => state.current) : null
      const [polledSession, setPolledSession] = React.useState(() => currentSessionId())
      React.useEffect(() => {
        if (useSessions) return undefined
        const id = setInterval(() => setPolledSession(currentSessionId()), 400)
        return () => clearInterval(id)
      }, [useSessions])
      const currentSession = useSessions ? hookedSession : polledSession
      const [open, setOpen] = React.useState(false)
      const [name, setName] = React.useState('')
      React.useEffect(() => {
        if (!name) return undefined
        writeLocale(locale, name)
        return undefined
      }, [name])
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
        if (node.stem) {
          try { await ensureSessionFor(node.stem) } catch {}
        }
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
        if (!currentSession) {
          setOpen(false)
          setPaneLayoutOpen(false)
          return
        }
        let cancelled = false
        ;(async () => {
          try { await pruneDeadSessions() } catch {}
          const map = await fetchSessionMap()
          if (cancelled) return
          const bound = map.bySession[currentSession]
          if (!bound) {
            setOpen(false)
            setPaneLayoutOpen(false)
            return
          }
          setName(bound)
          setOpen(true)
          setPaneLayoutOpen(true)
        })()
        return () => { cancelled = true }
      }, [currentSession])

      React.useEffect(() => {
        const onOpen = (event) => {
          if (event.detail && event.detail.name) {
            setName(event.detail.name)
            setOpen(true)
            setPaneLayoutOpen(true)
          }
        }
        window.addEventListener('easycad:open', onOpen)
        const onAdvice = async (event) => {
          const data = event && event.data
          if (!data || data.type !== 'easycad:run-advice' || !data.prompt) return
          const ok = await promptCurrentSession(data.prompt)
          if (!ok) window.alert(t(readLocale(), 'runAdviceFail'))
        }
        window.addEventListener('message', onAdvice)
        let last = 0
        const tick = async () => {
          try {
            const res = await fetch('/easycad/latest', { cache: 'no-store' })
            if (!res.ok) return
            const latest = await res.json()
            const stamp = Number(latest.updatedAt) || 0
            if (!latest.name || !stamp || stamp === last) return
            last = stamp
            const map = await fetchSessionMap()
            const sid = currentSessionId()
            const bound = sid && map.bySession[sid]
            if (bound) {
              if (bound !== latest.name) return
            } else {
              const summary = sid && sessionSummary(sid)
              if (summary && !summary.blank) return
            }
            setName(latest.name)
            setOpen(true)
            setPaneLayoutOpen(true)
          } catch {}
        }
        tick()
        const id = setInterval(tick, 2500)
        return () => {
          window.removeEventListener('easycad:open', onOpen)
          window.removeEventListener('message', onAdvice)
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
        + (partHasRunner(fileTree, name) ? '&work=runner' : '')
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
        if (!window.confirm(t(locale, 'confirmDelete', { name: label }))) return
        try {
          const res = await fetch('/easycad/delete?path=' + encodeURIComponent(node.path), { method: 'POST' })
          const data = await res.json().catch(() => ({}))
          if (!res.ok || !data.ok) {
            window.alert((data && data.error) || t(locale, 'deleteFailed'))
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
          window.alert(t(locale, 'deleteFailedDetail', { error: (err && err.message ? err.message : String(err)) }))
        }
      }

      return e('aside', {
        className: 'ec-overlay',
        'data-shell-overlay-entry': 'easycad',
        style: { width: `${paneWidth}px` },
      },
        e('div', { className: 'ec-resize', title: t(locale, 'resizePane'), ...paneResize }),
        e('div', { className: 'ec-head' },
          e('strong', null, 'EasyCAD'),
          e('span', { className: 'ec-part', title: partLabel }, partLabel),
          e('span', { className: 'ec-mem', title: t(locale, 'memoryTitle') }, t(locale, 'memory')),
          e('div', { style: { flex: 1 } }),
          e('button', {
            type: 'button',
            className: 'ec-lang',
            title: t(locale, 'langTitle'),
            'aria-label': t(locale, 'langTitle'),
            onClick: (event) => {
              const label = event.target && event.target.textContent
              const next = label === 'EN' ? 'en' : (label === '中' ? 'zh' : (locale === 'zh' ? 'en' : 'zh'))
              writeLocale(next, name)
            },
          },
            e('span', { 'data-on': locale === 'zh' ? 'true' : 'false' }, '中'),
            e('span', { 'data-on': locale === 'en' ? 'true' : 'false' }, 'EN'),
          ),
          e('button', { type: 'button', onClick: doRefresh }, t(locale, 'refresh')),
          e('button', { type: 'button', onClick: closePane }, t(locale, 'collapse')),
        ),
        e('div', { className: 'ec-cols' },
          e('section', {
            className: 'ec-tree' + (treeOpen ? '' : ' ec-tree-collapsed'),
            style: { width: treeOpen ? `${treeWidth}px` : `${TREE_RAIL}px` },
          },
            e('div', { className: 'ec-tree-head' },
              treeOpen ? e('span', { className: 'ec-tree-head-label' }, t(locale, 'models')) : null,
              e('div', { className: 'ec-tree-head-actions' },
                e('button', {
                  type: 'button',
                  className: 'ec-tree-toggle',
                  title: treeOpen ? t(locale, 'collapseTree') : t(locale, 'expandTree'),
                  'aria-label': treeOpen ? t(locale, 'collapseTree') : t(locale, 'expandTree'),
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
              emptyLabel: t(locale, 'emptyTree'),
            }) : null,
          ),
          treeOpen ? e('div', {
            className: 'ec-colsep',
            title: t(locale, 'resizeTree'),
            ...treeResize,
          }) : null,
          e('section', { className: 'ec-view' },
            e('iframe', { className: 'ec-frame', title: t(locale, 'iframeTitle'), src, key: `${name}__${reloadKey}` }),
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
          e('div', { className: 'ec-ctx-item', onClick: () => doDownload(ctxMenu.node) }, t(locale, 'download')),
          e('div', { className: 'ec-ctx-item ec-ctx-danger', onClick: () => doDelete(ctxMenu.node) }, t(locale, 'delete')),
        ) : null,
      )
    }

    const inject = ['slots', 'sessions', 'workspaces']
    function apply(ctx) {
      sessionsApi = ctx.sessions
      workspacesApi = ctx.workspaces
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
