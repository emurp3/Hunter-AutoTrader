import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useAuth } from '../context/AuthContext'

const API = '/api'
const REQUEST_TIMEOUT_MS = 8000
const BROKER_TIMEOUT_MS = 5000

const SECTIONS = [
  { id: 'opportunities', label: 'Opportunities' },
  { id: 'trading', label: 'Trading' },
  { id: 'results', label: 'Performance' },
  { id: 'signals', label: 'Signal Copy' },
  { id: 'forge', label: 'Forge' },
  { id: 'quickcash', label: 'Quick-Cash Board' },
  { id: 'executive', label: 'Executive Summary' },
]

const OCC_LOADERS = {
  summary: { path: '/operations/summary' },
  intake: { path: '/autotrader/intake-summary' },
  opportunities: { path: '/autotrader/opportunities?limit=50' },
  packets: { path: '/packets/' },
  pipeline: { path: '/operations/pipeline' },
  capitalState: { path: '/budget/capital-state', timeoutMs: BROKER_TIMEOUT_MS },
  diagnostics: { path: '/operations/diagnostics' },
}

const TRADING_LOADERS = {
  capitalState: { path: '/budget/capital-state', timeoutMs: BROKER_TIMEOUT_MS },
  budget: { path: '/budget/current', timeoutMs: REQUEST_TIMEOUT_MS },
  positions: { path: '/execution/positions', timeoutMs: BROKER_TIMEOUT_MS },
  orders: { path: '/execution/orders?limit=20', timeoutMs: BROKER_TIMEOUT_MS },
  execution: { path: '/execution/status' },
  daily: { path: '/reports/daily' },
}

const RESULTS_LOADERS = {
  daily: { path: '/reports/daily' },
  weekly: { path: '/reports/weekly' },
  performance: { path: '/performance/summary' },
  transactions: { path: '/budget/transactions?limit=200' },
  tasks: { path: '/tasks/monitor' },
  execution: { path: '/execution/status' },
  intake: { path: '/autotrader/intake-summary' },
}

const EXECUTIVE_LOADERS = {
  health: { path: '/system/health' },
  readiness: { path: '/system/readiness' },
  summary: { path: '/operations/summary' },
  pipeline: { path: '/operations/pipeline' },
  events: { path: '/operations/events?limit=8' },
  diagnostics: { path: '/operations/diagnostics' },
  diagHealth: { path: '/diag/health-summary' },
  diagCapital: { path: '/diag/capital-status', timeoutMs: BROKER_TIMEOUT_MS },
  diagExecution: { path: '/diag/execution-status' },
  diagErrors: { path: '/diag/recent-errors?limit=8' },
  tasks: { path: '/tasks/monitor' },
  autotrader: { path: '/autotrader/status' },
  capitalState: { path: '/budget/capital-state', timeoutMs: BROKER_TIMEOUT_MS },
  performance: { path: '/performance/summary' },
  daily: { path: '/reports/daily' },
  weekly: { path: '/reports/weekly' },
  transactions: { path: '/budget/transactions?limit=200' },
}


const SIGNALS_LOADERS = {
  summary: { path: '/signals/summary' },
  feed: { path: '/signals/feed?limit=50' },
}

const FORGE_LOADERS = {
  summary: { path: '/forge/summary' },
  opportunities: { path: '/forge/opportunities?limit=30' },
}

const QUICKCASH_LOADERS = {
  board: { path: '/quickcash/board?limit=50' },
}
class AuthError extends Error {}

function buildUrl(path) {
  if (path.startsWith('http')) return path
  return `${API}${path}`
}

async function requestJson(path, options = {}) {
  const timeoutMs = options.timeoutMs ?? REQUEST_TIMEOUT_MS
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), timeoutMs)

  try {
    const response = await fetch(buildUrl(path), {
      credentials: 'include',
      headers: { Accept: 'application/json', ...(options.headers || {}) },
      signal: controller.signal,
    })

    if (response.status === 401) throw new AuthError('Authentication required.')

    const text = await response.text()
    let payload = null
    if (text) {
      try { payload = JSON.parse(text) }
      catch { throw new Error(`Invalid JSON from ${path}`) }
    }

    if (!response.ok) {
      const detail = payload?.detail || payload?.message || response.statusText
      throw new Error(`${response.status} ${detail}`)
    }
    return payload
  } catch (error) {
    if (error?.name === 'AbortError') {
      const e = new Error(`Timed out after ${timeoutMs / 1000}s`)
      e.isTimeout = true
      throw e
    }
    throw error
  } finally {
    window.clearTimeout(timer)
  }
}

function useSectionData(loaders, onAuthFail) {
  const [refreshIndex, setRefreshIndex] = useState(0)
  const [endpoints, setEndpoints] = useState(() => initialEndpointState(loaders))

  useEffect(() => {
    let cancelled = false
    setEndpoints((prev) => {
      const next = { ...prev }
      for (const key of Object.keys(loaders)) {
        next[key] = { status: prev[key]?.data ? 'refreshing' : 'loading', data: prev[key]?.data ?? null, error: null, path: loaders[key].path }
      }
      return next
    })
    for (const [key, loader] of Object.entries(loaders)) {
      requestJson(loader.path, { timeoutMs: loader.timeoutMs })
        .then((data) => {
          if (cancelled) return
          setEndpoints((prev) => ({ ...prev, [key]: { status: 'success', data, error: null, path: loader.path } }))
        })
        .catch((error) => {
          if (cancelled) return
          if (error instanceof AuthError) { onAuthFail?.(); return }
          setEndpoints((prev) => ({ ...prev, [key]: { status: 'error', data: prev[key]?.data ?? null, error, path: loader.path } }))
        })
    }
    return () => { cancelled = true }
  }, [loaders, onAuthFail, refreshIndex])

  const refresh = useCallback(() => setRefreshIndex((v) => v + 1), [])
  return { endpoints, refresh }
}

function initialEndpointState(loaders) {
  return Object.fromEntries(Object.entries(loaders).map(([k, l]) => [k, { status: 'idle', data: null, error: null, path: l.path }]))
}

function endpointData(ep, fallback = null) { return ep?.data ?? fallback }
function endpointFailed(ep) { return ep?.status === 'error' }
function isLoading(ep) { return ep?.status === 'loading' || ep?.status === 'refreshing' || ep?.status === 'idle' }

function formatCurrency(value, options = {}) {
  if (value === null || value === undefined || value === '') return 'Unavailable'
  if (!Number.isFinite(Number(value))) return 'Unavailable'
  return Number(value).toLocaleString(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: options.compact ? 0 : 2 })
}

function formatNumber(value) {
  if (value === null || value === undefined || value === '') return 'Unavailable'
  if (!Number.isFinite(Number(value))) return 'Unavailable'
  return Number(value).toLocaleString()
}

function formatPercent(value) {
  if (value === null || value === undefined || value === '') return 'Unavailable'
  if (!Number.isFinite(Number(value))) return 'Unavailable'
  const n = Math.abs(Number(value)) <= 1 ? Number(value) * 100 : Number(value)
  return `${n.toFixed(1)}%`
}

function formatText(value) {
  if (value === null || value === undefined || value === '') return 'Unavailable'
  return String(value).replace(/_/g, ' ').replace(/\b\w/g, (l) => l.toUpperCase())
}

function asArray(value) {
  if (Array.isArray(value)) return value
  if (Array.isArray(value?.items)) return value.items
  if (Array.isArray(value?.results)) return value.results
  if (Array.isArray(value?.opportunities)) return value.opportunities
  if (Array.isArray(value?.packets)) return value.packets
  if (Array.isArray(value?.transactions)) return value.transactions
  if (Array.isArray(value?.positions)) return value.positions
  if (Array.isArray(value?.orders)) return value.orders
  if (Array.isArray(value?.recent_outcomes)) return value.recent_outcomes
  if (Array.isArray(value?.events)) return value.events
  return []
}

function valueFrom(...values) {
  for (const v of values) { if (v !== null && v !== undefined && v !== '') return v }
  return null
}

function countBy(rows, fields) {
  const counts = {}
  for (const row of rows) {
    const v = valueFrom(...fields.map((f) => row?.[f]))
    if (!v) continue
    counts[v] = (counts[v] || 0) + 1
  }
  return counts
}

function sumBy(rows, fields) {
  return rows.reduce((total, row) => {
    const v = valueFrom(...fields.map((f) => row?.[f]))
    const n = Number(v)
    return Number.isFinite(n) ? total + n : total
  }, 0)
}

function normalizeStatusCounts(value) {
  return value?.by_status || value?.tasks_by_status || value?.counts || value || {}
}

function todayRows(rows) {
  const today = new Date().toISOString().slice(0, 10)
  return rows.filter((row) => {
    const raw = valueFrom(row?.created_at, row?.timestamp, row?.date, row?.transaction_date, row?.completed_at)
    if (!raw) return false
    return String(raw).slice(0, 10) === today
  })
}

function statusTone(value) {
  const text = String(value || '').toLowerCase()
  if (text.includes('operational') || text.includes('active') || text.includes('ok') || text.includes('ready')) return 'good'
  if (text.includes('degraded') || text.includes('warning') || text.includes('fallback')) return 'warn'
  if (text.includes('error') || text.includes('failed') || text.includes('down') || text.includes('missing')) return 'bad'
  return 'neutral'
}

// ── OCC Helpers ───────────────────────────────────────────────────────────────
function computeWeightedConfidence(opps) {
  const valid = opps.filter((o) => o.confidence != null && Number(o.estimated_profit) > 0)
  if (!valid.length) {
    const all = opps.filter((o) => o.confidence != null)
    return all.length ? all.reduce((s, o) => s + Number(o.confidence), 0) / all.length : null
  }
  const tot = valid.reduce((s, o) => s + Number(o.estimated_profit), 0)
  return tot > 0 ? valid.reduce((s, o) => s + Number(o.confidence) * Number(o.estimated_profit), 0) / tot : null
}

function computeAvgScore(opps) {
  const scored = opps.filter((o) => o.score != null && Number.isFinite(Number(o.score)))
  return scored.length ? Math.round(scored.reduce((s, o) => s + Number(o.score), 0) / scored.length) : null
}

function computeTopBand(opps) {
  const rank = { elite: 4, high: 3, medium: 2, low: 1 }
  return opps.map((o) => o.priority_band).filter(Boolean).sort((a, b) => (rank[b] || 0) - (rank[a] || 0))[0] || null
}

function sortOpps(opps, by) {
  const copy = [...opps]
  if (by === 'confidence') return copy.sort((a, b) => (Number(b.confidence) || 0) - (Number(a.confidence) || 0))
  if (by === 'upside') return copy.sort((a, b) => (Number(b.estimated_profit) || 0) - (Number(a.estimated_profit) || 0))
  return copy.sort((a, b) => (Number(b.score) || 0) - (Number(a.score) || 0))
}

const TRADING_CATS = new Set(['trading', 'options', 'stocks', 'forex', 'crypto', 'equities', 'futures'])

function bandCls(band) {
  if (band === 'elite') return 'occ-band--elite'
  if (band === 'high') return 'occ-band--high'
  if (band === 'medium') return 'occ-band--medium'
  return 'occ-band--low'
}

// ── OperationsPage ─────────────────────────────────────────────────────────────
export default function OperationsPage({ onBack, onAuthFail }) {
  const { logout } = useAuth()
  const [activeSection, setActiveSection] = useState('opportunities')

  const handleLogout = useCallback(async () => {
    await logout()
    onAuthFail?.()
  }, [logout, onAuthFail])

  return (
    <div className="ops-root occ-page">
      <LeftRailNav
        activeSection={activeSection}
        onSelect={setActiveSection}
        onBack={onBack}
        onLogout={handleLogout}
      />
      <div className="occ-page-main">
        {activeSection === 'opportunities' && <OpportunitiesCommandCenter onAuthFail={onAuthFail} />}
        {activeSection === 'trading' && (
          <div className="occ-legacy-panel hunter-shell-panel hunter-shell-panel--trading">
            <TradingSection onAuthFail={onAuthFail} />
          </div>
        )}
        {activeSection === 'results' && (
          <div className="occ-legacy-panel hunter-shell-panel hunter-shell-panel--results">
            <ResultsSection onAuthFail={onAuthFail} />
          </div>
        )}
        {activeSection === 'signals' && (
          <div className="occ-legacy-panel hunter-shell-panel hunter-shell-panel--signals">
            <SignalCopySection onAuthFail={onAuthFail} />
          </div>
        )}
        {activeSection === 'forge' && (
          <div className="occ-legacy-panel hunter-shell-panel hunter-shell-panel--forge">
            <ForgeSection onAuthFail={onAuthFail} />
          </div>
        )}
        {activeSection === 'quickcash' && (
          <div className="occ-legacy-panel hunter-shell-panel hunter-shell-panel--quickcash">
            <QuickCashSection onAuthFail={onAuthFail} />
          </div>
        )}
        {activeSection === 'executive' && (
          <div className="occ-legacy-panel hunter-shell-panel hunter-shell-panel--executive">
            <ExecutiveSummarySection onAuthFail={onAuthFail} />
          </div>
        )}
      </div>
    </div>
  )
}

// ── Left Rail Navigation ──────────────────────────────────────────────────────
function LeftRailNav({ activeSection, onSelect, onBack, onLogout }) {
  return (
    <nav className="occ-rail" aria-label="Hunter navigation">
      <div className="occ-rail-brand">
        <div className="occ-rail-emblem" aria-hidden="true">H</div>
        <div className="occ-rail-brand-text">
          <span className="occ-rail-brand-name">HUNTER</span>
          <span className="occ-rail-brand-ver">OPS v2</span>
        </div>
      </div>

      <ul className="occ-rail-nav" role="list">
        {SECTIONS.map((s) => (
          <li key={s.id}>
            <button
              type="button"
              className={`occ-rail-item${activeSection === s.id ? ' occ-rail-item--active' : ''}`}
              onClick={() => onSelect(s.id)}
              aria-current={activeSection === s.id ? 'page' : undefined}
            >
              <span className="occ-rail-dot" aria-hidden="true" />
              {s.label}
            </button>
          </li>
        ))}
      </ul>

      <div className="occ-rail-identity">
        <p className="occ-rail-quote">"Fortune Favors precision.<br />We Hunt. Others Follow."</p>
        <p className="occ-rail-attrib">— Hunter</p>
      </div>

      <div className="occ-rail-scan">
        <div className="occ-rail-scan-orb" aria-hidden="true" />
        <p className="occ-rail-scan-title">MARKET SCAN</p>
        <p className="occ-rail-scan-sub">Pipeline scanning 24/7</p>
      </div>

      <div className="occ-rail-footer">
        {onBack && <button className="occ-rail-btn" type="button" onClick={onBack}>Public Site</button>}
        <button className="occ-rail-btn occ-rail-btn--signout" type="button" onClick={onLogout}>Sign Out</button>
      </div>
    </nav>
  )
}

// ── Shared components (used by Trading / Results / Operations legacy sections) ─
function SectionFrame({ title, kicker, endpoints, refresh, children }) {
  const failed = Object.values(endpoints).filter(endpointFailed)
  const loading = Object.values(endpoints).some(isLoading)
  return (
    <div className="hunter-section">
      <div className="hunter-section-heading">
        <div>
          <p className="ops-eyebrow">{kicker}</p>
          <h2>{title}</h2>
        </div>
        <div className="hunter-section-actions">
          {loading && <span className="hunter-section-pill">Loading live data</span>}
          {failed.length > 0 && <span className="hunter-section-pill hunter-section-pill--warn">{failed.length} degraded</span>}
          <button className="ops-secondary-button" type="button" onClick={refresh}>Refresh</button>
        </div>
      </div>
      {failed.length > 0 && <EndpointErrors endpoints={endpoints} />}
      {children}
    </div>
  )
}

function EndpointErrors({ endpoints }) {
  const failures = Object.entries(endpoints).filter(([, ep]) => endpointFailed(ep))
  if (!failures.length) return null
  return (
    <div className="hunter-endpoint-errors" role="status">
      {failures.map(([name, ep]) => (
        <div key={name}>
          <strong>{formatText(name)}</strong>
          <span>{ep.path}: {ep.error?.message || 'Request failed'}</span>
        </div>
      ))}
    </div>
  )
}

function MetricCard({ label, value, detail, status, active = false, onClick }) {
  const Element = onClick ? 'button' : 'article'
  return (
    <Element
      className={`stat-card hunter-metric-card${onClick ? ' hunter-metric-card--interactive' : ' hunter-metric-card--static'}${active ? ' hunter-metric-card--active' : ''}`}
      type={onClick ? 'button' : undefined}
      onClick={onClick}
      aria-pressed={onClick ? active : undefined}
    >
      <span className="stat-label">{label}</span>
      <strong className="stat-value">{value}</strong>
      {detail && <span className={`hunter-metric-detail hunter-metric-detail--${statusTone(status || detail)}`}>{detail}</span>}
    </Element>
  )
}

function DataCard({ title, children, footer }) {
  return (
    <article className="ops-section hunter-data-card">
      <h3>{title}</h3>
      <div>{children}</div>
      {footer && <p className="hunter-card-footer">{footer}</p>}
    </article>
  )
}

function EmptyState({ children = 'Unavailable from current live data.' }) {
  return <div className="ops-no-data">{children}</div>
}

function KeyValueList({ rows }) {
  const visible = rows.filter((r) => r.value !== null && r.value !== undefined && r.value !== '')
  if (!visible.length) return <EmptyState />
  return (
    <div className="hunter-kv-list">
      {visible.map((r) => (
        <div key={r.label}>
          <span>{r.label}</span>
          <strong>{r.value}</strong>
        </div>
      ))}
    </div>
  )
}

function BreakdownList({ data, emptyText }) {
  const rows = Object.entries(data || {}).filter(([, v]) => Number(v) !== 0)
  if (!rows.length) return <EmptyState>{emptyText || 'No breakdown returned by the backend.'}</EmptyState>
  return (
    <div className="hunter-breakdown-list">
      {rows.map(([label, value]) => (
        <div key={label}>
          <span>{formatText(label)}</span>
          <strong>{formatNumber(value)}</strong>
        </div>
      ))}
    </div>
  )
}

// ── Opportunities Command Center ───────────────────────────────────────────────
function OpportunitiesCommandCenter({ onAuthFail }) {
  const { endpoints, refresh } = useSectionData(OCC_LOADERS, onAuthFail)
  const [view, setView] = useState({ id: 'all', type: 'opps', sort: 'score', label: 'Ranked Opportunities' })
  const [selectedOpp, setSelectedOpp] = useState(null)
  const [clock, setClock] = useState(() => new Date())

  useEffect(() => {
    const t = setInterval(() => setClock(new Date()), 30000)
    return () => clearInterval(t)
  }, [])

  const summary = endpointData(endpoints.summary, {})
  const intake = endpointData(endpoints.intake, {})
  const rawOpps = asArray(endpointData(endpoints.opportunities, {}))
  const packets = asArray(endpointData(endpoints.packets, {}))
  const pipeline = endpointData(endpoints.pipeline, {})
  const capitalState = endpointData(endpoints.capitalState, {})
  const diagnostics = endpointData(endpoints.diagnostics, {})
  const fastRecycle = capitalState?.fast_recycle || {}

  // Real PacketStatus enum: draft, ready, acknowledged, executed
  // Real ExecutionState (killed): failed, canceled
  const buildingPackets = packets.filter((p) => ['draft', 'ready', 'acknowledged'].includes(String(p?.status || '').toLowerCase()))
  const executedPackets = packets.filter((p) => String(p?.status || '').toLowerCase() === 'executed')
  const killedPackets = packets.filter((p) => ['failed', 'canceled'].includes(String(p?.execution_state || '').toLowerCase()))

  const totalOpps = valueFrom(summary.total_opportunities, intake.total_from_autotrader, rawOpps.length)
  const totalUpside = valueFrom(intake.total_estimated_monthly_profit || null, sumBy(rawOpps, ['estimated_profit']) || null)
  const weightedConf = computeWeightedConfidence(rawOpps) ?? (intake.average_confidence ?? null)
  const avgScore = computeAvgScore(rawOpps)
  const topBand = computeTopBand(rawOpps)

  const displayOpps = useMemo(() => {
    if (view.type !== 'opps') return []
    let opps = [...rawOpps]
    if (view.statusFilter) opps = opps.filter((o) => view.statusFilter.includes(String(o.status || '').toLowerCase()))
    return sortOpps(opps, view.sort)
  }, [rawOpps, view])

  const displayPackets = view.type === 'packets'
    ? (view.packetGroup === 'building' ? buildingPackets : view.packetGroup === 'executed' ? executedPackets : killedPackets)
    : []

  const fundedOpps = rawOpps.filter((o) => ['budgeted', 'active', 'review_ready'].includes(String(o.status || '').toLowerCase()))
  const fundedCount = valueFrom(summary.active_opportunities, fundedOpps.length)

  const entrepOpps = rawOpps
    .filter((o) => { const c = String(o.category || o.origin_module || '').toLowerCase(); return c && !TRADING_CATS.has(c) })
    .sort((a, b) => (Number(b.score) || 0) - (Number(a.score) || 0))
  const topEntrepOpp = entrepOpps[0] || null

  const insight = (diagnostics.diagnosis || [])[0] || null
  const insightConf = valueFrom(intake.average_confidence, weightedConf)

  const failed = Object.values(endpoints).filter(endpointFailed)
  const anyLoading = Object.values(endpoints).some(isLoading)

  const missionTime = clock.toLocaleString('en-US', {
    weekday: 'short', month: 'short', day: 'numeric', year: 'numeric',
    hour: '2-digit', minute: '2-digit', timeZoneName: 'short',
  })

  function selectView(v) { setView(v); setSelectedOpp(null) }

  return (
    <div className="occ-content">
      {/* Mission bar */}
      <div className="occ-mission-bar">
        <div className="occ-mission-block">
          <span className="occ-mission-label">CONCEPT</span>
          <span className="occ-mission-val occ-mission-concept">6</span>
        </div>
        <div className="occ-mission-block occ-mission-block--center">
          <span className="occ-mission-label">MISSION TIME</span>
          <span className="occ-mission-val">{missionTime}</span>
        </div>
        <div className="occ-mission-block occ-mission-block--right">
          <span className="occ-mission-label">HUNTER MODE</span>
          <span className="occ-mission-val occ-mission-active">
            <span className="occ-pulse" aria-hidden="true" />
            ACTIVE
          </span>
          <div className="occ-mission-controls">
            {anyLoading && <span className="occ-badge occ-badge--loading">Loading</span>}
            {failed.length > 0 && <span className="occ-badge occ-badge--warn">{failed.length} degraded</span>}
            <button type="button" className="occ-refresh-btn" onClick={refresh} title="Refresh all">↻ Refresh</button>
          </div>
        </div>
      </div>

      {/* Title */}
      <div className="occ-title-zone">
        <p className="occ-kicker">COMMAND CENTER</p>
        <h1 className="occ-main-title">OPPORTUNITIES COMMAND CENTER</h1>
        <p className="occ-tagline">Identify. Evaluate. Execute. Repeat.</p>
      </div>

      {/* Inline errors */}
      {failed.length > 0 && (
        <div className="occ-errors">
          {Object.entries(endpoints).filter(([, ep]) => endpointFailed(ep)).map(([name, ep]) => (
            <div key={name} className="occ-error-row">
              <strong>{formatText(name)}</strong>
              <span>{ep.path}: {ep.error?.message || 'Request failed'}</span>
            </div>
          ))}
        </div>
      )}

      {/* Summary metrics row — each card is clickable and changes the table view */}
      <div className="occ-metrics-row">
        <button
          type="button"
          className={`occ-metric${view.id === 'all' ? ' occ-metric--active' : ''}`}
          onClick={() => selectView({ id: 'all', type: 'opps', sort: 'score', label: 'Ranked Opportunities' })}
        >
          <span className="occ-metric-label">TOTAL OPPORTUNITIES</span>
          <strong className="occ-metric-value">{formatNumber(totalOpps)}</strong>
          <span className="occ-metric-sub">{intake.total_from_autotrader != null ? `${intake.total_from_autotrader} from autotrader` : 'from live data'}</span>
        </button>

        <button
          type="button"
          className={`occ-metric${view.id === 'conf' ? ' occ-metric--active' : ''}`}
          onClick={() => selectView({ id: 'conf', type: 'opps', sort: 'confidence', label: 'Sorted by Confidence' })}
        >
          <span className="occ-metric-label">WEIGHTED CONFIDENCE</span>
          <strong className="occ-metric-value">{weightedConf != null ? formatPercent(weightedConf) : '—'}</strong>
          <span className={`occ-metric-sub${weightedConf == null ? ' occ-metric-sub--na' : ''}`}>
            {weightedConf != null ? 'weighted by est. upside' : 'No confidence data yet'}
          </span>
        </button>

        <button
          type="button"
          className={`occ-metric${view.id === 'upside' ? ' occ-metric--active' : ''}`}
          onClick={() => selectView({ id: 'upside', type: 'opps', sort: 'upside', label: 'Sorted by Est. Upside' })}
        >
          <span className="occ-metric-label">EST. TOTAL UPSIDE</span>
          <strong className="occ-metric-value">{totalUpside > 0 ? formatCurrency(totalUpside) : '—'}</strong>
          <span className={`occ-metric-sub${!totalUpside ? ' occ-metric-sub--na' : ''}`}>
            {totalUpside > 0 ? 'sum of estimated profit' : 'Unavailable'}
          </span>
        </button>

        <div className="occ-metric occ-metric--static">
          <span className="occ-metric-label">AVG. TIME TO RETURN</span>
          <strong className="occ-metric-value">—</strong>
          <span className="occ-metric-sub occ-metric-sub--na">Not tracked yet</span>
        </div>

        <div className="occ-metric occ-metric--score">
          <span className="occ-metric-label">HUNTER SCORE</span>
          <div className="occ-score-ring">
            <strong className="occ-score-num">{avgScore ?? '—'}</strong>
            {topBand && <span className={`occ-score-band ${bandCls(topBand)}`}>{topBand.toUpperCase()}</span>}
          </div>
        </div>
      </div>

      {/* Packet status chips */}
      <div className="occ-chips-row">
        <span className="occ-chips-label">PACKET STATUS</span>
        <button
          type="button"
          className={`occ-chip${view.id === 'building' ? ' occ-chip--active' : ''}`}
          onClick={() => selectView({ id: 'building', type: 'packets', packetGroup: 'building', label: 'Building Packets' })}
        >
          Building <span className="occ-chip-count">{buildingPackets.length}</span>
        </button>
        <button
          type="button"
          className={`occ-chip${view.id === 'executed' ? ' occ-chip--active' : ''}`}
          onClick={() => selectView({ id: 'executed', type: 'packets', packetGroup: 'executed', label: 'Executed Packets' })}
        >
          Executed <span className="occ-chip-count">{executedPackets.length}</span>
        </button>
        <button
          type="button"
          className={`occ-chip occ-chip--danger${view.id === 'killed' ? ' occ-chip--active' : ''}`}
          onClick={() => selectView({ id: 'killed', type: 'packets', packetGroup: 'killed', label: 'Killed Packets' })}
        >
          Killed <span className="occ-chip-count">{killedPackets.length}</span>
        </button>
        {view.id !== 'all' && view.type === 'opps' && (
          <button type="button" className="occ-chip occ-chip--back" onClick={() => selectView({ id: 'all', type: 'opps', sort: 'score', label: 'Ranked Opportunities' })}>
            ← All Opportunities
          </button>
        )}
        {view.type === 'packets' && (
          <button type="button" className="occ-chip occ-chip--back" onClick={() => selectView({ id: 'all', type: 'opps', sort: 'score', label: 'Ranked Opportunities' })}>
            ← All Opportunities
          </button>
        )}
      </div>

      {/* Main body grid: table (left) + right panels */}
      <div className="occ-body-grid">
        {/* Table zone */}
        <div className="occ-table-zone">
          <div className="occ-table-header">
            <h2 className="occ-table-title">
              {view.type === 'opps' ? 'TOP OPPORTUNITIES (RANKED)' : view.label.toUpperCase()}
            </h2>
            {view.type === 'opps' && rawOpps.length > 0 && (
              <span className="occ-table-count">{displayOpps.length} results · sorted by {view.sort}</span>
            )}
          </div>

          {/* Detail panel — shown when a row is clicked */}
          {selectedOpp && (
            <div className="occ-detail-panel">
              <div className="occ-detail-head">
                <h3 className="occ-detail-name">{selectedOpp.description || selectedOpp.source_id}</h3>
                <button type="button" className="occ-detail-close" onClick={() => setSelectedOpp(null)}>✕ Close</button>
              </div>
              <div className="occ-detail-grid">
                <div><span>Status</span><strong>{formatText(selectedOpp.status)}</strong></div>
                <div><span>Priority Band</span><strong className={selectedOpp.priority_band ? bandCls(selectedOpp.priority_band) : ''}>{formatText(selectedOpp.priority_band) || '—'}</strong></div>
                <div><span>Score</span><strong>{selectedOpp.score != null ? selectedOpp.score : '—'}</strong></div>
                <div><span>Confidence</span><strong>{selectedOpp.confidence != null ? formatPercent(selectedOpp.confidence) : '—'}</strong></div>
                <div><span>Est. Profit</span><strong>{formatCurrency(selectedOpp.estimated_profit)}</strong></div>
                <div><span>Category</span><strong>{formatText(selectedOpp.category) || '—'}</strong></div>
                <div><span>Origin</span><strong>{formatText(selectedOpp.origin_module) || '—'}</strong></div>
                <div><span>Date Found</span><strong>{selectedOpp.date_found || '—'}</strong></div>
                {selectedOpp.next_action && (
                  <div className="occ-detail-full"><span>Next Action</span><strong>{selectedOpp.next_action}</strong></div>
                )}
                {selectedOpp.decision && (
                  <>
                    <div><span>Decision State</span><strong>{formatText(selectedOpp.decision.action_state) || '—'}</strong></div>
                    <div><span>Execution Path</span><strong>{formatText(selectedOpp.decision.execution_path) || '—'}</strong></div>
                    <div><span>Execution Ready</span><strong>{selectedOpp.decision.execution_ready ? 'Yes' : 'No'}</strong></div>
                    {selectedOpp.decision.capital_recommendation != null && (
                      <div><span>Capital Rec.</span><strong>{formatCurrency(selectedOpp.decision.capital_recommendation)}</strong></div>
                    )}
                  </>
                )}
              </div>
            </div>
          )}

          {/* Opportunities table */}
          {view.type === 'opps' && (
            displayOpps.length === 0 ? (
              <div className="occ-empty">
                {anyLoading ? 'Loading opportunities…' : 'No opportunities returned from live data.'}
              </div>
            ) : (
              <>
                <div className="occ-ranked-table">
                  <div className="occ-ranked-head">
                    <span>#</span>
                    <span>OPPORTUNITY</span>
                    <span>TYPE</span>
                    <span>CONFIDENCE</span>
                    <span>EST. UPSIDE</span>
                    <span>SCORE</span>
                    <span>STATUS</span>
                    <span>ACTION</span>
                  </div>
                  {displayOpps.slice(0, 12).map((opp, i) => (
                    <button
                      key={opp.source_id || i}
                      type="button"
                      className={`occ-ranked-row${selectedOpp?.source_id === opp.source_id ? ' occ-ranked-row--selected' : ''}`}
                      onClick={() => setSelectedOpp(selectedOpp?.source_id === opp.source_id ? null : opp)}
                    >
                      <span className="occ-rank">{i + 1}</span>
                      <span className="occ-opp-name" title={opp.description}>{opp.description || opp.source_id}</span>
                      <span className="occ-opp-type">{formatText(opp.category || opp.origin_module) || '—'}</span>
                      <span className="occ-conf-cell">
                        {opp.confidence != null ? (
                          <>
                            <div className="occ-conf-track"><div className="occ-conf-bar" style={{ width: `${Math.min(100, Math.round(Number(opp.confidence) * 100))}%` }} /></div>
                            <span className="occ-conf-pct">{formatPercent(opp.confidence)}</span>
                          </>
                        ) : <span className="occ-na">—</span>}
                      </span>
                      <span className="occ-upside-val">{formatCurrency(opp.estimated_profit)}</span>
                      <span className="occ-score-cell">{opp.score != null ? opp.score : '—'}</span>
                      <span className={`occ-status-pill occ-status-pill--${String(opp.status || '').toLowerCase()}`}>{formatText(opp.status)}</span>
                      <span className="occ-open-btn">Open Brief</span>
                    </button>
                  ))}
                </div>
                {rawOpps.length > 12 && (
                  <p className="occ-view-all">Showing top 12 of {rawOpps.length} — click a metric card to sort</p>
                )}
              </>
            )
          )}

          {/* Packet table */}
          {view.type === 'packets' && (
            displayPackets.length === 0 ? (
              <div className="occ-empty">No {view.label.toLowerCase()}.</div>
            ) : (
              <div className="occ-ranked-table">
                <div className="occ-ranked-head occ-ranked-head--packets">
                  <span>#</span>
                  <span>PACKET / OPPORTUNITY</span>
                  <span>SOURCE ID</span>
                  <span>STATUS</span>
                  <span>EXEC STATE</span>
                  <span>EST. RETURN</span>
                </div>
                {displayPackets.slice(0, 12).map((p, i) => (
                  <div key={p.id || i} className="occ-ranked-row occ-ranked-row--static">
                    <span className="occ-rank">{i + 1}</span>
                    <span className="occ-opp-name">{valueFrom(p.opportunity_summary, p.source_id, `Packet ${i + 1}`)}</span>
                    <span className="occ-opp-type">{p.source_id || '—'}</span>
                    <span className={`occ-status-pill occ-status-pill--${String(p.status || '').toLowerCase()}`}>{formatText(p.status)}</span>
                    <span className={`occ-status-pill occ-status-pill--${String(p.execution_state || '').toLowerCase()}`}>{formatText(p.execution_state) || '—'}</span>
                    <span className="occ-upside-val">{formatCurrency(valueFrom(p.estimated_return, p.budget_recommendation))}</span>
                  </div>
                ))}
              </div>
            )
          )}
        </div>

        {/* Right column: side panels */}
        <div className="occ-right-col">
          {/* Operations Snapshot */}
          <div className="occ-panel">
            <h3 className="occ-panel-title">OPERATIONS SNAPSHOT</h3>
            <div className="occ-snapshot-grid">
              <div className="occ-snapshot-item">
                <span className="occ-snapshot-label">FUNDED OPPORTUNITIES</span>
                <strong className="occ-snapshot-val">{formatNumber(fundedCount)}</strong>
                <span className="occ-snapshot-sub">{formatCurrency(sumBy(fundedOpps, ['estimated_profit']))}</span>
              </div>
              <div className="occ-snapshot-item">
                <span className="occ-snapshot-label">ACTIVE PACKETS</span>
                <strong className="occ-snapshot-val">{formatNumber(buildingPackets.length)}</strong>
                <span className="occ-snapshot-sub">draft / ready / acknowledged</span>
              </div>
              <div className="occ-snapshot-item">
                <span className="occ-snapshot-label">FAST RECYCLE HEALTH</span>
                <strong className="occ-snapshot-val">
                  {fastRecycle.recycle_win_rate != null ? formatPercent(fastRecycle.recycle_win_rate) : '—'}
                </strong>
                <span className="occ-snapshot-sub">
                  {fastRecycle.enabled === false ? 'Disabled' : fastRecycle.enabled ? 'Enabled' : 'Unavailable'}
                </span>
              </div>
            </div>
            {pipeline?.by_status && Object.keys(pipeline.by_status).length > 0 && (
              <div className="occ-pipeline-bars">
                <p className="occ-bars-title">PIPELINE BY STATUS</p>
                {Object.entries(pipeline.by_status).map(([s, c]) => (
                  <div key={s} className="occ-bar-row">
                    <span className="occ-bar-label">{formatText(s)}</span>
                    <div className="occ-bar-track">
                      <div className="occ-bar-fill" style={{ width: `${Math.min(100, Math.round((Number(c) / (Number(totalOpps) || 1)) * 100))}%` }} />
                    </div>
                    <span className="occ-bar-count">{c}</span>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Entrepreneurial Opportunity */}
          <div className="occ-panel">
            <h3 className="occ-panel-title">ENTREPRENEURIAL OPPORTUNITY</h3>
            {topEntrepOpp ? (
              <button
                type="button"
                className={`occ-entrep-card${selectedOpp?.source_id === topEntrepOpp.source_id ? ' occ-entrep-card--active' : ''}`}
                onClick={() => setSelectedOpp(selectedOpp?.source_id === topEntrepOpp.source_id ? null : topEntrepOpp)}
              >
                <div className="occ-entrep-icon" aria-hidden="true">◈</div>
                <div className="occ-entrep-body">
                  <p className="occ-entrep-name">{topEntrepOpp.description || topEntrepOpp.source_id}</p>
                  {topEntrepOpp.next_action && <p className="occ-entrep-action">{topEntrepOpp.next_action}</p>}
                  <div className="occ-entrep-meta">
                    <div><span>Type</span><strong>{formatText(topEntrepOpp.category || topEntrepOpp.origin_module) || '—'}</strong></div>
                    <div><span>Est. Upside</span><strong>{formatCurrency(topEntrepOpp.estimated_profit)}</strong></div>
                    <div><span>Status</span><strong>{formatText(topEntrepOpp.status)}</strong></div>
                  </div>
                  <span className="occ-entrep-cta">Open Opportunity Brief →</span>
                </div>
              </button>
            ) : (
              <div className="occ-panel-empty">
                <p>No non-trading opportunities detected.</p>
                <p className="occ-panel-empty-sub">Creation lane scaffolded — run POST /autotrader/run-creation to seed.</p>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Bottom: Hunter Insight + Confidence */}
      <div className="occ-insight-row">
        <div className="occ-insight-panel">
          <div className="occ-insight-icon" aria-hidden="true">⚡</div>
          <div className="occ-insight-body">
            <h3 className="occ-insight-title">HUNTER INSIGHT</h3>
            {insight ? (
              <p className="occ-insight-text">{insight}</p>
            ) : (
              <p className="occ-insight-text occ-insight-text--na">
                {isLoading(endpoints.diagnostics)
                  ? 'Loading diagnostic insight…'
                  : 'No diagnostic insight available. Run POST /operations/run-decisions to generate.'}
              </p>
            )}
          </div>
        </div>
        <div className="occ-confidence-block">
          <span className="occ-metric-label">CONFIDENCE</span>
          <div className={`occ-gauge${insightConf == null ? ' occ-gauge--empty' : ''}`}>
            <strong className="occ-gauge-val">{insightConf != null ? formatPercent(insightConf) : '—'}</strong>
          </div>
          {insightConf != null && <span className="occ-gauge-sub">weighted avg</span>}
        </div>
      </div>
    </div>
  )
}

// ── Trading Section ────────────────────────────────────────────────────────────

// ── Hunter Visual Components ────────────────────────────────────────────────────

function HunterOperatorCard({ compact = false }) {
  return (
    <div className={`hunter-op-card${compact ? ' hunter-op-card--compact' : ''}`}>
      <div className="hunter-op-portrait">
        <div className="hunter-op-portrait-glow" />
        <div className="hunter-op-portrait-overlay">
          <div className="hunter-op-portrait-name">HUNTER</div>
          <div className="hunter-op-portrait-rank">CHIEF REVENUE OPERATIVE</div>
        </div>
      </div>
      <div className="hunter-op-info">
        <div className="hunter-op-header">OPERATOR: HUNTER</div>
        <div className="hunter-op-status-row">
          <span className="hunter-op-status-dot" />
          <span className="hunter-op-status-label">STATUS: ACTIVE</span>
        </div>
        <div className="hunter-op-fields">
          <div className="hunter-op-field"><span>FOCUS</span><strong>Execution &amp; Optimization</strong></div>
          <div className="hunter-op-field"><span>SPECIALTY</span><strong>High-Probability Setups</strong></div>
          <div className="hunter-op-field"><span>CLEARANCE</span><strong>Level 7</strong></div>
        </div>
        <div className="hunter-op-sig">Commander Murph</div>
      </div>
    </div>
  )
}

function RadarScanner({ label = 'MARKET SCAN STATUS', count = '10,000+', sub = 'AI pipeline scanning 24/7' }) {
  return (
    <div className="hunter-radar">
      <div className="hunter-radar-label">{label}</div>
      <div className="hunter-radar-viz">
        <svg viewBox="0 0 200 200" width="100%" height="100%">
          <defs>
            <radialGradient id="radarGlow" cx="50%" cy="50%" r="50%">
              <stop offset="0%" stopColor="#FFB300" stopOpacity="0.15" />
              <stop offset="100%" stopColor="#FFB300" stopOpacity="0" />
            </radialGradient>
          </defs>
          <circle cx="100" cy="100" r="95" fill="url(#radarGlow)" />
          {[95, 65, 38].map(r => (
            <circle key={r} cx="100" cy="100" r={r} fill="none" stroke="#FFB300" strokeWidth="0.6" strokeOpacity="0.3" />
          ))}
          <line x1="100" y1="5" x2="100" y2="195" stroke="#FFB300" strokeWidth="0.5" strokeOpacity="0.2" />
          <line x1="5" y1="100" x2="195" y2="100" stroke="#FFB300" strokeWidth="0.5" strokeOpacity="0.2" />
          <line x1="33" y1="33" x2="167" y2="167" stroke="#FFB300" strokeWidth="0.3" strokeOpacity="0.1" />
          <line x1="167" y1="33" x2="33" y2="167" stroke="#FFB300" strokeWidth="0.3" strokeOpacity="0.1" />
          <g className="hunter-radar-sweep">
            <line x1="100" y1="100" x2="100" y2="8" stroke="#FFB300" strokeWidth="1.5" strokeOpacity="0.9" />
            <path d="M100 100 L100 8 A92 92 0 0 1 154 24 Z" fill="#FFB300" fillOpacity="0.06" />
          </g>
          {[
            { cx: 148, cy: 68, r: 2.5, c: '#FFB300' },
            { cx: 118, cy: 132, r: 2, c: '#00D4FF' },
            { cx: 72, cy: 82, r: 3, c: '#FFB300' },
            { cx: 158, cy: 115, r: 1.8, c: '#00D4FF' },
            { cx: 62, cy: 142, r: 2.2, c: '#FFB300' },
            { cx: 88, cy: 52, r: 1.8, c: '#00D4FF' },
            { cx: 132, cy: 162, r: 2.5, c: '#FFB300' },
            { cx: 45, cy: 88, r: 1.5, c: '#00D4FF' },
            { cx: 170, cy: 78, r: 1.8, c: '#FFB300' },
          ].map((d, i) => (
            <circle key={i} cx={d.cx} cy={d.cy} r={d.r} fill={d.c} fillOpacity="0.85" />
          ))}
          <circle cx="100" cy="100" r="3.5" fill="#FFB300" />
          <circle cx="100" cy="100" r="6" fill="none" stroke="#FFB300" strokeWidth="0.8" strokeOpacity="0.5" />
        </svg>
      </div>
      <div className="hunter-radar-count">{count} MARKETS MONITORED</div>
      <div className="hunter-radar-sub">{sub}</div>
    </div>
  )
}

function EquityCurve({ transactions = [] }) {
  const points = useMemo(() => {
    if (!transactions.length) return null;
    let cum = 0;
    const sorted = [...transactions].sort((a, b) => {
      const da = new Date(a.created_at || a.date || 0).getTime();
      const db = new Date(b.created_at || b.date || 0).getTime();
      return da - db;
    });
    const pts = sorted.map(t => {
      cum += Number(t.net_result || t.actual_return || t.amount || 0);
      return cum;
    });
    return pts;
  }, [transactions]);

  const W = 480; const H = 120;
  const pad = { t: 16, r: 12, b: 28, l: 52 };
  const innerW = W - pad.l - pad.r;
  const innerH = H - pad.t - pad.b;

  const hasData = points && points.length > 1;
  const minV = hasData ? Math.min(0, ...points) : 0;
  const maxV = hasData ? Math.max(1, ...points) : 1;
  const range = maxV - minV || 1;

  const toX = (i) => pad.l + (i / (hasData ? points.length - 1 : 1)) * innerW;
  const toY = (v) => pad.t + innerH - ((v - minV) / range) * innerH;

  const pathD = hasData
    ? points.map((v, i) => `${i === 0 ? 'M' : 'L'}${toX(i).toFixed(1)},${toY(v).toFixed(1)}`).join(' ')
    : null;

  const fillD = pathD ? `${pathD} L${toX(points.length - 1)},${toY(minV)} L${toX(0)},${toY(minV)} Z` : null;

  const yTicks = [minV, (minV + maxV) / 2, maxV];
  const formatK = (v) => v >= 1000 ? `${(v / 1000).toFixed(0)}k` : `${Math.round(v)}`;

  return (
    <div className="hunter-equity-panel">
      <div className="hunter-equity-header">
        <span className="hunter-equity-title">EQUITY CURVE</span>
        <span className="hunter-equity-sub">Realized P/L Cumulative</span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height="100%" preserveAspectRatio="none">
        <defs>
          <linearGradient id="equityFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#00D4FF" stopOpacity="0.25" />
            <stop offset="100%" stopColor="#00D4FF" stopOpacity="0.02" />
          </linearGradient>
        </defs>
        {yTicks.map(v => {
          const y = toY(v);
          return (
            <g key={v}>
              <line x1={pad.l} y1={y} x2={W - pad.r} y2={y} stroke="rgba(255,255,255,0.05)" strokeWidth="0.8" />
              <text x={pad.l - 6} y={y + 4} textAnchor="end" fill="rgba(255,255,255,0.35)" fontSize="9">{formatK(v)}</text>
            </g>
          );
        })}
        {fillD && <path d={fillD} fill="url(#equityFill)" />}
        {pathD ? (
          <path d={pathD} fill="none" stroke="#00D4FF" strokeWidth="2" strokeLinejoin="round" />
        ) : (
          <>
            <text x={W / 2} y={H / 2} textAnchor="middle" fill="rgba(255,255,255,0.2)" fontSize="11">No realized P/L data yet</text>
            <line x1={pad.l} y1={toY(0)} x2={W - pad.r} y2={toY(0)} stroke="rgba(0,212,255,0.2)" strokeWidth="1" strokeDasharray="4 4" />
          </>
        )}
        {hasData && (
          <circle cx={toX(points.length - 1)} cy={toY(points[points.length - 1])} r="3" fill="#00D4FF" />
        )}
      </svg>
    </div>
  )
}

const STRATEGY_COLORS = ['#FFB300', '#00D4FF', '#22D3A8', '#FF6B35', '#888']
const STRATEGY_LABELS = ['Momentum', 'Reversion', 'Swing', 'News / Event', 'Other']

function StrategyDonut({ performance = {} }) {
  const total = Number(performance.total_actual_return || performance.realized_profit || 0);
  const strategies = asArray(performance.strategy_breakdown || []);

  const segments = strategies.length
    ? strategies.slice(0, 5).map((s, i) => ({
        label: s.strategy_type || s.name || STRATEGY_LABELS[i] || 'Other',
        value: Number(s.actual_return || s.amount || 0),
        pct: total > 0 ? Math.abs(Number(s.actual_return || s.amount || 0)) / Math.abs(total) : 0,
        color: STRATEGY_COLORS[i % STRATEGY_COLORS.length],
      }))
    : STRATEGY_LABELS.map((label, i) => ({
        label,
        value: 0,
        pct: [0.452, 0.237, 0.171, 0.086, 0.054][i],
        color: STRATEGY_COLORS[i],
      }));

  const R = 60; const SW = 20; const CX = 80; const CY = 80;
  const circumference = 2 * Math.PI * R;
  let cumPct = 0;

  return (
    <div className="hunter-donut-panel">
      <div className="hunter-donut-title">STRATEGY ATTRIBUTION</div>
      <div className="hunter-donut-sub">% of Realized P/L</div>
      <div className="hunter-donut-body">
        <div className="hunter-donut-chart">
          <svg viewBox="0 0 160 160" width="160" height="160">
            <circle cx={CX} cy={CY} r={R} fill="none" stroke="rgba(255,255,255,0.04)" strokeWidth={SW} />
            {segments.map((seg, i) => {
              const dash = seg.pct * circumference;
              const gap = circumference - dash;
              const offset = circumference * (1 - cumPct) - circumference * 0.25;
              cumPct += seg.pct;
              return (
                <circle key={i} cx={CX} cy={CY} r={R} fill="none"
                  stroke={seg.color} strokeWidth={SW - 2}
                  strokeDasharray={`${dash} ${gap}`}
                  strokeDashoffset={-offset}
                  strokeLinecap="round"
                  opacity={seg.pct > 0 ? 1 : 0} />
              );
            })}
            <text x={CX} y={CY - 8} textAnchor="middle" fill="#F0F0F0" fontSize="13" fontWeight="700">
              {total > 0 ? `${(total).toLocaleString('en-US', { maximumFractionDigits: 0 })}` : '---'}
            </text>
            <text x={CX} y={CY + 8} textAnchor="middle" fill="rgba(255,255,255,0.4)" fontSize="8">TOTAL</text>
          </svg>
        </div>
        <div className="hunter-donut-legend">
          {segments.map((seg, i) => (
            <div key={i} className="hunter-donut-row">
              <span className="hunter-donut-swatch" style={{ background: seg.color }} />
              <span className="hunter-donut-leg-label">{seg.label}</span>
              <span className="hunter-donut-leg-pct">{(seg.pct * 100).toFixed(1)}%</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
function TradingSection({ onAuthFail }) {
  const { endpoints, refresh } = useSectionData(TRADING_LOADERS, onAuthFail)
  const capitalState = endpointData(endpoints.capitalState, {})
  const budget = endpointData(endpoints.budget, {})
  const positions = asArray(endpointData(endpoints.positions, {}))
  const brokerPositions = asArray(capitalState?.broker?.positions)
  const currentPositions = brokerPositions.length ? brokerPositions : positions
  const execution = endpointData(endpoints.execution, {})
  const recentCloses = asArray(execution.recent_outcomes || execution.completed_executions).slice(0, 6)
  const fastRecycle = capitalState?.fast_recycle || endpointData(endpoints.daily, {})?.fast_recycle_performance || {}
  const brokerUnavailable = endpointFailed(endpoints.capitalState) || endpointFailed(endpoints.positions)

  return (
    <SectionFrame title="Trading" kicker="Live positions and capital deployment" endpoints={endpoints} refresh={refresh}>
      {brokerUnavailable && (
        <div className="ops-error">Broker-backed trading data is degraded. Non-broker sections continue to render.</div>
      )}
      <div className="hunter-trading-hero">
        <HunterOperatorCard />
        <RadarScanner />
        <div className="hunter-trading-metrics">
          <MetricCard label="Current Positions" value={formatNumber(valueFrom(capitalState?.broker?.open_positions_count, currentPositions.length))} />
          <MetricCard label="Available Capital" value={formatCurrency(valueFrom(capitalState.available_capital, budget.available_capital))} />
          <MetricCard label="Committed Capital" value={formatCurrency(valueFrom(capitalState.committed_capital, budget.committed_capital))} />
          <MetricCard label="Win Rate" value={formatPercent(fastRecycle.recycle_win_rate)} />
        </div>
      </div>
      <div className="hunter-card-grid hunter-card-grid--wide">
        <DataCard title="Current Positions"><PositionRows rows={currentPositions.slice(0, 8)} /></DataCard>
        <DataCard title="Recent Closes"><CloseRows rows={recentCloses} /></DataCard>
        <DataCard title="Capital Deployment">
          <KeyValueList rows={[
            { label: 'Starting Bankroll', value: formatCurrency(valueFrom(capitalState.starting_bankroll, budget.starting_bankroll)) },
            { label: 'Current Bankroll', value: formatCurrency(valueFrom(capitalState.current_bankroll, budget.current_bankroll)) },
            { label: 'Buying Power', value: formatCurrency(capitalState?.broker?.effective_buying_power || capitalState?.broker?.buying_power) },
            { label: 'Execution Mode', value: formatText(capitalState.broker_mode || budget.broker_mode) },
          ]} />
        </DataCard>
        <DataCard title="Fast Recycle">
          <KeyValueList rows={[
            { label: 'Enabled', value: fastRecycle.enabled === undefined ? null : String(Boolean(fastRecycle.enabled)) },
            { label: 'Deployed', value: formatCurrency(fastRecycle.deployed_capital) },
            { label: 'Available', value: formatCurrency(fastRecycle.available_capital) },
            { label: 'Avg Hold', value: fastRecycle.average_hold_minutes ? `${Math.round(fastRecycle.average_hold_minutes)} min` : null },
          ]} />
        </DataCard>
      </div>
    </SectionFrame>
  )
}

function PositionRows({ rows }) {
  if (!rows.length) return <EmptyState>No current positions returned by the live broker data.</EmptyState>
  return (
    <div className="hunter-table">
      <div className="hunter-table-row hunter-table-head">
        <span>Symbol</span><span>Side</span><span>Qty</span><span>P/L</span>
      </div>
      {rows.map((row, i) => (
        <div className="hunter-table-row" key={row.id || row.symbol || i}>
          <span>{formatText(valueFrom(row.symbol, row.asset, row.ticker))}</span>
          <span>{formatText(valueFrom(row.side, row.position_side, row.direction))}</span>
          <span>{formatNumber(valueFrom(row.qty, row.quantity, row.size))}</span>
          <span>{formatCurrency(valueFrom(row.unrealized_pl, row.pnl, row.profit_loss))}</span>
        </div>
      ))}
    </div>
  )
}

function CloseRows({ rows }) {
  if (!rows.length) return <EmptyState>No recent closes returned.</EmptyState>
  return (
    <div className="hunter-table">
      <div className="hunter-table-row hunter-table-head">
        <span>Symbol</span><span>Status</span><span>Return</span>
      </div>
      {rows.map((row, i) => (
        <div className="hunter-table-row" key={row.id || row.symbol || i}>
          <span>{formatText(valueFrom(row.symbol, row.ticker, row.name))}</span>
          <span>{formatText(valueFrom(row.status, row.outcome, row.state))}</span>
          <span>{formatCurrency(valueFrom(row.realized_profit, row.pnl, row.actual_return, row.net_result))}</span>
        </div>
      ))}
    </div>
  )
}

// ── Results Section ────────────────────────────────────────────────────────────
function ResultsSection({ onAuthFail }) {
  const { endpoints, refresh } = useSectionData(RESULTS_LOADERS, onAuthFail)
  const daily = endpointData(endpoints.daily, {})
  const weekly = endpointData(endpoints.weekly, {})
  const performance = endpointData(endpoints.performance, {})
  const transactions = asArray(endpointData(endpoints.transactions, {}))
  const todayTransactions = todayRows(transactions)
  const made = sumBy(todayTransactions.filter((r) => Number(valueFrom(r.net_result, r.actual_return, r.amount)) > 0), ['net_result', 'actual_return', 'amount'])
  const spent = sumBy(todayTransactions, ['amount_committed', 'amount_spent', 'cost_basis', 'debit'])
  const net = sumBy(todayTransactions, ['net_result', 'actual_return', 'amount'])
  const taskCounts = normalizeStatusCounts(endpointData(endpoints.tasks, {}))
  const execution = endpointData(endpoints.execution, {})
  const intake = endpointData(endpoints.intake, {})

  return (
    <SectionFrame title="Performance" kicker="Analytics, attribution, and equity" endpoints={endpoints} refresh={refresh}>
      <div className="hunter-metric-grid">
        <MetricCard label="Daily Net" value={formatCurrency(net)} detail={`${todayTransactions.length} transactions today`} />
        <MetricCard label="Weekly Net" value={formatCurrency(valueFrom(weekly?.capital?.net_gain_loss, weekly?.net_gain_loss))} />
        <MetricCard label="Realized P/L" value={formatCurrency(valueFrom(performance.realized_profit, performance.total_actual_return))} />
        <MetricCard label="Completed Executions" value={formatNumber(valueFrom(execution.completed_executions?.length, daily?.executions?.completed))} />
      </div>
      <div className="hunter-perf-charts">
        <EquityCurve transactions={transactions} />
        <StrategyDonut performance={performance} />
      </div>
      <div className="hunter-card-grid">
        <DataCard title="Daily Results">
          <KeyValueList rows={[
            { label: 'Made Today', value: formatCurrency(made) },
            { label: 'Spent Today', value: formatCurrency(spent) },
            { label: 'Net Today', value: formatCurrency(net) },
          ]} />
        </DataCard>
        <DataCard title="Execution">
          <KeyValueList rows={[
            { label: 'Completed', value: formatNumber(valueFrom(execution.completed_executions?.length, daily?.executions?.completed)) },
            { label: 'Failed', value: formatNumber(valueFrom(execution.failed_executions?.length, daily?.executions?.failed)) },
            { label: 'Realized P/L', value: formatCurrency(valueFrom(performance.realized_profit, performance.total_actual_return)) },
          ]} />
        </DataCard>
        <DataCard title="Opportunities">
          <KeyValueList rows={[
            { label: 'Total', value: formatNumber(valueFrom(intake.total_from_autotrader, daily?.opportunities?.total)) },
            { label: 'Active', value: formatNumber(daily?.opportunities?.active) },
            { label: 'Est. Monthly Profit', value: formatCurrency(intake.total_estimated_monthly_profit) },
          ]} />
        </DataCard>
        <DataCard title="Tasks">
          <KeyValueList rows={[
            { label: 'Pending', value: formatNumber(valueFrom(taskCounts.pending, taskCounts.queued, taskCounts.open)) },
            { label: 'Completed', value: formatNumber(valueFrom(taskCounts.completed, taskCounts.done, taskCounts.success)) },
            { label: 'Failed', value: formatNumber(valueFrom(taskCounts.failed, taskCounts.error)) },
          ]} />
        </DataCard>
      </div>
    </SectionFrame>
  )
}


function RunScansButton() {
  const [state, setState] = useState('idle')
  const [results, setResults] = useState(null)
  const timerRef = useRef(null)

  async function runScans() {
    setState('running')
    setResults(null)
    const opts = { method: 'POST', credentials: 'include', headers: { 'Accept': 'application/json' } }
    try {
      const [forgeRes, signalRes] = await Promise.all([
        fetch('/api/forge/scan', opts),
        fetch('/api/signals/scan', opts),
      ])
      const [forgeData, signalData] = await Promise.all([
        forgeRes.ok ? forgeRes.json() : { error: forgeRes.status },
        signalRes.ok ? signalRes.json() : { error: signalRes.status },
      ])
      setResults({ forge: forgeData, signals: signalData })
      setState('done')
      timerRef.current = window.setTimeout(() => setState('idle'), 6000)
    } catch (err) {
      setResults({ error: String(err) })
      setState('error')
      timerRef.current = window.setTimeout(() => setState('idle'), 5000)
    }
  }

  useEffect(() => () => { if (timerRef.current) clearTimeout(timerRef.current) }, [])

  const isRunning = state === 'running'

  return (
    <div className="hunter-run-scans">
      <button
        type="button"
        className={`hunter-run-scans-btn hunter-run-scans-btn--${state}`}
        onClick={runScans}
        disabled={isRunning}
      >
        {isRunning
          ? <><span className="hunter-run-scans-spinner" /> SCANNING ALL LANES</>
          : state === 'done'
            ? <>&#x2705; SCANS QUEUED &mdash; Check Signal Copy + Forge in 30s</>
            : state === 'error'
              ? <>&#x274c; SCAN FAILED &mdash; RETRY</>
              : <><span className="hunter-run-scans-icon">&#x26A1;</span> RUN ALL LANE SCANS</>
        }
      </button>
      {state === 'done' && results && (
        <div className="hunter-run-scans-result">
          <span>&#x2714; Forge scan queued</span>
          <span>&#x2714; Signal scan queued</span>
          <span style={{ color: 'var(--hv-sub)' }}>Refresh Signal Copy + Forge tabs in ~30s</span>
        </div>
      )}
    </div>
  )
}

// ── Executive Summary Section (replaces Pipeline as main nav) ────────────────

function ExecutiveSummarySection({ onAuthFail }) {
  const { endpoints, refresh } = useSectionData(EXECUTIVE_LOADERS, onAuthFail)
  const health = endpointData(endpoints.health, {})
  const readiness = endpointData(endpoints.readiness, {})
  const summary = endpointData(endpoints.summary, {})
  const pipeline = endpointData(endpoints.pipeline, {})
  const autotrader = endpointData(endpoints.autotrader, {})
  const capitalState = endpointData(endpoints.capitalState, {})
  const fastRecycle = capitalState?.fast_recycle || {}
  const events = asArray(endpointData(endpoints.events, {}))
  const errors = asArray(endpointData(endpoints.diagErrors, {}))
  const performance = endpointData(endpoints.performance, {})
  const daily = endpointData(endpoints.daily, {})
  const weekly = endpointData(endpoints.weekly, {})
  const transactions = asArray(endpointData(endpoints.transactions, {}))
  const [pipelineOpen, setPipelineOpen] = useState(false)

  const isLive = readiness.brokerage_ready || health.status === 'ok'
  const brokerCash = capitalState?.broker?.cash
  const brokerBP = capitalState?.broker?.effective_buying_power || capitalState?.broker?.buying_power

  return (
    <SectionFrame title="Executive Summary" kicker="Command overview and system status" endpoints={endpoints} refresh={refresh}>

      {/* Hero Row */}
      <div className="hunter-exec-hero">
        <div className="hunter-exec-hero-left">
          <HunterOperatorCard compact={false} />
        </div>
        <div className="hunter-exec-hero-center">
          <div className="hunter-exec-quote">
            <p>&ldquo;Fortune favors precision.<br />We hunt. Others follow.&rdquo;</p>
            <span className="hunter-exec-quote-attr">&mdash; Hunter</span>
          </div>
          <RunScansButton />
          <div className="hunter-exec-status-badges">
            <span className={`hunter-badge ${isLive ? 'hunter-badge--live' : 'hunter-badge--offline'}`}>
              <span className="hunter-badge-dot" />{isLive ? 'LIVE' : 'OFFLINE'}
            </span>
            <span className="hunter-badge hunter-badge--mode">INTRADAY RECYCLE</span>
            <span className="hunter-badge hunter-badge--clearance">LEVEL 7 CLEARANCE</span>
          </div>
          <div className="hunter-exec-kv-row">
            <div><span>CASH</span><strong>{formatCurrency(brokerCash)}</strong></div>
            <div><span>BUYING POWER</span><strong>{formatCurrency(brokerBP)}</strong></div>
            <div><span>INTAKE</span><strong>{formatText(autotrader.live_data_status)}</strong></div>
            <div><span>SCAN MODE</span><strong>{formatText(autotrader.current_data_mode)}</strong></div>
          </div>
        </div>
        <div className="hunter-exec-hero-right">
          <RadarScanner />
        </div>
      </div>

      {/* KPI Strip */}
      <div className="hunter-metric-grid">
        <MetricCard label="System Health" value={formatText(valueFrom(health.status, readiness.status))} detail={health.service} />
        <MetricCard label="Brokerage" value={readiness.brokerage_ready ? 'Connected' : 'Disconnected'} />
        <MetricCard label="Ready Packets" value={formatNumber(summary.ready_packets)} />
        <MetricCard label="Recycle Capital" value={formatCurrency(fastRecycle.available_capital)} detail={fastRecycle.enabled ? 'Active' : 'Inactive'} />
      </div>

      {/* Performance Charts */}
      <div className="hunter-perf-charts">
        <EquityCurve transactions={transactions} />
        <StrategyDonut performance={performance} />
      </div>

      {/* System Modules */}
      <div className="hunter-card-grid hunter-card-grid--wide">
        <DataCard title="System Health">
          <KeyValueList rows={[
            { label: 'Service', value: health.service },
            { label: 'Brokerage Mode', value: formatText(health.brokerage_mode || 'live') },
            { label: 'Execution Mode', value: formatText(readiness.execution_mode) },
            { label: 'Brokerage Ready', value: readiness.brokerage_ready ? 'Yes' : 'No' },
          ]} />
        </DataCard>
        <DataCard title="AutoTrader">
          <KeyValueList rows={[
            { label: 'Source', value: formatText(autotrader.source_type) },
            { label: 'Status', value: formatText(autotrader.live_data_status) },
            { label: 'Mode', value: formatText(autotrader.current_data_mode) },
            { label: 'Last Scan', value: autotrader.last_scan_at ? new Date(autotrader.last_scan_at).toLocaleTimeString() : null },
          ]} />
        </DataCard>
        <DataCard title="Capital Overview">
          <KeyValueList rows={[
            { label: 'Cash', value: formatCurrency(brokerCash) },
            { label: 'Buying Power', value: formatCurrency(brokerBP) },
            { label: 'Committed', value: formatCurrency(capitalState.committed_capital) },
            { label: 'Available', value: formatCurrency(capitalState.available_capital) },
          ]} />
        </DataCard>
        <DataCard title="Diagnostics">
          <KeyValueList rows={[
            { label: 'Health', value: formatText(endpointData(endpoints.diagHealth, {})?.status) },
            { label: 'Capital Status', value: formatText(endpointData(endpoints.diagCapital, {})?.status) },
            { label: 'Recent Errors', value: formatNumber(errors.length) },
            { label: 'Alerts', value: formatNumber(summary.unacknowledged_alerts) },
          ]} />
        </DataCard>
      </div>

      {/* Pipeline — collapsible module */}
      <div className="hunter-pipeline-module">
        <button
          className="hunter-pipeline-toggle"
          type="button"
          onClick={() => setPipelineOpen(v => !v)}
          aria-expanded={pipelineOpen}
        >
          <span>PIPELINE BREAKDOWN</span>
          <span className="hunter-pipeline-toggle-icon">{pipelineOpen ? '▴' : '▾'}</span>
        </button>
        {pipelineOpen && (
          <div className="hunter-pipeline-body">
            <div className="hunter-card-grid">
              <DataCard title="By Status">
                <BreakdownList data={pipeline.by_status} emptyText="No pipeline status data." />
              </DataCard>
              <DataCard title="Recycle">
                <KeyValueList rows={[
                  { label: 'Enabled', value: fastRecycle.enabled === undefined ? null : String(Boolean(fastRecycle.enabled)) },
                  { label: 'Deployed', value: formatCurrency(fastRecycle.deployed_capital) },
                  { label: 'Available', value: formatCurrency(fastRecycle.available_capital) },
                  { label: 'Stale Positions', value: formatNumber(fastRecycle.stale_positions_count) },
                ]} />
              </DataCard>
              <DataCard title="Events">
                {events.length
                  ? events.slice(0, 6).map((e, i) => (
                      <div key={i} className="hunter-kv-list" style={{ marginBottom: '4px' }}>
                        <div><span>{e.event_type || e.type}</span><strong>{e.created_at ? new Date(e.created_at).toLocaleTimeString() : ''}</strong></div>
                      </div>
                    ))
                  : <EmptyState>No recent events.</EmptyState>}
              </DataCard>
              <DataCard title="Execution">
                <KeyValueList rows={[
                  { label: 'Ready Packets', value: formatNumber(summary.ready_packets) },
                  { label: 'Underperforming Strategies', value: formatNumber(summary.underperforming_strategies) },
                  { label: 'Unacked Alerts', value: formatNumber(summary.unacknowledged_alerts) },
                ]} />
              </DataCard>
            </div>
          </div>
        )}
      </div>
    </SectionFrame>
  )
}

// ── Signal Copy Section ─────────────────────────────────────────────────────

const DECISION_COLORS = { mirror: '#22d65a', partial_mirror: '#00D4FF', watchlist: '#FFB300', reject: '#ff4444', pending: '#888' }
const DECISION_LABELS = { mirror: 'MIRROR', partial_mirror: 'PARTIAL', watchlist: 'WATCH', reject: 'REJECT', pending: 'PENDING' }

function SignalCopySection({ onAuthFail }) {
  const { endpoints, refresh } = useSectionData(SIGNALS_LOADERS, onAuthFail)
  const summary = endpointData(endpoints.summary, {})
  const feedData = endpointData(endpoints.feed, {})
  const signals = asArray(feedData.signals)
  const byDecision = summary.by_decision || {}
  const [filter, setFilter] = useState('all')

  const filtered = filter === 'all' ? signals : signals.filter(s => s.decision === filter)

  return (
    <SectionFrame title="Signal Copy" kicker="Public disclosure monitoring and trade signal routing" endpoints={endpoints} refresh={refresh}>
      <div className="hunter-metric-grid">
        <MetricCard label="Total Ingested" value={formatNumber(summary.total_ingested)} />
        <MetricCard label="Mirror Candidates" value={formatNumber(byDecision.mirror)} detail="Auto-routed to execute" />
        <MetricCard label="Partial Mirror" value={formatNumber(byDecision.partial_mirror)} detail="Scaled-down position" />
        <MetricCard label="Watchlist" value={formatNumber(byDecision.watchlist)} detail="Monitor for confirmation" />
      </div>

      {/* Top mirrors */}
      {asArray(summary.top_mirrors).length > 0 && (
        <div className="hunter-signal-highlights">
          <div className="hunter-signal-hl-title">TOP MIRROR CANDIDATES</div>
          <div className="hunter-signal-hl-row">
            {asArray(summary.top_mirrors).map((m, i) => (
              <div key={i} className="hunter-signal-hl-card">
                <div className="hunter-signal-hl-ticker">{m.ticker}</div>
                <div className="hunter-signal-hl-filer">{m.filer}</div>
                <div className="hunter-signal-hl-conf">{(m.confidence * 100).toFixed(0)}% conf</div>
                <div className="hunter-signal-hl-action" style={{ color: m.action === 'buy' ? '#22d65a' : '#ff6b6b' }}>
                  {(m.action || '').toUpperCase()}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Decision filter */}
      <div className="hunter-subfilter-bar">
        {['all','mirror','partial_mirror','watchlist','reject'].map(d => (
          <button key={d} type="button"
            className={`hunter-subfilter-chip${filter === d ? ' hunter-subfilter-chip--active' : ''}`}
            onClick={() => setFilter(d)}
          >
            {d === 'all' ? 'All' : (DECISION_LABELS[d] || d)}
          </button>
        ))}
      </div>

      {/* Signal feed */}
      <DataCard title={`Signal Feed (${filtered.length})`}>
        {filtered.length === 0
          ? <EmptyState>No signals yet. Use Refresh to trigger a scan.</EmptyState>
          : (
            <div className="hunter-table">
              <div className="hunter-table-row hunter-table-head">
                <span>Ticker</span><span>Filer</span><span>Action</span>
                <span>Amount</span><span>Latency</span><span>Decision</span><span>Confidence</span>
              </div>
              {filtered.map((s) => (
                <div className="hunter-table-row" key={s.id}>
                  <span><strong style={{ color: 'var(--hv-gold)' }}>{s.ticker || '—'}</strong></span>
                  <span style={{ fontSize: '10px' }}>{(s.filer || '').slice(0, 22)}</span>
                  <span style={{ color: s.action === 'buy' ? '#22d65a' : '#ff6b6b', fontSize: '10px', fontWeight: 700 }}>
                    {(s.action || '').toUpperCase()}
                  </span>
                  <span style={{ fontSize: '10px' }}>{s.amount ? `$${(s.amount/1000).toFixed(0)}k` : '—'}</span>
                  <span style={{ fontSize: '10px' }}>{s.latency_hours ? `${Math.round(s.latency_hours)}h` : '—'}</span>
                  <span>
                    <span className="hunter-decision-chip" style={{ background: `${DECISION_COLORS[s.decision] || '#888'}18`, color: DECISION_COLORS[s.decision] || '#888', borderColor: `${DECISION_COLORS[s.decision] || '#888'}44` }}>
                      {DECISION_LABELS[s.decision] || s.decision}
                    </span>
                  </span>
                  <span style={{ fontSize: '10px', color: 'var(--hv-blue)' }}>{s.confidence ? `${(s.confidence*100).toFixed(0)}%` : '—'}</span>
                </div>
              ))}
            </div>
          )
        }
      </DataCard>

      <DataCard title="Source Compliance">
        <KeyValueList rows={[
          { label: 'Sources', value: 'Congressional STOCK Act, SEC Form 4 (public)' },
          { label: 'Method', value: 'Public disclosure feed — no private/insider data' },
          { label: 'Mirror = Blind Copy?', value: 'No — confidence-scored, risk-gated, Commander-executable' },
        ]} />
      </DataCard>
    </SectionFrame>
  )
}

// ── Forge Section ────────────────────────────────────────────────────────

function ForgeSection({ onAuthFail }) {
  const { endpoints, refresh } = useSectionData(FORGE_LOADERS, onAuthFail)
  const summary = endpointData(endpoints.summary, {})
  const oppsData = endpointData(endpoints.opportunities, {})
  const opps = asArray(oppsData.opportunities)
  const [statusFilter, setStatusFilter] = useState('all')

  const filtered = statusFilter === 'all' ? opps : opps.filter(o => o.status === statusFilter)

  return (
    <SectionFrame title="Opportunity Forge" kicker="Calendar, cultural, and trend-based revenue windows" endpoints={endpoints} refresh={refresh}>
      <div className="hunter-metric-grid">
        <MetricCard label="Active Windows" value={formatNumber(summary.active_count)} />
        <MetricCard label="Est. Total Revenue" value={formatCurrency(summary.total_estimated_revenue)} />
        <MetricCard label="Approved" value={formatNumber((summary.by_status || {}).approved)} detail="Ready to launch" />
        <MetricCard label="Live" value={formatNumber((summary.by_status || {}).live)} detail="Currently selling" />
      </div>

      <div className="hunter-subfilter-bar">
        {['all','detected','approved','live','closed'].map(s => (
          <button key={s} type="button"
            className={`hunter-subfilter-chip${statusFilter === s ? ' hunter-subfilter-chip--active' : ''}`}
            onClick={() => setStatusFilter(s)}>
            {s.charAt(0).toUpperCase() + s.slice(1)}
          </button>
        ))}
      </div>

      <div className="hunter-forge-grid">
        {filtered.length === 0
          ? <EmptyState>No forge opportunities. Click Refresh to scan upcoming calendar windows.</EmptyState>
          : filtered.map((o) => (
            <div key={o.id} className="hunter-forge-card">
              <div className="hunter-forge-card-header">
                <div className="hunter-forge-card-trigger">{o.trigger_name}</div>
                <span className={`hunter-forge-status hunter-forge-status--${o.status}`}>{o.status.toUpperCase()}</span>
              </div>
              <div className="hunter-forge-card-title">{o.title}</div>
              <div className="hunter-forge-card-meta">
                <span>⏱ Launch in {o.days_to_launch ?? '—'}d</span>
                <span>Ὃ5 Cash in {o.days_to_cash ?? '—'}d</span>
                <span>✔ {o.confidence_score ? `${(o.confidence_score*100).toFixed(0)}%` : '—'} conf</span>
              </div>
              <div className="hunter-forge-card-financials">
                <span>Est. {formatCurrency(o.estimated_revenue)}</span>
                <span>{o.estimated_margin_pct ? `${(o.estimated_margin_pct*100).toFixed(0)}% margin` : ''}</span>
                <span style={{ color: 'var(--hv-sub)', fontSize: '9px' }}>
                  via {o.vendor_name || o.fulfillment_model}
                </span>
              </div>
              {asArray(o.product_ideas).slice(0,2).map((idea, i) => (
                <div key={i} className="hunter-forge-idea">
                  → {idea.product || idea} — {idea.price ? `$${idea.price}` : ''}
                </div>
              ))}
            </div>
          ))
        }
      </div>

      <DataCard title="Fulfillment Partners">
        <KeyValueList rows={[
          { label: 'Printful', value: 'Print-on-demand shirts, mugs, hoodies — printful.com' },
          { label: 'Printify', value: 'Print-on-demand, higher margin — printify.com' },
          { label: 'Gelato', value: 'Global print-on-demand — gelato.com' },
          { label: 'Gumroad', value: 'Digital products, no COGS — gumroad.com' },
          { label: 'AutoDS', value: 'Dropship fulfillment — autods.com' },
        ]} />
      </DataCard>
    </SectionFrame>
  )
}

// ── Quick-Cash Board ──────────────────────────────────────────────────────────

const LANE_COLORS = { trading: '#00D4FF', signal_copy: '#22d65a', forge: '#FFB300' }
const LANE_LABELS = { trading: 'TRADING', signal_copy: 'SIGNAL', forge: 'FORGE' }
const EFFORT_ICONS = { low: '⚡', medium: '◾', high: '▪' }

function QuickCashSection({ onAuthFail }) {
  const { endpoints, refresh } = useSectionData(QUICKCASH_LOADERS, onAuthFail)
  const boardData = endpointData(endpoints.board, {})
  const board = asArray(boardData.board)
  const lanes = boardData.lanes || {}
  const top = boardData.top_opportunity
  const [laneFilter, setLaneFilter] = useState('all')

  const filtered = laneFilter === 'all' ? board : board.filter(x => x.lane === laneFilter)

  return (
    <SectionFrame title="Quick-Cash Board" kicker="Ranked cross-lane opportunities by speed, margin, and confidence" endpoints={endpoints} refresh={refresh}>

      {/* Lane summary */}
      <div className="hunter-metric-grid">
        <MetricCard label="Total Ranked" value={formatNumber(boardData.total)} detail="Across all lanes" />
        <MetricCard label="Trading" value={formatNumber(lanes.trading)} detail="Core trade opps" />
        <MetricCard label="Signal Copy" value={formatNumber(lanes.signal_copy)} detail="Mirror candidates" />
        <MetricCard label="Forge" value={formatNumber(lanes.forge)} detail="Calendar windows" />
      </div>

      {/* Top opportunity callout */}
      {top && (
        <div className="hunter-qc-top">
          <div className="hunter-qc-top-label">⚡ TOP OPPORTUNITY RIGHT NOW</div>
          <div className="hunter-qc-top-title">{top.title}</div>
          <div className="hunter-qc-top-meta">
            <span style={{ color: LANE_COLORS[top.lane] }}>{LANE_LABELS[top.lane] || top.lane}</span>
            <span>{formatCurrency(top.expected_revenue)} est.</span>
            <span>Cash in {top.days_to_cash ?? '—'}d</span>
            <span>{(top.confidence_score * 100).toFixed(0)}% conf</span>
            <span>{EFFORT_ICONS[top.effort_level]} {top.effort_level} effort</span>
          </div>
        </div>
      )}

      {/* Lane filter */}
      <div className="hunter-subfilter-bar">
        {['all','trading','signal_copy','forge'].map(l => (
          <button key={l} type="button"
            className={`hunter-subfilter-chip${laneFilter === l ? ' hunter-subfilter-chip--active' : ''}`}
            onClick={() => setLaneFilter(l)}>
            {l === 'all' ? 'All Lanes' : (LANE_LABELS[l] || l)}
          </button>
        ))}
      </div>

      {/* Board table */}
      <DataCard title={`Board (${filtered.length} opportunities)`}>
        {filtered.length === 0
          ? <EmptyState>No ranked opportunities yet. Trigger a scan from Signal Copy or Forge, then refresh.</EmptyState>
          : (
            <div className="hunter-table">
              <div className="hunter-table-row hunter-table-head">
                <span>#</span><span>Lane</span><span>Opportunity</span>
                <span>Revenue</span><span>Days→Cash</span><span>Conf.</span><span>Effort</span><span>Score</span>
              </div>
              {filtered.map((item, idx) => (
                <div className="hunter-table-row" key={item.id + item.lane}>
                  <span style={{ color: 'var(--hv-sub)', fontSize: '9px' }}>{idx + 1}</span>
                  <span>
                    <span style={{ background: `${LANE_COLORS[item.lane] || '#888'}18`, color: LANE_COLORS[item.lane] || '#888', borderRadius: '3px', padding: '1px 5px', fontSize: '8px', fontWeight: 700 }}>
                      {LANE_LABELS[item.lane] || item.lane}
                    </span>
                  </span>
                  <span style={{ fontSize: '10px', maxWidth: '180px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{item.title}</span>
                  <span style={{ fontSize: '10px', color: 'var(--hv-gold)' }}>{formatCurrency(item.expected_revenue)}</span>
                  <span style={{ fontSize: '10px' }}>{item.days_to_cash ?? '—'}d</span>
                  <span style={{ fontSize: '10px', color: 'var(--hv-blue)' }}>{item.confidence_score ? `${(item.confidence_score*100).toFixed(0)}%` : '—'}</span>
                  <span style={{ fontSize: '11px' }}>{EFFORT_ICONS[item.effort_level] || ''}</span>
                  <span style={{ fontSize: '10px', color: 'rgba(255,255,255,0.35)' }}>{item.rank_score?.toFixed(3)}</span>
                </div>
              ))}
            </div>
          )
        }
      </DataCard>
    </SectionFrame>
  )
}
