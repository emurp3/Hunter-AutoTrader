import { useCallback, useEffect, useState } from 'react'
import { useAuth } from '../context/AuthContext'

const API = '/api'
const REQUEST_TIMEOUT_MS = 8000
const BROKER_TIMEOUT_MS = 5000

const SECTIONS = [
  { id: 'opportunities', label: 'Opportunities' },
  { id: 'trading', label: 'Trading' },
  { id: 'results', label: 'Results' },
  { id: 'operations', label: 'Operations' },
]

const OPPORTUNITY_LOADERS = {
  summary: { path: '/operations/summary' },
  intake: { path: '/autotrader/intake-summary' },
  opportunities: { path: '/autotrader/opportunities?limit=50' },
  packets: { path: '/packets/' },
  pipeline: { path: '/operations/pipeline' },
  execution: { path: '/execution/status' },
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

const OPERATIONS_LOADERS = {
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

    if (response.status === 401) {
      throw new AuthError('Authentication required.')
    }

    const text = await response.text()
    let payload = null
    if (text) {
      try {
        payload = JSON.parse(text)
      } catch (error) {
        throw new Error(`Invalid JSON from ${path}`)
      }
    }

    if (!response.ok) {
      const detail = payload?.detail || payload?.message || response.statusText
      throw new Error(`${response.status} ${detail}`)
    }

    return payload
  } catch (error) {
    if (error?.name === 'AbortError') {
      const timeoutError = new Error(`Timed out after ${timeoutMs / 1000}s`)
      timeoutError.isTimeout = true
      throw timeoutError
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

    setEndpoints((previous) => {
      const next = { ...previous }
      for (const key of Object.keys(loaders)) {
        next[key] = {
          status: previous[key]?.data ? 'refreshing' : 'loading',
          data: previous[key]?.data ?? null,
          error: null,
          path: loaders[key].path,
        }
      }
      return next
    })

    for (const [key, loader] of Object.entries(loaders)) {
      requestJson(loader.path, { timeoutMs: loader.timeoutMs })
        .then((data) => {
          if (cancelled) return
          setEndpoints((previous) => ({
            ...previous,
            [key]: { status: 'success', data, error: null, path: loader.path },
          }))
        })
        .catch((error) => {
          if (cancelled) return
          if (error instanceof AuthError) {
            onAuthFail?.()
            return
          }
          setEndpoints((previous) => ({
            ...previous,
            [key]: { status: 'error', data: previous[key]?.data ?? null, error, path: loader.path },
          }))
        })
    }

    return () => {
      cancelled = true
    }
  }, [loaders, onAuthFail, refreshIndex])

  const refresh = useCallback(() => setRefreshIndex((value) => value + 1), [])

  return { endpoints, refresh }
}

function initialEndpointState(loaders) {
  return Object.fromEntries(
    Object.entries(loaders).map(([key, loader]) => [
      key,
      { status: 'idle', data: null, error: null, path: loader.path },
    ]),
  )
}

function endpointData(endpoint, fallback = null) {
  return endpoint?.data ?? fallback
}

function endpointFailed(endpoint) {
  return endpoint?.status === 'error'
}

function isLoading(endpoint) {
  return endpoint?.status === 'loading' || endpoint?.status === 'refreshing' || endpoint?.status === 'idle'
}

function formatCurrency(value, options = {}) {
  if (value === null || value === undefined || value === '') return 'Unavailable'
  if (!Number.isFinite(Number(value))) return 'Unavailable'
  return Number(value).toLocaleString(undefined, {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: options.compact ? 0 : 2,
  })
}

function formatNumber(value) {
  if (value === null || value === undefined || value === '') return 'Unavailable'
  if (!Number.isFinite(Number(value))) return 'Unavailable'
  return Number(value).toLocaleString()
}

function formatPercent(value) {
  if (value === null || value === undefined || value === '') return 'Unavailable'
  if (!Number.isFinite(Number(value))) return 'Unavailable'
  const number = Math.abs(Number(value)) <= 1 ? Number(value) * 100 : Number(value)
  return `${number.toFixed(1)}%`
}

function formatText(value) {
  if (value === null || value === undefined || value === '') return 'Unavailable'
  return String(value)
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (letter) => letter.toUpperCase())
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
  for (const value of values) {
    if (value !== null && value !== undefined && value !== '') return value
  }
  return null
}

function countBy(rows, fields) {
  const counts = {}
  for (const row of rows) {
    const value = valueFrom(...fields.map((field) => row?.[field]))
    if (!value) continue
    counts[value] = (counts[value] || 0) + 1
  }
  return counts
}

function sumBy(rows, fields) {
  return rows.reduce((total, row) => {
    const value = valueFrom(...fields.map((field) => row?.[field]))
    const number = Number(value)
    return Number.isFinite(number) ? total + number : total
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
  if (text.includes('operational') || text.includes('active') || text.includes('ok') || text.includes('ready')) {
    return 'good'
  }
  if (text.includes('degraded') || text.includes('warning') || text.includes('fallback')) return 'warn'
  if (text.includes('error') || text.includes('failed') || text.includes('down') || text.includes('missing')) return 'bad'
  return 'neutral'
}

export default function OperationsPage({ onBack, onAuthFail }) {
  const { logout } = useAuth()
  const [activeSection, setActiveSection] = useState('opportunities')

  const handleLogout = useCallback(async () => {
    await logout()
    onAuthFail?.()
  }, [logout, onAuthFail])

  return (
    <div className="ops-root">
      <header className="ops-header">
        <div>
          <p className="ops-eyebrow">Hunter Operations v2</p>
          <h1 className="ops-title">Logged-In Command Shell</h1>
          <p className="ops-subtitle">
            Independent operational sections with guarded, truthful live data.
          </p>
        </div>
        <div className="ops-header-actions">
          {onBack && (
            <button className="ops-secondary-button" type="button" onClick={onBack}>
              Public Site
            </button>
          )}
          <button className="ops-action-button" type="button" onClick={handleLogout}>
            Sign Out
          </button>
        </div>
      </header>

      <nav className="hunter-shell-tabs" aria-label="Hunter logged-in sections">
        {SECTIONS.map((section) => (
          <button
            key={section.id}
            type="button"
            className={`hunter-shell-tab${activeSection === section.id ? ' hunter-shell-tab--active' : ''}`}
            onClick={() => setActiveSection(section.id)}
          >
            {section.label}
          </button>
        ))}
      </nav>

      <main className="hunter-shell-body">
        <section hidden={activeSection !== 'opportunities'}>
          <OpportunitiesSection onAuthFail={onAuthFail} />
        </section>
        <section hidden={activeSection !== 'trading'}>
          <TradingSection onAuthFail={onAuthFail} />
        </section>
        <section hidden={activeSection !== 'results'}>
          <ResultsSection onAuthFail={onAuthFail} />
        </section>
        <section hidden={activeSection !== 'operations'}>
          <OperationsSection onAuthFail={onAuthFail} />
        </section>
      </main>
    </div>
  )
}

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
          <button className="ops-secondary-button" type="button" onClick={refresh}>
            Refresh
          </button>
        </div>
      </div>
      {failed.length > 0 && <EndpointErrors endpoints={endpoints} />}
      {children}
    </div>
  )
}

function EndpointErrors({ endpoints }) {
  const failures = Object.entries(endpoints).filter(([, endpoint]) => endpointFailed(endpoint))
  if (!failures.length) return null

  return (
    <div className="hunter-endpoint-errors" role="status">
      {failures.map(([name, endpoint]) => (
        <div key={name}>
          <strong>{formatText(name)}</strong>
          <span>{endpoint.path}: {endpoint.error?.message || 'Request failed'}</span>
        </div>
      ))}
    </div>
  )
}

function MetricCard({ label, value, detail, status }) {
  return (
    <article className="stat-card hunter-metric-card">
      <span className="stat-label">{label}</span>
      <strong className="stat-value">{value}</strong>
      {detail && <span className={`hunter-metric-detail hunter-metric-detail--${statusTone(status || detail)}`}>{detail}</span>}
    </article>
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
  const visible = rows.filter((row) => row.value !== null && row.value !== undefined && row.value !== '')
  if (!visible.length) return <EmptyState />

  return (
    <div className="hunter-kv-list">
      {visible.map((row) => (
        <div key={row.label}>
          <span>{row.label}</span>
          <strong>{row.value}</strong>
        </div>
      ))}
    </div>
  )
}

function BreakdownList({ data, emptyText }) {
  const rows = Object.entries(data || {}).filter(([, value]) => Number(value) !== 0)
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

function OpportunitiesSection({ onAuthFail }) {
  const { endpoints, refresh } = useSectionData(OPPORTUNITY_LOADERS, onAuthFail)
  const summary = endpointData(endpoints.summary, {})
  const intake = endpointData(endpoints.intake, {})
  const opportunities = asArray(endpointData(endpoints.opportunities, {}))
  const packets = asArray(endpointData(endpoints.packets, {}))
  const execution = endpointData(endpoints.execution, {})

  const created = valueFrom(summary.total_opportunities, intake.total_from_autotrader, opportunities.length)
  const building = packets.filter((packet) => ['building', 'draft', 'ready', 'queued'].includes(String(packet?.status || '').toLowerCase())).length
  const executed = valueFrom(
    packets.filter((packet) => ['executed', 'completed'].includes(String(packet?.status || '').toLowerCase())).length || null,
    execution.completed_executions?.length,
  )
  const killed = valueFrom(
    packets.filter((packet) => ['killed', 'failed', 'rejected', 'retired'].includes(String(packet?.status || '').toLowerCase())).length || null,
    execution.failed_executions?.length,
  )

  const byAgent = countBy(opportunities, ['agent', 'assigned_agent', 'created_by', 'owner'])
  const byChannel = intake.by_origin || countBy(opportunities, ['origin', 'origin_module', 'channel', 'source'])
  const byType = intake.by_category || countBy(opportunities, ['category', 'type', 'opportunity_type'])

  return (
    <SectionFrame title="Opportunities" kicker="Command center" endpoints={endpoints} refresh={refresh}>
      <div className="hunter-metric-grid">
        <MetricCard label="Created" value={formatNumber(created)} detail="Live opportunity total" />
        <MetricCard label="Building" value={formatNumber(building)} detail="Packet statuses: building/draft/ready/queued" />
        <MetricCard label="Executed" value={formatNumber(executed)} detail="Execution or packet completions" />
        <MetricCard label="Killed" value={formatNumber(killed)} detail="Failed, rejected, retired, or killed" />
      </div>
      <div className="hunter-card-grid">
        <DataCard title="By Agent">
          <BreakdownList data={byAgent} emptyText="No agent attribution field returned by the current opportunities endpoint." />
        </DataCard>
        <DataCard title="By Channel">
          <BreakdownList data={byChannel} emptyText="No channel/origin breakdown returned by the backend." />
        </DataCard>
        <DataCard title="By Type">
          <BreakdownList data={byType} emptyText="No category/type breakdown returned by the backend." />
        </DataCard>
        <DataCard title="Top Live Opportunities">
          <OpportunityRows rows={opportunities.slice(0, 6)} />
        </DataCard>
      </div>
    </SectionFrame>
  )
}

function OpportunityRows({ rows }) {
  if (!rows.length) return <EmptyState>No live opportunities returned.</EmptyState>
  return (
    <div className="hunter-table">
      <div className="hunter-table-row hunter-table-head">
        <span>Name</span>
        <span>Status</span>
        <span>Confidence</span>
      </div>
      {rows.map((row, index) => (
        <div className="hunter-table-row" key={row.id || row.symbol || row.name || index}>
          <span>{valueFrom(row.title, row.name, row.symbol, row.opportunity_name, `Opportunity ${index + 1}`)}</span>
          <span>{formatText(valueFrom(row.status, row.stage, row.state))}</span>
          <span>{formatPercent(valueFrom(row.confidence, row.weighted_confidence, row.score))}</span>
        </div>
      ))}
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
    <SectionFrame title="Trading" kicker="Positions and capital" endpoints={endpoints} refresh={refresh}>
      {brokerUnavailable && (
        <div className="ops-error">
          Broker-backed trading data is degraded. Non-broker sections continue to render.
        </div>
      )}
      <div className="hunter-metric-grid">
        <MetricCard label="Current Positions" value={formatNumber(valueFrom(capitalState?.broker?.open_positions_count, currentPositions.length))} />
        <MetricCard label="Available Capital" value={formatCurrency(valueFrom(capitalState.available_capital, budget.available_capital))} />
        <MetricCard label="Committed Capital" value={formatCurrency(valueFrom(capitalState.committed_capital, budget.committed_capital))} />
        <MetricCard label="Fast Recycle Win Rate" value={formatPercent(fastRecycle.recycle_win_rate)} />
      </div>
      <div className="hunter-card-grid hunter-card-grid--wide">
        <DataCard title="Current Positions">
          <PositionRows rows={currentPositions.slice(0, 8)} />
        </DataCard>
        <DataCard title="Recent Closes">
          <CloseRows rows={recentCloses} />
        </DataCard>
        <DataCard title="Capital Deployment">
          <KeyValueList
            rows={[
              { label: 'Starting Bankroll', value: formatCurrency(valueFrom(capitalState.starting_bankroll, budget.starting_bankroll)) },
              { label: 'Current Bankroll', value: formatCurrency(valueFrom(capitalState.current_bankroll, budget.current_bankroll)) },
              { label: 'Buying Power', value: formatCurrency(capitalState?.broker?.effective_buying_power || capitalState?.broker?.buying_power) },
              { label: 'Broker Mode', value: formatText(capitalState.broker_mode || budget.broker_mode) },
            ]}
          />
        </DataCard>
        <DataCard title="Fast Recycle">
          <KeyValueList
            rows={[
              { label: 'Enabled', value: fastRecycle.enabled === undefined ? null : String(Boolean(fastRecycle.enabled)) },
              { label: 'Deployed', value: formatCurrency(fastRecycle.deployed_capital) },
              { label: 'Available', value: formatCurrency(fastRecycle.available_capital) },
              { label: 'Average Hold', value: fastRecycle.average_hold_minutes ? `${Math.round(fastRecycle.average_hold_minutes)} min` : null },
            ]}
          />
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
        <span>Symbol</span>
        <span>Side</span>
        <span>Qty</span>
        <span>P/L</span>
      </div>
      {rows.map((row, index) => (
        <div className="hunter-table-row" key={row.id || row.symbol || index}>
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
        <span>Symbol</span>
        <span>Status</span>
        <span>Return</span>
      </div>
      {rows.map((row, index) => (
        <div className="hunter-table-row" key={row.id || row.symbol || index}>
          <span>{formatText(valueFrom(row.symbol, row.ticker, row.name))}</span>
          <span>{formatText(valueFrom(row.status, row.outcome, row.state))}</span>
          <span>{formatCurrency(valueFrom(row.realized_profit, row.pnl, row.actual_return, row.net_result))}</span>
        </div>
      ))}
    </div>
  )
}

function ResultsSection({ onAuthFail }) {
  const { endpoints, refresh } = useSectionData(RESULTS_LOADERS, onAuthFail)
  const daily = endpointData(endpoints.daily, {})
  const weekly = endpointData(endpoints.weekly, {})
  const performance = endpointData(endpoints.performance, {})
  const transactions = asArray(endpointData(endpoints.transactions, {}))
  const todayTransactions = todayRows(transactions)
  const made = sumBy(todayTransactions.filter((row) => Number(valueFrom(row.net_result, row.actual_return, row.amount)) > 0), ['net_result', 'actual_return', 'amount'])
  const spent = sumBy(todayTransactions, ['amount_committed', 'amount_spent', 'cost_basis', 'debit'])
  const net = sumBy(todayTransactions, ['net_result', 'actual_return', 'amount'])
  const taskCounts = normalizeStatusCounts(endpointData(endpoints.tasks, {}))
  const execution = endpointData(endpoints.execution, {})
  const intake = endpointData(endpoints.intake, {})

  return (
    <SectionFrame title="Results" kicker="Made, spent, net" endpoints={endpoints} refresh={refresh}>
      <div className="hunter-metric-grid">
        <MetricCard label="Daily Made" value={formatCurrency(made)} detail={`${todayTransactions.length} transaction rows today`} />
        <MetricCard label="Daily Spent" value={formatCurrency(spent)} detail="From committed/spent transaction fields" />
        <MetricCard label="Daily Net" value={formatCurrency(net)} detail="From realized transaction fields" />
        <MetricCard label="Weekly Net" value={formatCurrency(valueFrom(weekly?.capital?.net_gain_loss, weekly?.net_gain_loss))} />
      </div>
      <div className="hunter-card-grid">
        <DataCard title="Tasks">
          <KeyValueList
            rows={[
              { label: 'Pending', value: formatNumber(valueFrom(taskCounts.pending, taskCounts.queued, taskCounts.open)) },
              { label: 'Completed', value: formatNumber(valueFrom(taskCounts.completed, taskCounts.done, taskCounts.success)) },
              { label: 'Failed', value: formatNumber(valueFrom(taskCounts.failed, taskCounts.error)) },
            ]}
          />
        </DataCard>
        <DataCard title="Trading Results">
          <KeyValueList
            rows={[
              { label: 'Completed Executions', value: formatNumber(valueFrom(execution.completed_executions?.length, daily?.executions?.completed)) },
              { label: 'Failed Executions', value: formatNumber(valueFrom(execution.failed_executions?.length, daily?.executions?.failed)) },
              { label: 'Realized P/L', value: formatCurrency(valueFrom(performance.realized_profit, performance.total_actual_return, daily?.capital?.realized_profit)) },
            ]}
          />
        </DataCard>
        <DataCard title="Opportunity Results">
          <KeyValueList
            rows={[
              { label: 'Total Opportunities', value: formatNumber(valueFrom(intake.total_from_autotrader, daily?.opportunities?.total)) },
              { label: 'Active', value: formatNumber(daily?.opportunities?.active) },
              { label: 'Estimated Monthly Profit', value: formatCurrency(intake.total_estimated_monthly_profit) },
            ]}
          />
        </DataCard>
        <DataCard title="Report Status">
          <KeyValueList
            rows={[
              { label: 'Daily Report Date', value: daily.report_date },
              { label: 'Weekly Generated', value: weekly.generated_at },
              { label: 'Execution Mode', value: formatText(daily.execution_mode) },
            ]}
          />
        </DataCard>
      </div>
    </SectionFrame>
  )
}

function OperationsSection({ onAuthFail }) {
  const { endpoints, refresh } = useSectionData(OPERATIONS_LOADERS, onAuthFail)
  const health = endpointData(endpoints.health, {})
  const readiness = endpointData(endpoints.readiness, {})
  const summary = endpointData(endpoints.summary, {})
  const pipeline = endpointData(endpoints.pipeline, {})
  const autotrader = endpointData(endpoints.autotrader, {})
  const capitalState = endpointData(endpoints.capitalState, {})
  const fastRecycle = capitalState?.fast_recycle || {}
  const events = asArray(endpointData(endpoints.events, {}))
  const errors = asArray(endpointData(endpoints.diagErrors, {}))

  return (
    <SectionFrame title="Operations" kicker="Health and diagnostics" endpoints={endpoints} refresh={refresh}>
      <div className="hunter-metric-grid">
        <MetricCard label="System Health" value={formatText(valueFrom(health.status, readiness.status))} detail={health.service || health.version} />
        <MetricCard label="Execution Status" value={formatText(valueFrom(endpointData(endpoints.diagExecution, {})?.status, summary.execution_status))} />
        <MetricCard label="Intake Health" value={formatText(valueFrom(autotrader.live_data_status, autotrader.last_scan_status))} detail={autotrader.current_data_mode} />
        <MetricCard label="Recycle Health" value={fastRecycle.enabled === undefined ? 'Unavailable' : (fastRecycle.enabled ? 'Enabled' : 'Disabled')} />
      </div>
      <div className="hunter-card-grid hunter-card-grid--wide">
        <DataCard title="System Health">
          <KeyValueList
            rows={[
              { label: 'Service', value: health.service },
              { label: 'Version', value: health.version },
              { label: 'Readiness', value: formatText(readiness.status) },
              { label: 'Mode', value: formatText(health.mode || readiness.mode) },
            ]}
          />
        </DataCard>
        <DataCard title="Intake Health">
          <KeyValueList
            rows={[
              { label: 'Source Reachable', value: autotrader.source_reachable === undefined ? null : String(Boolean(autotrader.source_reachable)) },
              { label: 'Current Data Mode', value: formatText(autotrader.current_data_mode) },
              { label: 'Live Data Status', value: formatText(autotrader.live_data_status) },
              { label: 'Record Count', value: formatNumber(autotrader.live_data_record_count) },
            ]}
          />
        </DataCard>
        <DataCard title="Pipeline">
          <BreakdownList data={pipeline.by_status} emptyText="No pipeline status breakdown returned." />
        </DataCard>
        <DataCard title="Diagnostics">
          <KeyValueList
            rows={[
              { label: 'Health Summary', value: formatText(endpointData(endpoints.diagHealth, {})?.status) },
              { label: 'Capital Status', value: formatText(endpointData(endpoints.diagCapital, {})?.status) },
              { label: 'Recent Errors', value: formatNumber(errors.length) },
              { label: 'Recent Events', value: formatNumber(events.length) },
            ]}
          />
        </DataCard>
        <DataCard title="Execution Status">
          <KeyValueList
            rows={[
              { label: 'Ready Packets', value: formatNumber(summary.ready_packets) },
              { label: 'Unacknowledged Alerts', value: formatNumber(summary.unacknowledged_alerts) },
              { label: 'Underperforming Strategies', value: formatNumber(summary.underperforming_strategies) },
            ]}
          />
        </DataCard>
        <DataCard title="Recycle Status">
          <KeyValueList
            rows={[
              { label: 'Enabled', value: fastRecycle.enabled === undefined ? null : String(Boolean(fastRecycle.enabled)) },
              { label: 'Available Capital', value: formatCurrency(fastRecycle.available_capital) },
              { label: 'Deployed Capital', value: formatCurrency(fastRecycle.deployed_capital) },
              { label: 'Stale Positions', value: formatNumber(fastRecycle.stale_positions_count) },
            ]}
          />
        </DataCard>
      </div>
    </SectionFrame>
  )
}
