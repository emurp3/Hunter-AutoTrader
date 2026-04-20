import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useAuth } from '../context/AuthContext'

const API = '/api'

const fallbackData = {
  summary: {
    weekly_quotas: {
      all_met: false,
      source_discovery: { sources_found_this_week: 0, required: 10, shortfall: 10, quota_met: false },
      strategy_deployment: { active_count: 0, required: 10, shortfall: 10, quota_met: false },
    },
    total_opportunities: 0,
    active_opportunities: 0,
    elite_opportunities: 0,
    high_opportunities: 0,
    unacknowledged_alerts: 0,
    ready_packets: 0,
    underperforming_strategies: 0,
  },
  alerts: [],
  strategies: {
    active_count: 0,
    activated_this_week: 0,
    retired_this_week: 0,
    replacement_strategies_required: 0,
    total_expected_return: 0,
    total_actual_return: 0,
    candidates_available: 0,
    strategies: [],
  },
  budget: {
    starting_bankroll: 0,
    current_bankroll: 0,
    available_capital: 0,
    committed_capital: 0,
    realized_profit: 0,
    unrealized_exposure: 0,
    allocation_count: 0,
    evaluation_start_date: null,
    evaluation_end_date: null,
    capital_match_eligible: false,
    capital_match_amount: 0,
    month_end_review: {
      starting_bankroll: 0,
      ending_bankroll: 0,
      net_gain_loss: 0,
      growth_pct: 0,
      doubled_bankroll: false,
      capital_match_eligible: false,
      recommended_match_amount: 0,
      next_cycle_bankroll_if_matched: 0,
      evaluation_start_date: null,
      evaluation_end_date: null,
      days_remaining: null,
    },
    budget: {
      starting_bankroll: 0,
      current_bankroll: 0,
      evaluation_start_date: null,
      evaluation_end_date: null,
      status: 'no_open_budget',
    },
    allocations_by_source: [],
  },
  allocations: [],
  atStatus: {
    source_configured: false,
    source_reachable: false,
    intake_running: false,
    last_scan_status: 'never_run',
    last_scan_at: null,
    last_error: 'Operational data unavailable.',
    live_data_status: 'missing',
    live_data_message: 'Operational data unavailable until the backend reconnects.',
    live_data_updated_at: null,
    live_data_record_count: 0,
    stale_after_hours: 24,
    using_fallback: false,
    fallback_reason: null,
    fallback_record_count: 0,
    current_data_mode: 'offline',
    last_scan_counts: {
      scanned: 0,
      inserted: 0,
      updated: 0,
      skipped: 0,
      errors: 0,
    },
    config: {
      source_type: 'unknown',
      file_path: null,
      seed_path: null,
    },
  },
  intakeSummary: {
    total_from_autotrader: 0,
    total_estimated_monthly_profit: 0,
    average_confidence: null,
    by_status: {},
    by_category: {},
    by_origin: {},
    current_data_mode: 'offline',
    using_fallback: false,
    live_data_status: 'missing',
    top_5_by_score: [],
  },
  packets: [],
  pipeline: {
    by_status: {},
    by_band: {},
    top_10: [],
  },
  events: {
    count: 0,
    events: [],
  },
  executionStatus: {
    active_executions: [],
    completed_executions: [],
    failed_executions: [],
    recent_outcomes: [],
    counts: {
      active: 0,
      completed: 0,
      failed: 0,
    },
  },
  performanceSummary: {
    outcomes_recorded: 0,
    completed_executions: 0,
    failed_executions: 0,
    success_rate: null,
    total_actual_return: 0,
    best_lane: null,
    weakest_lane: null,
    average_return_per_opportunity_type: [],
  },
  diagnostics: null,
}

function buildScanSignature(status) {
  return [
    status?.last_scan_at ?? 'none',
    status?.last_scan_status ?? 'never_run',
    status?.live_data_status ?? 'missing',
    status?.current_data_mode ?? 'offline',
  ].join('|')
}

function sleep(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms))
}

function StatCard({ label, value, sub, highlight }) {
  return (
    <div className={`stat-card${highlight ? ' stat-card--highlight' : ''}`}>
      <div className="stat-value">{value ?? '—'}</div>
      <div className="stat-label">{label}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  )
}

function QuotaBadge({ met }) {
  return (
    <span className={`quota-badge${met ? ' quota-badge--met' : ' quota-badge--short'}`}>
      {met ? 'QUOTA MET' : 'SHORTFALL'}
    </span>
  )
}

function StatusDot({ ok, label }) {
  return (
    <span className={`status-dot${ok ? ' status-dot--ok' : ' status-dot--fail'}`}>
      {ok ? '●' : '○'} {label}
    </span>
  )
}

function OpportunityModal({ opportunity, onClose }) {
  useEffect(() => {
    function onKey(e) { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  if (!opportunity) return null
  const d = opportunity.decision
  return (
    <div className="opp-modal-overlay" onClick={onClose}>
      <div className="opp-modal" onClick={e => e.stopPropagation()}>
        <div className="opp-modal-header">
          <span className={`opp-band opp-band--${opportunity.priority_band ?? 'low'}`}>
            {opportunity.priority_band?.toUpperCase() ?? 'LOW'}
          </span>
          <span className="opp-modal-score">Score {opportunity.score?.toFixed(1) ?? '—'}</span>
          <button className="opp-modal-close" onClick={onClose}>✕</button>
        </div>
        <div className="opp-modal-body">
          <div className="opp-modal-desc">{opportunity.description}</div>
          <div className="opp-modal-grid">
            <div className="opp-modal-field">
              <span className="opp-modal-label">Source ID</span>
              <span className="opp-modal-value opp-modal-mono">{opportunity.source_id}</span>
            </div>
            <div className="opp-modal-field">
              <span className="opp-modal-label">Category</span>
              <span className="opp-modal-value">{opportunity.category ?? '—'}</span>
            </div>
            <div className="opp-modal-field">
              <span className="opp-modal-label">Status</span>
              <span className="opp-modal-value">{opportunity.status ?? '—'}</span>
            </div>
            <div className="opp-modal-field">
              <span className="opp-modal-label">Origin</span>
              <span className="opp-modal-value">{opportunity.origin_module?.replace(/_/g, ' ') ?? '—'}</span>
            </div>
            <div className="opp-modal-field">
              <span className="opp-modal-label">Est. Monthly Profit</span>
              <span className="opp-modal-value opp-modal-profit">
                {opportunity.estimated_profit != null ? `$${Number(opportunity.estimated_profit).toLocaleString()}` : '—'}
              </span>
            </div>
            <div className="opp-modal-field">
              <span className="opp-modal-label">Confidence</span>
              <span className="opp-modal-value">
                {opportunity.confidence != null ? `${(opportunity.confidence * 100).toFixed(0)}%` : '—'}
              </span>
            </div>
            {opportunity.date_found && (
              <div className="opp-modal-field">
                <span className="opp-modal-label">Found</span>
                <span className="opp-modal-value">{new Date(opportunity.date_found).toLocaleDateString()}</span>
              </div>
            )}
            {opportunity.next_action && (
              <div className="opp-modal-field opp-modal-field--full">
                <span className="opp-modal-label">Next Action</span>
                <span className="opp-modal-value">{opportunity.next_action}</span>
              </div>
            )}
          </div>
          {opportunity.marketplace_lane && (
            <div className="opp-modal-mkt-section">
              <div className="opp-modal-mkt-title">Facebook Marketplace Lane</div>
              <div className="opp-modal-mkt-row">
                <span className="mkt-lane-badge">FB Marketplace</span>
                {opportunity.marketplace_provider && (
                  <span className="opp-modal-value" style={{ fontSize: '0.7rem' }}>
                    {opportunity.marketplace_provider.replace(/_/g, ' ')}
                  </span>
                )}
                {opportunity.marketplace_routing_label && (
                  <span className={`mkt-routing-badge mkt-routing-badge--${opportunity.marketplace_routing_label}`}>
                    {opportunity.marketplace_routing_label.replace(/_/g, ' ')}
                  </span>
                )}
                {opportunity.marketplace_execution_state && (
                  <span className={`mkt-exec-state mkt-exec-state--${opportunity.marketplace_execution_state}`}>
                    {opportunity.marketplace_execution_state.replace(/_/g, ' ')}
                  </span>
                )}
              </div>
              {opportunity.marketplace_blocked_reason && (
                <div className="opp-modal-mkt-blocked">
                  Blocked: {opportunity.marketplace_blocked_reason}
                </div>
              )}
            </div>
          )}
          {d && (
            <div className="opp-modal-decision">
              <div className="opp-modal-decision-title">Decision</div>
              <div className="opp-modal-grid">
                <div className="opp-modal-field">
                  <span className="opp-modal-label">Action State</span>
                  <span className={`opp-modal-state opp-modal-state--${d.action_state}`}>{d.action_state?.replace(/_/g, ' ').toUpperCase()}</span>
                </div>
                <div className="opp-modal-field">
                  <span className="opp-modal-label">Execution Path</span>
                  <span className="opp-modal-value">{d.execution_path?.replace(/_/g, ' ') ?? '—'}</span>
                </div>
                <div className="opp-modal-field">
                  <span className="opp-modal-label">Capital Rec.</span>
                  <span className="opp-modal-value">
                    {d.capital_recommendation != null ? `$${Number(d.capital_recommendation).toLocaleString()}` : '—'}
                  </span>
                </div>
                <div className="opp-modal-field">
                  <span className="opp-modal-label">Execution Ready</span>
                  <span className={`opp-modal-value ${d.execution_ready ? 'opp-modal-yes' : 'opp-modal-no'}`}>
                    {d.execution_ready ? 'Yes' : 'No'}
                  </span>
                </div>
                {d.blocked_by && (
                  <div className="opp-modal-field">
                    <span className="opp-modal-label">Blocked By</span>
                    <span className="opp-modal-value opp-modal-blocked">{d.blocked_by}</span>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function formatRelativeDate(value) {
  if (!value) {
    return 'No recent event'
  }

  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }

  return date.toLocaleString()
}

function formatCurrency(value) {
  if (value == null || Number.isNaN(Number(value))) {
    return '—'
  }

  return `$${Number(value).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`
}

function formatCount(value) {
  if (value == null || Number.isNaN(Number(value))) {
    return '—'
  }

  return Number(value).toLocaleString()
}

function formatDurationMinutes(value) {
  if (value == null || Number.isNaN(Number(value))) {
    return '—'
  }

  const minutes = Math.max(0, Number(value))
  if (minutes < 60) {
    return `${minutes.toFixed(minutes < 10 ? 1 : 0)}m`
  }

  const hours = Math.floor(minutes / 60)
  const remainingMinutes = Math.round(minutes % 60)
  return remainingMinutes > 0 ? `${hours}h ${remainingMinutes}m` : `${hours}h`
}

function extractTaskId(alert) {
  const body = alert?.body ?? ''
  const match = body.match(/task_id=([a-f0-9-]+)/i)
  return match?.[1] ?? null
}

function alertActionHints(alert) {
  const taskId = extractTaskId(alert)
  return {
    taskId,
    canAcknowledge: Boolean(alert?.id),
    canRetry: Boolean(taskId && alert?.alert_type === 'review_required'),
  }
}

function exportCsv(rows) {
  const cols = ['timestamp', 'source_id', 'allocation_name', 'category', 'amount_committed', 'expected_return', 'actual_return', 'net_result', 'status', 'budget_cycle']
  const header = cols.join(',')
  const body = rows.map(r =>
    cols.map(c => {
      const v = r[c] ?? ''
      return `"${String(v).replace(/"/g, '""')}"`
    }).join(',')
  ).join('\n')
  const blob = new Blob([header + '\n' + body], { type: 'text/csv' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `hunter-transactions-${new Date().toISOString().slice(0,10)}.csv`
  a.click()
  URL.revokeObjectURL(url)
}

class AuthError extends Error {
  constructor() { super('Unauthorized'); this.isAuthError = true }
}

async function requestJson(path, options = {}) {
  const response = await fetch(`${API}${path}`, {
    credentials: 'include',
    headers: {
      Accept: 'application/json',
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
      ...options.headers,
    },
    ...options,
  })

  if (response.status === 401) throw new AuthError()

  let data = null
  try {
    data = await response.json()
  } catch {
    data = null
  }

  if (!response.ok) {
    throw new Error(data?.detail || `Request failed with status ${response.status}`)
  }

  return data
}

async function loadOperationalData() {
  const [
    summary,
    alerts,
    strategies,
    atStatus,
    packets,
    pipeline,
    budgetResult,
    capitalStateResult,
    budgetReviewResult,
    allocationsResult,
    intakeResult,
    eventsResult,
    executionStatusResult,
    performanceSummaryResult,
    readinessResult,
    dailyReportResult,
    liveOpportunitiesResult,
    transactionsResult,
    diagHealthResult,
    diagCapitalResult,
    diagExecutionResult,
    diagErrorsResult,
    diagTaskTypesResult,
  ] = await Promise.allSettled([
    requestJson('/operations/summary'),
    requestJson('/alerts/?active_only=true'),
    requestJson('/strategies/weekly'),
    requestJson('/autotrader/status'),
    requestJson('/packets/'),
    requestJson('/operations/pipeline'),
    requestJson('/budget/current'),
    requestJson('/budget/capital-state'),
    requestJson('/budget/review'),
    requestJson('/budget/allocations'),
    requestJson('/autotrader/intake-summary'),
    requestJson('/operations/events?limit=8'),
    requestJson('/execution/status'),
    requestJson('/performance/summary'),
    requestJson('/system/readiness'),
    requestJson('/reports/daily'),
    requestJson('/autotrader/opportunities?limit=20'),
    requestJson('/budget/transactions?limit=200'),
    requestJson('/diag/health-summary'),
    requestJson('/diag/capital-status'),
    requestJson('/diag/execution-status'),
    requestJson('/diag/recent-errors?limit=8'),
    requestJson('/diag/task-type-summary'),
  ])

  if (
    summary.status !== 'fulfilled' ||
    alerts.status !== 'fulfilled' ||
    strategies.status !== 'fulfilled' ||
    atStatus.status !== 'fulfilled' ||
    packets.status !== 'fulfilled' ||
    pipeline.status !== 'fulfilled'
  ) {
    throw new Error('Core operational endpoints are unavailable.')
  }

  return {
    summary: summary.value,
    alerts: Array.isArray(alerts.value) ? alerts.value : [],
    strategies: strategies.value,
    atStatus: atStatus.value,
    packets: Array.isArray(packets.value) ? packets.value : [],
    pipeline: pipeline.value,
    budget: budgetResult.status === 'fulfilled' ? budgetResult.value : null,
    capitalState: capitalStateResult.status === 'fulfilled' ? capitalStateResult.value : null,
    budgetReview: budgetReviewResult.status === 'fulfilled' ? budgetReviewResult.value : null,
    allocations: allocationsResult.status === 'fulfilled' && Array.isArray(allocationsResult.value)
      ? allocationsResult.value
      : [],
    intakeSummary: intakeResult.status === 'fulfilled' ? intakeResult.value : null,
    events: eventsResult.status === 'fulfilled' ? eventsResult.value : null,
    executionStatus: executionStatusResult.status === 'fulfilled' ? executionStatusResult.value : null,
    performanceSummary:
      performanceSummaryResult.status === 'fulfilled' ? performanceSummaryResult.value : null,
    readiness: readinessResult.status === 'fulfilled' ? readinessResult.value : null,
    dailyReport: dailyReportResult.status === 'fulfilled' ? dailyReportResult.value : null,
    liveOpportunities: liveOpportunitiesResult.status === 'fulfilled' ? liveOpportunitiesResult.value : null,
    transactions: transactionsResult.status === 'fulfilled' ? transactionsResult.value : null,
    diagnostics: {
      health: diagHealthResult.status === 'fulfilled' ? diagHealthResult.value : null,
      capital: diagCapitalResult.status === 'fulfilled' ? diagCapitalResult.value : null,
      execution: diagExecutionResult.status === 'fulfilled' ? diagExecutionResult.value : null,
      recentErrors: diagErrorsResult.status === 'fulfilled' ? diagErrorsResult.value : null,
      taskTypes: diagTaskTypesResult.status === 'fulfilled' ? diagTaskTypesResult.value : null,
    },
  }
}

export default function OperationsPage({ onBack, onAuthFail }) {
  const { logout } = useAuth()
  const [summary, setSummary] = useState(fallbackData.summary)
  const [alerts, setAlerts] = useState(fallbackData.alerts)
  const [strategies, setStrategies] = useState(fallbackData.strategies)
  const [budget, setBudget] = useState(fallbackData.budget)
  const [capitalState, setCapitalState] = useState(null)
  const [budgetReview, setBudgetReview] = useState(fallbackData.budget.month_end_review)
  const [allocations, setAllocations] = useState(fallbackData.allocations)
  const [atStatus, setAtStatus] = useState(fallbackData.atStatus)
  const [intakeSummary, setIntakeSummary] = useState(fallbackData.intakeSummary)
  const [packets, setPackets] = useState(fallbackData.packets)
  const [pipeline, setPipeline] = useState(fallbackData.pipeline)
  const [events, setEvents] = useState(fallbackData.events)
  const [executionStatus, setExecutionStatus] = useState(fallbackData.executionStatus)
  const [performanceSummary, setPerformanceSummary] = useState(fallbackData.performanceSummary)
  const [readiness, setReadiness] = useState(null)
  const [dailyReport, setDailyReport] = useState(null)
  const [liveOpportunities, setLiveOpportunities] = useState(null)
  const [transactions, setTransactions] = useState(null)
  const [diagnostics, setDiagnostics] = useState(fallbackData.diagnostics)
  const [selectedOpportunity, setSelectedOpportunity] = useState(null)
  const [txSortKey, setTxSortKey] = useState('timestamp')
  const [txSortDir, setTxSortDir] = useState('desc')
  const [txPage, setTxPage] = useState(0)
  const TX_PAGE_SIZE = 15
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const [usingFallback, setUsingFallback] = useState(false)
  const [commandState, setCommandState] = useState({ type: null, status: 'idle', message: '' })
  const [outcomeForm, setOutcomeForm] = useState({
    packetId: '',
    mode: 'complete',
    actualReturn: '',
    successReason: '',
    failureReason: '',
    notes: '',
  })

  function applyOperationalData(data) {
    setSummary(data.summary)
    setAlerts(data.alerts)
    setStrategies(data.strategies)
    setBudget(data.budget)
    setCapitalState(data.capitalState ?? null)
    setBudgetReview(data.budgetReview ?? data.budget?.month_end_review ?? null)
    setAllocations(data.allocations)
    setAtStatus(data.atStatus)
    setIntakeSummary(data.intakeSummary)
    setPackets(data.packets)
    setPipeline(data.pipeline)
    setEvents(data.events)
    setExecutionStatus(data.executionStatus ?? fallbackData.executionStatus)
    setPerformanceSummary(data.performanceSummary ?? fallbackData.performanceSummary)
    setReadiness(data.readiness ?? null)
    setDailyReport(data.dailyReport ?? null)
    setLiveOpportunities(data.liveOpportunities ?? null)
    setTransactions(data.transactions ?? null)
    setDiagnostics(data.diagnostics ?? fallbackData.diagnostics)
  }

  async function waitForIntakeCompletion(previousSignature) {
    let sawRunning = false
    let latestStatus = null

    for (let attempt = 0; attempt < 20; attempt += 1) {
      const status = await requestJson('/autotrader/status')
      latestStatus = status
      const nextSignature = buildScanSignature(status)

      if (status.intake_running) {
        sawRunning = true
      }

      if (!status.intake_running && nextSignature !== previousSignature && status.last_scan_status !== 'never_run') {
        return status
      }

      if (sawRunning && !status.intake_running) {
        return status
      }

      await sleep(1500)
    }

    return latestStatus
  }

  function handleLogout() {
    logout()
    if (onBack) {
      onBack()
      return
    }
    if (onAuthFail) {
      onAuthFail()
    }
  }

  useEffect(() => {
    let active = true

    async function load() {
      try {
        const data = await loadOperationalData()
        if (!active) {
          return
        }

        applyOperationalData(data)
        setError(null)
        setUsingFallback(false)
      } catch (err) {
        if (!active) {
          return
        }

        if (err?.isAuthError) {
          if (onAuthFail) onAuthFail()
          return
        }

        applyOperationalData({
          summary: fallbackData.summary,
          alerts: fallbackData.alerts,
          strategies: fallbackData.strategies,
          budget: fallbackData.budget,
          budgetReview: fallbackData.budget.month_end_review,
          allocations: fallbackData.allocations,
          atStatus: fallbackData.atStatus,
          intakeSummary: fallbackData.intakeSummary,
          packets: fallbackData.packets,
          pipeline: fallbackData.pipeline,
          events: fallbackData.events,
          executionStatus: fallbackData.executionStatus,
          performanceSummary: fallbackData.performanceSummary,
        })
        setUsingFallback(true)
        setError('Hunter backend is unavailable. No live operational data is being shown.')
      } finally {
        if (active) {
          setLoading(false)
        }
      }
    }

    load()
    const intervalId = window.setInterval(load, 30000)

    return () => {
      active = false
      window.clearInterval(intervalId)
    }
  }, [])

  const quotas = summary?.weekly_quotas
  const discoveryQuota = quotas?.source_discovery
  const strategyQuota = quotas?.strategy_deployment

  const pressureSignals = useMemo(
    () =>
      [
        summary?.unacknowledged_alerts > 0 &&
          `${summary.unacknowledged_alerts} unacked alert${summary.unacknowledged_alerts > 1 ? 's' : ''}`,
        summary?.underperforming_strategies > 0 &&
          `${summary.underperforming_strategies} underperforming strateg${
            summary.underperforming_strategies > 1 ? 'ies' : 'y'
          }`,
        discoveryQuota &&
          !discoveryQuota.quota_met &&
          `source discovery shortfall (${discoveryQuota.shortfall} needed)`,
        strategyQuota &&
          !strategyQuota.quota_met &&
          `strategy quota shortfall (${strategyQuota.shortfall} needed)`,
        strategies?.replacement_strategies_required > 0 &&
          `${strategies.replacement_strategies_required} replacement${
            strategies.replacement_strategies_required > 1 ? 's' : ''
          } required`,
      ].filter(Boolean),
    [discoveryQuota, strategies, strategyQuota, summary],
  )

  const pressureLevel =
    pressureSignals.length === 0
      ? 'clear'
      : pressureSignals.length <= 1
        ? 'low'
        : pressureSignals.length <= 3
          ? 'elevated'
          : 'critical'

  const readyPackets = packets.filter((packet) => packet.status === 'ready')

  const txSortedRows = useMemo(() => {
    const rows = transactions?.transactions ?? []
    return [...rows].sort((a, b) => {
      const av = a[txSortKey] ?? ''
      const bv = b[txSortKey] ?? ''
      if (av < bv) return txSortDir === 'asc' ? -1 : 1
      if (av > bv) return txSortDir === 'asc' ? 1 : -1
      return 0
    })
  }, [transactions, txSortKey, txSortDir])

  const txPageRows = txSortedRows.slice(txPage * TX_PAGE_SIZE, (txPage + 1) * TX_PAGE_SIZE)
  const txPageCount = Math.ceil(txSortedRows.length / TX_PAGE_SIZE)

  function txSort(key) {
    if (txSortKey === key) setTxSortDir(d => d === 'asc' ? 'desc' : 'asc')
    else { setTxSortKey(key); setTxSortDir('desc') }
    setTxPage(0)
  }

  function txSortArrow(key) {
    if (txSortKey !== key) return ' ↕'
    return txSortDir === 'asc' ? ' ↑' : ' ↓'
  }
  const fundedPackets = packets.filter(
    (packet) => packet.status === 'acknowledged' || packet.status === 'executed',
  )
  const activeExecutions = executionStatus?.active_executions ?? []
  const completedExecutions = executionStatus?.completed_executions ?? []
  const failedExecutions = executionStatus?.failed_executions ?? []
  const recentTimingOutcomes = executionStatus?.recent_closed_positions?.length
    ? executionStatus.recent_closed_positions
    : (executionStatus?.recent_outcomes ?? [])
      .map((outcome) => ({
        symbol: outcome?.timing?.symbol ?? outcome?.source_id ?? '—',
        ...outcome?.timing,
        recorded_at: outcome?.recorded_at,
        source_id: outcome?.source_id,
      }))
      .filter((item) => item.hold_duration_minutes != null || item.time_to_realized_profit_minutes != null)
  const top10 = pipeline?.top_10 ?? []
  const recentEvents = events?.events ?? []
  const endpointStatus = usingFallback ? 'API Offline' : 'API Online'
  const liveDataStatus = atStatus?.live_data_status ?? 'missing'
  const fallbackActive = Boolean(atStatus?.using_fallback || atStatus?.current_data_mode === 'seed')
  const liveFeedReady = liveDataStatus === 'ready'
  const autotraderOffline = !liveFeedReady
  const intakeCount = intakeSummary?.total_from_autotrader ?? 0
  const autoTraderHeadline = autotraderOffline ? 'AutoTrader offline / no live data' : 'AutoTrader live'
  const autoTraderModeLabel = fallbackActive
    ? 'Seed fallback active'
    : liveFeedReady
      ? 'Live bridge active'
      : 'No usable intake source'

  const missionSummary = useMemo(() => {
    if (usingFallback) {
      return 'Backend unavailable. Hunter is showing an empty offline state until the API reconnects.'
    }

    if (fallbackActive) {
      return 'AutoTrader is offline, but Hunter is using seeded opportunities so intake and strategy logic can run today.'
    }

    if (autotraderOffline) {
      return 'Live AutoTrader data is unavailable. Run intake to pull from the emergency seed source until the bridge is repaired.'
    }

    if (atStatus?.last_scan_status === 'success') {
      return 'Hunter is connected and ready for a live intake run or quota enforcement pass.'
    }

      return 'Hunter is connected. Run an intake pass or quota check to validate the first task end to end.'
  }, [atStatus, autotraderOffline, fallbackActive, usingFallback])

  const fundedOpportunities = useMemo(() => {
    if (Array.isArray(allocations) && allocations.length > 0) {
      return allocations
    }

    return budget?.allocations_by_source ?? []
  }, [allocations, budget])

  const budgetCycle =
    budget?.budget ??
    ((budget?.evaluation_start_date ||
      budget?.evaluation_end_date ||
      budget?.starting_bankroll != null ||
      budget?.starting_budget != null ||
      budget?.status === 'open' ||
      budget?.status === 'closed')
      ? budget
      : null)

  const budgetStatus = budgetCycle?.status ?? 'no_open_budget'
  const hasPlanningBudget = Boolean(budgetCycle)
  const planningCycleStart = budgetCycle?.evaluation_start_date ?? budget?.evaluation_start_date ?? null
  const planningCycleEnd = budgetCycle?.evaluation_end_date ?? budget?.evaluation_end_date ?? null

  // ── Broker-reconciled capital state (capital-state endpoint wins in live mode) ──
  // capitalState comes from /budget/capital-state which applies broker truth in live
  // mode; fall back to /budget/current values if capital-state is unavailable.
  const cs = capitalState  // shorthand
  const hasCapitalSource = Boolean(cs || budget)

  const startingBankroll =
    cs?.starting_bankroll ??
    budget?.starting_bankroll ?? budget?.budget?.starting_bankroll ?? budget?.budget?.starting_budget ?? 0

  const currentBankroll =
    cs?.current_bankroll ??
    budget?.current_bankroll ?? budget?.budget?.current_bankroll ?? startingBankroll

  const availableCapital =
    cs?.available_capital ??
    budget?.available_capital ?? budget?.available_budget ?? budget?.remaining_budget ?? 0

  const committedCapital =
    cs?.committed_capital ??
    budget?.committed_capital ?? budget?.allocated_budget ?? budget?.total_allocated ?? 0

  const realizedProfit =
    cs?.realized_profit ??
    budget?.realized_profit ?? budget?.realized_return ?? budget?.budget?.realized_return ?? 0

  const unrealizedPl =
    cs?.unrealized_pl ?? budget?.unrealized_pl ?? 0

  const monthEndReview = budgetReview ?? cs?.month_end_review ?? budget?.month_end_review ?? null
  const originalBaseCapital = monthEndReview?.starting_bankroll ?? startingBankroll
  const doublingTarget =
    monthEndReview?.doubling_threshold ??
    (originalBaseCapital > 0 ? originalBaseCapital * 2 : budget?.flip_target ?? currentBankroll ?? 0)
  const doublingProgressPct =
    monthEndReview?.progress_to_doubling_threshold ??
    (doublingTarget > 0 ? Math.max(0, (currentBankroll / doublingTarget) * 100) : 0)
  const evaluationEndDate =
    cs?.evaluation_end_date ?? budget?.evaluation_end_date ?? budget?.budget?.evaluation_end_date ?? null
  const capitalMatchAmount =
    cs?.capital_match_amount ?? budget?.capital_match_amount ?? monthEndReview?.recommended_match_amount ?? 0
  const capitalMatchEligible =
    cs?.capital_match_eligible ?? budget?.capital_match_eligible ?? monthEndReview?.capital_match_eligible ?? false

  // Strategy / broker metadata
  const strategyMode = cs?.strategy_mode ?? budget?.strategy_mode ?? 'RECYCLE'
  const liveExecStrategy = cs?.live_execution_strategy ?? budget?.live_execution_strategy ?? 'INTRADAY_RECYCLE'
  const brokerMode =
    cs?.broker_mode ??
    budget?.broker_mode ??
    readiness?.broker_account_mode ??
    readiness?.modules?.brokerage?.account_mode ??
    'unknown'
  const executionPolicyMode =
    cs?.execution_mode ??
    budget?.execution_mode ??
    readiness?.execution_policy_mode ??
    readiness?.execution_mode ??
    'guardrailed'
  const isLiveMode = brokerMode === 'live'
  const lastBrokerSyncAt = cs?.last_broker_sync_at ?? budget?.last_broker_sync_at ?? null
  const brokerSyncSuccess = cs?.broker_sync_success ?? budget?.broker_sync_success ?? false
  const mismatchDetected = cs?.mismatch_detected ?? budget?.mismatch_detected ?? false
  const mismatchDetails = cs?.mismatch_details ?? budget?.mismatch_details ?? null
  const openPositionsCount = cs?.broker?.open_positions_count ?? cs?.open_positions_count ?? 0
  const effectiveBuyingPower = cs?.broker?.effective_buying_power ?? 0
  const brokerPositions = cs?.broker?.positions ?? []

  // Raw broker account fields for tooltip transparency
  const brokerCash = cs?.broker_cash ?? cs?.broker?.cash ?? 0
  const brokerBuyingPower = cs?.broker_buying_power ?? cs?.broker?.buying_power ?? 0
  const brokerPortfolioValue = cs?.broker_portfolio_value ?? cs?.broker?.portfolio_value ?? 0
  const reservedByOpenOrders = cs?.reserved_by_open_orders ?? cs?.broker?.reserved_by_open_orders ?? 0
  const openBuyOrdersCount = cs?.open_buy_orders_count ?? cs?.broker?.open_buy_orders_count ?? 0
  const openSellOrdersCount = cs?.broker?.open_sell_orders_count ?? 0
  const openOrdersCount = openBuyOrdersCount + openSellOrdersCount
  // CAPITAL_RESERVE_BUFFER matches backend default ($2.00 env: CAPITAL_RESERVE_BUFFER)
  const capitalReserveBuffer = brokerBuyingPower > 0
    ? Math.max(0, brokerBuyingPower - availableCapital)
    : 2.00
  const brokerModeLabel =
    brokerMode === 'live'
      ? 'Live broker'
      : brokerMode === 'paper'
        ? 'Paper broker'
        : 'Broker mode unknown'
  const brokerModeBadge =
    brokerMode === 'live'
      ? 'LIVE BROKER'
      : brokerMode === 'paper'
        ? 'PAPER BROKER'
        : 'BROKER UNKNOWN'
  const executionPolicyLabel =
    executionPolicyMode === 'live'
      ? 'Live execution policy'
      : executionPolicyMode === 'sandbox'
        ? 'Guardrailed execution policy'
        : `${String(executionPolicyMode).replace(/_/g, ' ')} execution policy`
  const executionPolicyBadge =
    executionPolicyMode === 'live'
      ? 'LIVE EXECUTION'
      : 'GUARDRAILED EXECUTION'
  const brokerApiOnline =
    brokerSyncSuccess ||
    readiness?.broker_connection_ready ||
    readiness?.modules?.brokerage?.connected ||
    readiness?.modules?.sandbox_brokerage?.connected ||
    false
  const capitalTruthLabel = brokerSyncSuccess
    ? 'Broker truth'
    : 'Internal ledger fallback'
  const capitalTruthCopy = brokerSyncSuccess
    ? 'These capital numbers are sourced from the broker-reconciled capital endpoint and reflect account truth.'
    : 'Broker sync is unavailable, so Hunter is falling back to internal ledger values. The cards stay visible, but they are labeled as fallback.'
  const capitalUnavailableCopy =
    'No broker or ledger capital payload is available yet. The broker block stays visible so missing capital data is explicit instead of hidden.'
  const capitalTruthTimestamp = lastBrokerSyncAt ? new Date(lastBrokerSyncAt).toLocaleTimeString() : null
  const currentBankrollLabel = brokerSyncSuccess ? 'Current Bankroll / Portfolio Value' : 'Current Bankroll'
  const diagCapital = diagnostics?.capital
  const diagExecution = diagnostics?.execution
  const diagErrors = diagnostics?.recentErrors?.errors ?? []
  const diagTaskTypes = diagnostics?.taskTypes
  const diagnosticStatusLabel = diagCapital?.ui_source === 'broker'
    ? 'Broker truth'
    : diagCapital?.ui_source === 'fallback'
      ? 'Fallback / internal ledger'
      : 'No payload'
  const diagnosticPlanningLabel = diagCapital?.planning_state_source_label ?? 'unknown'
  const diagnosticInconsistent = Boolean(diagCapital?.states_inconsistent)
  const capitalEndpointStatus = diagCapital?.budget_capital_state_endpoint_status ?? 'unknown'
  const currentEndpointStatus = diagCapital?.budget_current_endpoint_status ?? 'unknown'
  const diagLastError =
    diagErrors[0]?.error_message ??
    diagCapital?.error_message ??
    diagExecution?.error_message ??
    'No recent captured capital or execution exception.'

  async function runCommand(type) {
    const commands = {
      intake: { label: 'AutoTrader intake', path: '/autotrader/run-intake' },
      quotas: { label: 'weekly quota enforcement', path: '/operations/run-quotas' },
    }

    const command = commands[type]
    if (!command) {
      return
    }

    setCommandState({
      type,
      status: 'running',
      message: `Running ${command.label}...`,
    })

    try {
      const initialSignature = type === 'intake' ? buildScanSignature(atStatus) : null
      const result = await requestJson(command.path, { method: 'POST' })
      const intakeStatus =
        type === 'intake'
          ? await waitForIntakeCompletion(initialSignature)
          : null

      const refreshed = await loadOperationalData()
      applyOperationalData(refreshed)
      setUsingFallback(false)
      setError(null)
      setCommandState({
        type,
        status: 'success',
        message:
          type === 'intake'
            ? intakeStatus?.using_fallback
              ? `Intake completed using seed fallback. Live source status: ${intakeStatus.live_data_status ?? 'offline'}.`
              : intakeStatus?.last_scan_status === 'success'
                ? `AutoTrader intake completed. ${intakeStatus.live_data_record_count ?? refreshed.intakeSummary?.total_from_autotrader ?? 0} records available.`
                : result.message || 'AutoTrader intake request accepted. Refreshing live status now.'
            : 'Weekly quota enforcement completed successfully.',
      })
    } catch (commandError) {
      setCommandState({
        type,
        status: 'error',
        message: `Could not run ${command.label}: ${commandError.message}`,
      })
    }
  }

  useEffect(() => {
    if (!activeExecutions.length) {
      return
    }

    const hasSelectedPacket = activeExecutions.some(
      (execution) => String(execution.packet_id) === String(outcomeForm.packetId),
    )

    if (!hasSelectedPacket) {
      const first = activeExecutions[0]
      setOutcomeForm((current) => ({
        ...current,
        packetId: String(first.packet_id),
      }))
    }
  }, [activeExecutions, outcomeForm.packetId])

  const selectedExecution = useMemo(
    () => activeExecutions.find((execution) => String(execution.packet_id) === String(outcomeForm.packetId)) ?? null,
    [activeExecutions, outcomeForm.packetId],
  )

  async function submitOutcome(mode) {
    if (!outcomeForm.packetId) {
      setCommandState({
        type: 'outcome',
        status: 'error',
        message: 'Select an active execution before recording an outcome.',
      })
      return
    }

    const actualReturnNumber =
      outcomeForm.actualReturn === '' ? null : Number.parseFloat(outcomeForm.actualReturn)

    if (outcomeForm.actualReturn !== '' && Number.isNaN(actualReturnNumber)) {
      setCommandState({
        type: 'outcome',
        status: 'error',
        message: 'Actual return must be a valid number.',
      })
      return
    }

    if (mode === 'complete' && !outcomeForm.successReason.trim()) {
      setCommandState({
        type: 'outcome',
        status: 'error',
        message: 'Add a success reason before recording a completed outcome.',
      })
      return
    }

    if (mode === 'fail' && !outcomeForm.failureReason.trim()) {
      setCommandState({
        type: 'outcome',
        status: 'error',
        message: 'Add a failure reason before recording a failed outcome.',
      })
      return
    }

    setCommandState({
      type: 'outcome',
      status: 'running',
      message:
        mode === 'complete'
          ? `Recording completed outcome for packet ${outcomeForm.packetId}...`
          : `Recording failed outcome for packet ${outcomeForm.packetId}...`,
    })

    try {
      const path =
        mode === 'complete'
          ? `/execution/complete/${outcomeForm.packetId}`
          : `/execution/fail/${outcomeForm.packetId}`

      await requestJson(path, {
        method: 'POST',
        body: JSON.stringify({
          actual_return: actualReturnNumber,
          success_reason: mode === 'complete' ? outcomeForm.successReason.trim() : null,
          failure_reason: mode === 'fail' ? outcomeForm.failureReason.trim() : null,
          notes: outcomeForm.notes.trim() || null,
        }),
      })

      const refreshed = await loadOperationalData()
      applyOperationalData(refreshed)
      setUsingFallback(false)
      setError(null)
      setCommandState({
        type: 'outcome',
        status: 'success',
        message:
          mode === 'complete'
            ? `Completed outcome recorded for packet ${outcomeForm.packetId}.`
            : `Failed outcome recorded for packet ${outcomeForm.packetId}.`,
      })
      setOutcomeForm((current) => ({
        ...current,
        actualReturn: '',
        successReason: '',
        failureReason: '',
        notes: '',
      }))
    } catch (submitError) {
      setCommandState({
        type: 'outcome',
        status: 'error',
        message: `Could not record the outcome: ${submitError.message}`,
      })
    }
  }

  return (
    <div className="ops-root">
      <OpportunityModal opportunity={selectedOpportunity} onClose={() => setSelectedOpportunity(null)} />
      <header className="ops-header">
        <button className="ops-back" onClick={onBack}>
          ← Hunter
        </button>
        <h1 className="ops-title">Operations Dashboard</h1>
        <div className="ops-header-meta">
          {readiness && (
            <span
              className={`ops-status-badge ops-status-badge--${
                brokerMode === 'live' ? 'live-mode' : 'sandbox-mode'
              }`}
            >
              {brokerModeBadge}
            </span>
          )}
          {readiness && (
            <span
              className={`ops-status-badge ops-status-badge--${brokerApiOnline ? 'ready' : 'not-ready'}`}
            >
              {brokerApiOnline ? 'BROKER API ONLINE' : 'BROKER API OFFLINE'}
            </span>
          )}
          <span className={`ops-status-badge ops-status-badge--${usingFallback ? 'fallback' : 'live'}`}>
            {endpointStatus}
          </span>
          <span className="ops-version">v0.2.0</span>
          <button className="ops-logout" onClick={handleLogout}>
            Logout
          </button>
        </div>
      </header>

      {loading && <div className="ops-loading">Loading operational data...</div>}
      {!loading && error && <div className="ops-error">{error}</div>}

      {!loading && summary && (
        <main className="ops-main">
          <section className="ops-command-deck">
            <div className="ops-command-copy">
              <div className="ops-kicker">Mission Control</div>
              <h2>Hunter readiness panel</h2>
              <p>{missionSummary}</p>
            </div>
            <div className="ops-command-grid">
              <div className="ops-command-card">
                <div className="ops-command-label">First Task</div>
                <div className="ops-command-title">Run AutoTrader Intake</div>
                <div className="ops-command-text">
                  {usingFallback
                    ? 'Backend unavailable. Intake is disabled until the Hunter API reconnects.'
                    : fallbackActive || autotraderOffline
                    ? 'AutoTrader is offline, so this run will use the seeded opportunity file instead of silent empty data.'
                    : 'Best live operational test once the AutoTrader bridge is healthy.'}
                </div>
                <button
                  className="ops-action-button"
                  onClick={() => runCommand('intake')}
                  disabled={commandState.status === 'running' || usingFallback}
                >
                  Run Intake
                </button>
              </div>
              <div className="ops-command-card">
                <div className="ops-command-label">Enforcement</div>
                <div className="ops-command-title">Run Weekly Quotas</div>
                <div className="ops-command-text">
                  Re-check strategy deployment and source discovery requirements.
                </div>
                <button
                  className="ops-action-button ops-action-button--secondary"
                  onClick={() => runCommand('quotas')}
                  disabled={commandState.status === 'running' || usingFallback}
                >
                  Run Quotas
                </button>
              </div>
            </div>
            <div className="ops-command-status">
              <div className="ops-command-status-label">Command Status</div>
              <div
                className={`ops-command-status-message ops-command-status-message--${
                  commandState.status === 'idle' ? 'neutral' : commandState.status
                }`}
              >
                {commandState.message || 'No command run in this session yet.'}
              </div>
            </div>
          </section>

          {readiness && (
            <section className="ops-readiness-panel">
              <div className="ops-readiness-header">
                <h3>System Readiness</h3>
                <span className={`readiness-mode-badge readiness-mode-badge--${brokerMode === 'live' ? 'live' : 'sandbox'}`}>
                  {brokerMode === 'live' ? 'LIVE BROKER ACCOUNT' : brokerMode === 'paper' ? 'PAPER BROKER ACCOUNT' : 'BROKER ACCOUNT UNKNOWN'}
                </span>
                <span
                  className={`readiness-status-badge readiness-status-badge--${
                    brokerApiOnline ? 'ready' : 'blocked'
                  }`}
                >
                  {brokerApiOnline ? 'Broker API Ready' : 'Broker API Offline'}
                </span>
                <span className={`readiness-mode-badge readiness-mode-badge--${executionPolicyMode === 'live' ? 'live' : 'sandbox'}`}>
                  {executionPolicyBadge}
                </span>
              </div>

              {readiness.blockers?.length > 0 && (
                <div className="ops-readiness-blockers">
                  <div className="ops-readiness-group-label">Blockers</div>
                  {readiness.blockers.map((b, i) => (
                    <div key={i} className="ops-readiness-blocker">{b}</div>
                  ))}
                </div>
              )}

              <div className="ops-readiness-modules">
                {Object.entries(readiness.modules ?? {}).map(([key, mod]) => (
                  <div
                    key={key}
                    className={`ops-readiness-module ops-readiness-module--${
                      mod.status === 'connected' || mod.status === 'ok' || mod.status === 'ready' || mod.status === 'open'
                        ? 'ok'
                        : mod.status === 'partial' || mod.status === 'prewired'
                        ? 'partial'
                        : 'offline'
                    }`}
                  >
                    <span className="ops-readiness-module-name">{key.replace('_', ' ')}</span>
                    <span className="ops-readiness-module-status">{mod.status}</span>
                  </div>
                ))}
              </div>

              {readiness.warnings?.length > 0 && (
                <div className="ops-readiness-warnings">
                  {readiness.warnings.slice(0, 3).map((w, i) => (
                    <div key={i} className="ops-readiness-warning">{w}</div>
                  ))}
                </div>
              )}
            </section>
          )}

          {usingFallback && (
            <div className="ops-no-data">
              Backend unavailable. Hunter is intentionally showing an empty offline state instead of fake operational data.
            </div>
          )}

          {!usingFallback && autotraderOffline && (
            <div className="ops-no-data ops-no-data--warning">
              AutoTrader offline / no live data. {fallbackActive
                ? 'Hunter is using seed_opportunities.json for intake.'
                : 'Run intake to activate seed fallback until autotrader.json is healthy again.'}
            </div>
          )}

          <section className="ops-summary-strip">
            <div className="ops-summary-chip">
              <span className="ops-summary-label">AutoTrader</span>
              <strong>{autoTraderHeadline}</strong>
            </div>
            <div className="ops-summary-chip">
              <span className="ops-summary-label">Data mode</span>
              <strong>{autoTraderModeLabel}</strong>
            </div>
            <div className="ops-summary-chip">
              <span className="ops-summary-label">Recent event</span>
              <strong>{formatRelativeDate(recentEvents[0]?.created_at)}</strong>
            </div>
            <div className="ops-summary-chip">
              <span className="ops-summary-label">Intake sources</span>
              <strong>{intakeCount}</strong>
            </div>
            <div className="ops-summary-chip">
              <span className="ops-summary-label">Capital status</span>
              <strong>{budgetStatus === 'open' ? 'Bankroll active' : budgetStatus}</strong>
            </div>
          </section>

          <section className="ops-section">
            <div className="ops-section-header">
              <h2>Diagnostic Snapshot</h2>
              <span className={`budget-status-pill budget-status-pill--${diagnosticInconsistent ? 'no_open_budget' : 'open'}`}>
                {diagnosticInconsistent ? 'ATTENTION' : 'LIVE DIAG'}
              </span>
            </div>
            <div className="diag-snapshot-grid">
              <div className="diag-snapshot-card">
                <div className="diag-snapshot-title">Capital Endpoints</div>
                <div className="diag-snapshot-row">
                  <span>/budget/current</span>
                  <strong>{currentEndpointStatus}</strong>
                </div>
                <div className="diag-snapshot-row">
                  <span>/budget/capital-state</span>
                  <strong>{capitalEndpointStatus}</strong>
                </div>
                <div className="diag-snapshot-row">
                  <span>Broker state mode</span>
                  <strong>{diagnosticStatusLabel}</strong>
                </div>
                <div className="diag-snapshot-row">
                  <span>Planning source</span>
                  <strong>{diagnosticPlanningLabel}</strong>
                </div>
                <div className="diag-snapshot-row">
                  <span>Last broker sync</span>
                  <strong>{diagCapital?.last_successful_broker_sync_at ? formatRelativeDate(diagCapital.last_successful_broker_sync_at) : '—'}</strong>
                </div>
                <div className="diag-snapshot-row">
                  <span>Last broker sync failure</span>
                  <strong>{diagCapital?.last_failed_broker_sync_at ? formatRelativeDate(diagCapital.last_failed_broker_sync_at) : '—'}</strong>
                </div>
              </div>

              <div className="diag-snapshot-card">
                <div className="diag-snapshot-title">Execution Pipeline</div>
                <div className="diag-snapshot-row">
                  <span>Planned / funded</span>
                  <strong>{formatCount(diagExecution?.planned_items)} / {formatCount(diagExecution?.funded_items)}</strong>
                </div>
                <div className="diag-snapshot-row">
                  <span>Executable / active</span>
                  <strong>{formatCount(diagExecution?.executable_items)} / {formatCount(diagExecution?.active_executions)}</strong>
                </div>
                <div className="diag-snapshot-row">
                  <span>Completed / failed</span>
                  <strong>{formatCount(diagExecution?.completed_executions)} / {formatCount(diagExecution?.failed_executions)}</strong>
                </div>
                <div className="diag-snapshot-row">
                  <span>Unsupported task types 24h</span>
                  <strong>{formatCount(diagTaskTypes?.unsupported_task_count_24h ?? diagExecution?.unsupported_task_count_24h)}</strong>
                </div>
                <div className="diag-snapshot-row">
                  <span>Unsupported types</span>
                  <strong>{(diagTaskTypes?.unsupported_task_types ?? diagExecution?.unsupported_task_types ?? []).join(', ') || 'none'}</strong>
                </div>
              </div>

              <div className="diag-snapshot-card">
                <div className="diag-snapshot-title">Recent Exceptions</div>
                <div className="diag-snapshot-row">
                  <span>Capital inconsistency</span>
                  <strong>{diagnosticInconsistent ? 'yes' : 'no'}</strong>
                </div>
                <div className="diag-snapshot-row">
                  <span>Planning says no open budget</span>
                  <strong>{diagCapital?.planning_says_no_open_budget ? 'yes' : 'no'}</strong>
                </div>
                <div className="diag-snapshot-row">
                  <span>Readiness says budget open</span>
                  <strong>{diagCapital?.readiness_says_budget_open ? 'yes' : 'no'}</strong>
                </div>
                <div className="diag-snapshot-error">
                  {diagLastError}
                </div>
              </div>
            </div>
          </section>

          <section className="ops-section">
            <div className="ops-section-header">
              <h2>Capital Deployment</h2>
              <span className={`budget-status-pill budget-status-pill--${brokerSyncSuccess ? 'open' : 'no_open_budget'}`}>
                {capitalTruthLabel.toUpperCase()}
              </span>
            </div>
            <div className="budget-execution-shell">

                {/* ── Broker mismatch warning banner ─────────────────────── */}
                {mismatchDetected && (
                  <div className="broker-mismatch-banner" style={{
                    background: '#fff3cd', border: '1px solid #ffc107',
                    borderRadius: '6px', padding: '10px 14px', marginBottom: '12px',
                    fontSize: '13px', color: '#856404'
                  }}>
                    <strong>⚠ Capital Mismatch Detected</strong> — Broker truth differs from
                    internal ledger.{' '}
                    {mismatchDetails && <span style={{fontFamily:'monospace'}}>{mismatchDetails}</span>}
                    {' '}Dashboard is showing broker-authoritative values.
                  </div>
                )}

                {/* ── Broker sync error warning ───────────────────────────── */}
                {isLiveMode && !brokerSyncSuccess && (
                  <div className="broker-sync-error-banner" style={{
                    background: '#f8d7da', border: '1px solid #f5c6cb',
                    borderRadius: '6px', padding: '10px 14px', marginBottom: '12px',
                    fontSize: '13px', color: '#721c24'
                  }}>
                    <strong>⚠ Broker Sync Failed</strong> — Could not reach Alpaca to verify
                    live capital state. Dashboard may be showing stale internal ledger values.
                  </div>
                )}

                <div className="budget-execution-hero">
                  <div className="budget-execution-copy">
                    <div className="ops-kicker" style={{display:'flex', gap:'8px', alignItems:'center', flexWrap:'wrap'}}>
                      <span>{fallbackActive || autotraderOffline ? 'Seed Mode — Capital Tracking Active' : 'Live Capital State'}</span>
                      <span style={{background:'#e8f4fd', color:'#0c63e4', borderRadius:'4px', padding:'2px 8px', fontSize:'11px', fontWeight:700}}>
                      {strategyMode}
                      </span>
                      <span style={{background:'#f0f0f0', color:'#555', borderRadius:'4px', padding:'2px 8px', fontSize:'11px'}}>
                        {liveExecStrategy}
                      </span>
                      <span style={{background: brokerMode === 'live' ? '#d4edda' : '#fff3cd', color: brokerMode === 'live' ? '#155724' : '#856404', borderRadius:'4px', padding:'2px 8px', fontSize:'11px', fontWeight:600}}>
                        {brokerModeBadge}
                      </span>
                      <span style={{background: executionPolicyMode === 'live' ? '#d4edda' : '#f0f0f0', color: executionPolicyMode === 'live' ? '#155724' : '#555', borderRadius:'4px', padding:'2px 8px', fontSize:'11px', fontWeight:600}}>
                        {executionPolicyBadge}
                      </span>
                    </div>
                    <h3>
                      Broker state stays visible even when planning is closed or broker sync is degraded.
                    </h3>
                    <p style={{fontSize:'12px', color:'#888', marginTop:'4px'}}>
                      {hasCapitalSource ? `${brokerModeLabel}. ${capitalTruthCopy}` : capitalUnavailableCopy}
                      {capitalTruthTimestamp && (
                        <> Last sync attempt: <strong>{capitalTruthTimestamp}</strong>{brokerSyncSuccess ? ' (verified)' : ' (unverified)'}</>
                      )}
                      {!hasCapitalSource && (
                        <> Waiting on <code>/budget/current</code> and <code>/budget/capital-state</code> to return capital data.</>
                      )}
                    </p>
                  </div>
                  <div className="budget-execution-stats">
                    <div
                      className="budget-execution-stat budget-execution-stat--available"
                      title={brokerSyncSuccess
                        ? `Alpaca buying_power (${formatCurrency(brokerBuyingPower)}) minus $${capitalReserveBuffer.toFixed(2)} reserve buffer = ${formatCurrency(availableCapital)}`
                        : 'Internal ledger value (broker sync unavailable)'}
                    >
                      <span className="budget-execution-label">Available Capital</span>
                      <strong>{hasCapitalSource ? formatCurrency(availableCapital) : '—'}</strong>
                      <span className="budget-execution-sublabel">
                        {hasCapitalSource && brokerSyncSuccess
                          ? `Alpaca buying_power − $${capitalReserveBuffer.toFixed(2)} reserve`
                          : hasCapitalSource
                            ? capitalTruthLabel
                            : 'Waiting for capital payload'}
                      </span>
                    </div>
                    <div
                      className="budget-execution-stat budget-execution-stat--allocated"
                      title={brokerSyncSuccess
                        ? `Open position market values + ${openBuyOrdersCount} pending buy order(s) reserved (${formatCurrency(reservedByOpenOrders)}). Total deployed: ${formatCurrency(committedCapital)}`
                        : 'Internal ledger value (broker sync unavailable)'}
                    >
                      <span className="budget-execution-label">Committed Capital</span>
                      <strong>{hasCapitalSource ? formatCurrency(committedCapital) : '—'}</strong>
                      <span className="budget-execution-sublabel">
                        {hasCapitalSource && brokerSyncSuccess
                          ? `${openPositionsCount} positions + ${openBuyOrdersCount} pending order${openBuyOrdersCount !== 1 ? 's' : ''} · Alpaca`
                          : hasCapitalSource
                            ? capitalTruthLabel
                            : 'Waiting for capital payload'}
                      </span>
                    </div>
                    <div
                      className="budget-execution-stat budget-execution-stat--current"
                      title={brokerSyncSuccess
                        ? `Alpaca portfolio_value = ${formatCurrency(brokerPortfolioValue)} (cash ${formatCurrency(brokerCash)} + ${openPositionsCount} position market values)`
                        : 'Internal ledger value (broker sync unavailable)'}
                    >
                      <span className="budget-execution-label">{currentBankrollLabel}</span>
                      <strong>{hasCapitalSource ? formatCurrency(currentBankroll) : '—'}</strong>
                      <span className="budget-execution-sublabel">
                        {hasCapitalSource
                          ? brokerSyncSuccess ? 'Alpaca portfolio_value' : capitalTruthLabel
                          : 'Waiting for capital payload'}
                      </span>
                    </div>
                    <div
                      className="budget-execution-stat budget-execution-stat--funded"
                      title={brokerSyncSuccess
                        ? `${openPositionsCount} open position(s) reported by Alpaca broker`
                        : 'Internal ledger count'}
                    >
                      <span className="budget-execution-label">Open Positions</span>
                      <strong>{formatCount(hasCapitalSource ? openPositionsCount : null)}</strong>
                      <span className="budget-execution-sublabel">
                        {hasCapitalSource
                          ? brokerSyncSuccess ? 'Broker open position count' : capitalTruthLabel
                          : 'Waiting for capital payload'}
                      </span>
                    </div>
                    <div
                      className="budget-execution-stat budget-execution-stat--return"
                      title={`Hunter-tracked realized P/L from closed trades since last restart. $0 means all ${openPositionsCount} position(s) are still open — nothing sold yet. Resets when Render redeploys the app.`}
                    >
                      <span className="budget-execution-label">Realized P/L</span>
                      <strong>{hasCapitalSource ? formatCurrency(realizedProfit) : '—'}</strong>
                      <span className="budget-execution-sublabel">
                        {hasCapitalSource
                          ? `Hunter ledger · ${openPositionsCount > 0 && realizedProfit === 0
                          ? `${openPositionsCount} positions open, none sold yet`
                          : 'resets on restart'}`
                          : 'Waiting for capital payload'}
                      </span>
                    </div>
                    <div
                      className={`budget-execution-stat ${unrealizedPl >= 0 ? 'budget-execution-stat--available' : 'budget-execution-stat--allocated'}`}
                      title={brokerSyncSuccess
                        ? `Sum of unrealized_pl across all ${openPositionsCount} open Alpaca position(s). Paper gain/loss if all positions were closed right now.`
                        : 'Unrealized P/L unavailable (broker sync failed)'}
                    >
                      <span className="budget-execution-label">Unrealized P/L</span>
                      <strong style={{color: unrealizedPl >= 0 ? '#1a7f37' : '#cf222e'}}>
                        {hasCapitalSource ? `${unrealizedPl >= 0 ? '+' : ''}${formatCurrency(unrealizedPl)}` : '—'}
                      </strong>
                      <span className="budget-execution-sublabel">
                        {hasCapitalSource && brokerSyncSuccess
                          ? `Σ ${openPositionsCount} position${openPositionsCount !== 1 ? 's' : ''} · Alpaca`
                          : hasCapitalSource
                            ? 'Broker sync unavailable'
                            : 'Waiting for capital payload'}
                      </span>
                    </div>
                    <div
                      className="budget-execution-stat budget-execution-stat--funded"
                      title={brokerSyncSuccess
                        ? `${openOrdersCount} open buy order(s) currently reserving ${formatCurrency(reservedByOpenOrders)}`
                        : 'Internal ledger count of open buy orders'}
                    >
                      <span className="budget-execution-label">Open Orders</span>
                      <strong>{formatCount(hasCapitalSource ? openOrdersCount : null)}</strong>
                      <span className="budget-execution-sublabel">
                        {hasCapitalSource && reservedByOpenOrders > 0
                          ? `${formatCurrency(reservedByOpenOrders)} reserved`
                          : hasCapitalSource && brokerSyncSuccess
                            ? 'No pending buy orders'
                            : hasCapitalSource
                              ? capitalTruthLabel
                              : 'Waiting for capital payload'}
                      </span>
                    </div>
                  </div>
                </div>

                {/* ── Broker account breakdown strip ──────────────────────── */}
                {brokerSyncSuccess && (
                  <div style={{display:'flex', gap:'12px', flexWrap:'wrap', padding:'8px 0', borderTop:'1px solid #eee', marginTop:'8px', fontSize:'11px', color:'#666'}}>
                    <span title="Settled cash balance in Alpaca account">💵 <strong>Cash:</strong> {formatCurrency(brokerCash)}</span>
                    <span title="Alpaca buying_power — what Alpaca says you can deploy">📊 <strong>Buying Power:</strong> {formatCurrency(brokerBuyingPower)}</span>
                    <span title="Alpaca portfolio_value = cash + all position market values">📈 <strong>Portfolio Value:</strong> {formatCurrency(brokerPortfolioValue)}</span>
                    {reservedByOpenOrders > 0 && (
                      <span title={`$${reservedByOpenOrders.toFixed(2)} reserved by ${openBuyOrdersCount} open buy order(s)`}>
                        ⏳ <strong>Pending Buys:</strong> {formatCurrency(reservedByOpenOrders)} ({openBuyOrdersCount})
                      </span>
                    )}
                    <span title="Effective buying power after reserve buffer and pending buy orders">🔓 <strong>Deployable:</strong> {formatCurrency(effectiveBuyingPower)}</span>
                    {lastBrokerSyncAt && (
                      <span>🕐 <strong>Synced:</strong> {new Date(lastBrokerSyncAt).toLocaleTimeString()} ✓</span>
                    )}
                  </div>
                )}
                {/* ── Per-position strip (live mode only) ─────────────────── */}
                {isLiveMode && brokerPositions.length > 0 && (
                  <div style={{display:'flex', gap:'8px', flexWrap:'wrap', padding:'6px 0', borderTop:'1px solid #eee', marginTop:'4px', fontSize:'11px', color:'#555'}}>
                    <span style={{fontWeight:600, color:'#888'}}>Positions:</span>
                    {brokerPositions.map(p =>
                      <span key={p.symbol} style={{color: (p.unrealized_pl||0) >= 0 ? '#1a7f37' : '#cf222e'}}>
                        {p.symbol} {(p.unrealized_pl||0) >= 0 ? '+' : ''}{(p.unrealized_pl||0).toFixed(2)} · hold {formatDurationMinutes(p.hold_minutes)}
                        {p.max_hold_minutes != null && ` / ${formatDurationMinutes(p.max_hold_minutes)} max`}
                        {p.over_max_hold ? ' · over max' : ''}
                      </span>
                    )}
                  </div>
                )}

                <div className="budget-state-grid">
                  <div className="budget-state-panel">
                    <div className="budget-state-panel__header">
                      <h3>Broker State</h3>
                      <span className={`budget-status-pill budget-status-pill--${brokerSyncSuccess ? 'open' : 'no_open_budget'}`}>
                        {brokerSyncSuccess ? 'VERIFIED' : 'FALLBACK'}
                      </span>
                    </div>
                    <div className="budget-row budget-row--primary">
                      <div className="budget-cell">
                        <div className="budget-value">{hasCapitalSource ? formatCurrency(availableCapital) : '—'}</div>
                        <div className="budget-label">Available Capital</div>
                      </div>
                      <div className="budget-cell">
                        <div className="budget-value">{hasCapitalSource ? formatCurrency(committedCapital) : '—'}</div>
                        <div className="budget-label">Committed Capital</div>
                      </div>
                      <div className="budget-cell">
                        <div className="budget-value">{hasCapitalSource ? formatCurrency(currentBankroll) : '—'}</div>
                        <div className="budget-label">{currentBankrollLabel}</div>
                      </div>
                      <div className="budget-cell">
                        <div className={`budget-value${unrealizedPl >= 0 ? ' budget-value--pos' : ' budget-value--neg'}`}>
                          {hasCapitalSource ? `${unrealizedPl >= 0 ? '+' : ''}${formatCurrency(unrealizedPl)}` : '—'}
                        </div>
                        <div className="budget-label">Unrealized P/L</div>
                      </div>
                      <div className="budget-cell">
                        <div className={`budget-value${realizedProfit >= 0 ? ' budget-value--pos' : ' budget-value--neg'}`}>
                          {hasCapitalSource ? formatCurrency(realizedProfit) : '—'}
                        </div>
                        <div className="budget-label">Realized P/L</div>
                      </div>
                      <div className="budget-cell">
                        <div className="budget-value">{formatCount(hasCapitalSource ? openPositionsCount : null)}</div>
                        <div className="budget-label">Open Positions</div>
                      </div>
                      <div className="budget-cell">
                        <div className="budget-value">{formatCount(hasCapitalSource ? openOrdersCount : null)}</div>
                        <div className="budget-label">Open Orders</div>
                      </div>
                    </div>
                    <div className="budget-meta">
                      <span>{hasCapitalSource ? capitalTruthCopy : capitalUnavailableCopy}</span>
                      {capitalTruthTimestamp && (
                        <>
                          <span className="budget-sep">·</span>
                          <span>Last sync {capitalTruthTimestamp}</span>
                        </>
                      )}
                    </div>
                  </div>

                  <div className="budget-state-panel">
                    <div className="budget-state-panel__header">
                      <h3>Pipeline / Planning State</h3>
                      <span className={`budget-status-pill budget-status-pill--${budgetStatus}`}>
                        {budgetStatus.toUpperCase()}
                      </span>
                    </div>
                    <div className="budget-row budget-row--secondary">
                      <div className="budget-cell">
                        <div className="budget-value">{hasPlanningBudget ? formatCurrency(startingBankroll) : '—'}</div>
                        <div className="budget-label">Starting Bankroll</div>
                      </div>
                      <div className="budget-cell">
                        <div className="budget-value">{hasPlanningBudget ? formatCurrency(originalBaseCapital) : '—'}</div>
                        <div className="budget-label">Original Base Capital</div>
                      </div>
                      <div className="budget-cell">
                        <div className="budget-value">{hasPlanningBudget ? formatCurrency(doublingTarget) : '—'}</div>
                        <div className="budget-label">Doubling Threshold</div>
                      </div>
                      <div className="budget-cell">
                        <div className="budget-value">{hasPlanningBudget ? `${doublingProgressPct.toFixed(0)}%` : '—'}</div>
                        <div className="budget-label">Progress to Threshold</div>
                      </div>
                      <div className="budget-cell">
                        <div className={`budget-value${capitalMatchEligible ? ' budget-value--pos' : ''}`}>
                          {hasPlanningBudget ? (capitalMatchEligible ? 'YES' : 'NO') : '—'}
                        </div>
                        <div className="budget-label">Capital Match Eligible</div>
                      </div>
                      <div className="budget-cell">
                        <div className={`budget-value${capitalMatchAmount > 0 ? ' budget-value--pos' : ''}`}>
                          {hasPlanningBudget ? formatCurrency(capitalMatchAmount) : '—'}
                        </div>
                        <div className="budget-label">Recommended Match Amount</div>
                      </div>
                      <div className="budget-cell">
                        <div className="budget-value">{hasPlanningBudget ? formatCount(summary?.ready_packets ?? 0) : '—'}</div>
                        <div className="budget-label">Ready Packets</div>
                      </div>
                      <div className="budget-cell">
                        <div className="budget-value">{hasPlanningBudget ? formatCount(pipeline?.by_status?.planned ?? 0) : '—'}</div>
                        <div className="budget-label">Planned Count</div>
                      </div>
                      <div className="budget-cell">
                        <div className="budget-value">{hasPlanningBudget ? formatCount(allocations?.length ?? budget?.allocations_by_source?.length ?? 0) : '—'}</div>
                        <div className="budget-label">Budgeted Count</div>
                      </div>
                      <div className="budget-cell">
                        <div className="budget-value">{hasPlanningBudget ? formatCurrency(strategies?.total_expected_return ?? 0) : '—'}</div>
                        <div className="budget-label">Expected Return</div>
                      </div>
                    </div>
                    <div className="budget-meta">
                      <span>Status {budgetStatus}</span>
                      <span className="budget-sep">·</span>
                      <span>Cycle start {planningCycleStart ?? 'n/a'}</span>
                      <span className="budget-sep">·</span>
                      <span>Review end {planningCycleEnd ?? evaluationEndDate ?? 'n/a'}</span>
                      <span className="budget-sep">·</span>
                      <span>Match eligible {hasPlanningBudget ? (capitalMatchEligible ? 'yes' : 'no') : 'n/a'}</span>
                    </div>
                  </div>
                </div>

                {hasPlanningBudget && (
                  <div className="budget-progress-block">
                    <div className="budget-progress-header">
                      <div>
                        <div className="budget-progress-title">Month-End Evaluation</div>
                        <div className="budget-progress-copy">
                          Progress toward doubling from {formatCurrency(startingBankroll)} to{' '}
                          {formatCurrency(doublingTarget)}
                        </div>
                      </div>
                      <div className="budget-progress-value">{doublingProgressPct.toFixed(0)}%</div>
                    </div>
                    <div className="budget-progress-track">
                      <div
                        className="budget-progress-fill"
                        style={{ width: `${doublingProgressPct}%` }}
                      />
                    </div>
                    <div className="budget-progress-meta">
                      <span>Current {formatCurrency(currentBankroll)}</span>
                      <span className="budget-sep">·</span>
                      <span>Days remaining {monthEndReview?.days_remaining ?? 'n/a'}</span>
                      <span className="budget-sep">·</span>
                      <span>Potential match {formatCurrency(capitalMatchAmount)}</span>
                    </div>
                  </div>
                )}

                <div className="budget-allocation-panel">
                  <div className="budget-allocation-header">
                    <h3>Funded Opportunities</h3>
                    <span className="ops-count">{fundedOpportunities.length}</span>
                  </div>
                  {fundedOpportunities.length === 0 ? (
                    <div className="ops-no-data">No allocations recorded yet.</div>
                  ) : (
                    <div className="budget-allocation-list">
                      {fundedOpportunities.map((allocation, index) => (
                        <div
                          key={allocation.id ?? allocation.source_id ?? `allocation-${index}`}
                          className="budget-allocation-row"
                        >
                          <div className="budget-allocation-copy">
                            <div className="budget-allocation-title">
                              {allocation.allocation_name || allocation.source_id}
                            </div>
                            <div className="budget-allocation-meta">
                              <span>{allocation.source_id ?? 'unlinked'}</span>
                              <span>{allocation.status ?? 'planned'}</span>
                              {allocation.expected_return != null && (
                                <span>exp {formatCurrency(allocation.expected_return)}</span>
                              )}
                              {allocation.net_result != null && allocation.net_result !== 0 && (
                                <span>net {formatCurrency(allocation.net_result)}</span>
                              )}
                            </div>
                          </div>
                          <div className="budget-allocation-amount">
                            {formatCurrency(allocation.amount_allocated)}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
          </section>

          <section className="ops-section">
            <div className="ops-section-header">
              <h2>Execution Outcomes</h2>
              <span className="ops-count">{activeExecutions.length} active</span>
            </div>
            <div className="execution-grid">
              <div className="ops-panel execution-panel">
                <div className="execution-panel-header">
                  <div>
                    <div className="ops-kicker">Operator Console</div>
                    <h3>Record a real execution outcome</h3>
                  </div>
                  <div className="execution-counts">
                    <span>Completed {completedExecutions.length}</span>
                    <span>Failed {failedExecutions.length}</span>
                  </div>
                </div>

                {activeExecutions.length === 0 ? (
                  <div className="ops-no-data">No active or in-progress executions are ready for outcome entry.</div>
                ) : (
                  <>
                    <div className="execution-active-list">
                      {activeExecutions.map((execution) => (
                        <button
                          key={execution.packet_id}
                          type="button"
                          className={`execution-active-card${
                            String(execution.packet_id) === String(outcomeForm.packetId)
                              ? ' execution-active-card--selected'
                              : ''
                          }`}
                          onClick={() =>
                            setOutcomeForm((current) => ({
                              ...current,
                              packetId: String(execution.packet_id),
                            }))
                          }
                        >
                          <div className="execution-active-top">
                            <span className="execution-pill">Packet {execution.packet_id}</span>
                            <span className="execution-state-pill">{execution.execution_state}</span>
                          </div>
                          <div className="execution-active-title">{execution.source_id}</div>
                          <div className="execution-active-meta">
                            <span>{execution.priority_band ?? 'unknown'} priority</span>
                            {execution.estimated_return != null && (
                              <span>est {formatCurrency(execution.estimated_return)}</span>
                            )}
                            {execution.allocation?.amount_allocated != null && (
                              <span>alloc {formatCurrency(execution.allocation.amount_allocated)}</span>
                            )}
                          </div>
                        </button>
                      ))}
                    </div>

                    <div className="execution-form-shell">
                      <div className="execution-form-grid">
                        <label className="execution-field">
                          <span>Packet</span>
                          <select
                            value={outcomeForm.packetId}
                            onChange={(event) =>
                              setOutcomeForm((current) => ({
                                ...current,
                                packetId: event.target.value,
                              }))
                            }
                          >
                            {activeExecutions.map((execution) => (
                              <option key={execution.packet_id} value={execution.packet_id}>
                                {execution.packet_id} - {execution.source_id}
                              </option>
                            ))}
                          </select>
                        </label>
                        <label className="execution-field">
                          <span>Actual Return</span>
                          <input
                            type="number"
                            inputMode="decimal"
                            step="0.01"
                            value={outcomeForm.actualReturn}
                            onChange={(event) =>
                              setOutcomeForm((current) => ({
                                ...current,
                                actualReturn: event.target.value,
                              }))
                            }
                            placeholder="0.00"
                          />
                        </label>
                      </div>

                      {selectedExecution && (
                        <div className="execution-selected-meta">
                          <span>Selected {selectedExecution.source_id}</span>
                          <span className="budget-sep">·</span>
                          <span>State {selectedExecution.execution_state}</span>
                          <span className="budget-sep">·</span>
                          <span>
                            Allocation{' '}
                            {selectedExecution.allocation?.amount_allocated != null
                              ? formatCurrency(selectedExecution.allocation.amount_allocated)
                              : 'none'}
                          </span>
                        </div>
                      )}

                      <label className="execution-field">
                        <span>Success Reason</span>
                        <input
                          type="text"
                          value={outcomeForm.successReason}
                          onChange={(event) =>
                            setOutcomeForm((current) => ({
                              ...current,
                              successReason: event.target.value,
                            }))
                          }
                          placeholder="Required for completed outcomes"
                        />
                      </label>

                      <label className="execution-field">
                        <span>Failure Reason</span>
                        <input
                          type="text"
                          value={outcomeForm.failureReason}
                          onChange={(event) =>
                            setOutcomeForm((current) => ({
                              ...current,
                              failureReason: event.target.value,
                            }))
                          }
                          placeholder="Required for failed outcomes"
                        />
                      </label>

                      <label className="execution-field">
                        <span>Notes</span>
                        <textarea
                          rows="3"
                          value={outcomeForm.notes}
                          onChange={(event) =>
                            setOutcomeForm((current) => ({
                              ...current,
                              notes: event.target.value,
                            }))
                          }
                          placeholder="Operator notes for audit trail"
                        />
                      </label>

                      <div className="execution-form-actions">
                        <button
                          type="button"
                          className="ops-action-button"
                          onClick={() => submitOutcome('complete')}
                          disabled={commandState.status === 'running'}
                        >
                          Record Complete Outcome
                        </button>
                        <button
                          type="button"
                          className="ops-action-button ops-action-button--secondary"
                          onClick={() => submitOutcome('fail')}
                          disabled={commandState.status === 'running'}
                        >
                          Record Failed Outcome
                        </button>
                      </div>
                    </div>
                  </>
                )}
              </div>

              <div className="ops-panel execution-summary-panel">
                <div className="execution-panel-header">
                  <div>
                    <div className="ops-kicker">Live Feedback</div>
                    <h3>Execution and performance state</h3>
                  </div>
                </div>
                <div className="stat-grid execution-summary-stats">
                  <StatCard label="Active Executions" value={executionStatus?.counts?.active ?? 0} />
                  <StatCard label="Completed" value={executionStatus?.counts?.completed ?? 0} />
                  <StatCard label="Failed" value={executionStatus?.counts?.failed ?? 0} />
                  <StatCard
                    label="Outcomes Logged"
                    value={performanceSummary?.outcomes_recorded ?? 0}
                    sub={
                      performanceSummary?.success_rate != null
                        ? `${(performanceSummary.success_rate * 100).toFixed(0)}% success`
                        : 'Awaiting first real outcome'
                    }
                  />
                </div>
                <div className="execution-summary-meta">
                  <span>Total actual return {formatCurrency(performanceSummary?.total_actual_return ?? 0)}</span>
                  <span className="budget-sep">·</span>
                  <span>Best lane {performanceSummary?.best_lane ?? 'n/a'}</span>
                  <span className="budget-sep">·</span>
                  <span>Weakest lane {performanceSummary?.weakest_lane ?? 'n/a'}</span>
                </div>
                <div className="timing-snapshot">
                  <div className="timing-snapshot__header">
                    <div className="ops-kicker">Position Timing</div>
                    <h4>Return timing snapshot</h4>
                  </div>
                  {brokerPositions.length === 0 && recentTimingOutcomes.length === 0 ? (
                    <div className="ops-no-data">No live position timing has been recorded yet.</div>
                  ) : (
                    <>
                      {brokerPositions.length > 0 && (
                        <div className="timing-strip">
                          {brokerPositions.map((position) => (
                            <div
                              key={`open-${position.symbol}`}
                              className={`timing-pill${position.over_max_hold ? ' timing-pill--warn' : ''}`}
                            >
                              <strong>{position.symbol}</strong>
                              <span>Open hold {formatDurationMinutes(position.hold_minutes)}</span>
                              <span>Max {formatDurationMinutes(position.max_hold_minutes)}</span>
                            </div>
                          ))}
                        </div>
                      )}
                      {recentTimingOutcomes.length > 0 && (
                        <div className="timing-list">
                          {recentTimingOutcomes.slice(0, 6).map((item, index) => (
                            <div
                              key={`${item.symbol}-${item.exited_at ?? item.recorded_at ?? index}`}
                              className="timing-row"
                            >
                              <div className="timing-row__main">
                                <strong>{item.symbol ?? 'position'}</strong>
                                <span>
                                  {item.exited_at
                                    ? `Closed ${formatRelativeDate(item.exited_at)}`
                                    : `Recorded ${formatRelativeDate(item.recorded_at)}`}
                                </span>
                              </div>
                              <div className="timing-row__metrics">
                                <span>Hold {formatDurationMinutes(item.hold_duration_minutes)}</span>
                                <span>
                                  Time to realized profit {item.time_to_realized_profit_minutes != null
                                    ? formatDurationMinutes(item.time_to_realized_profit_minutes)
                                    : 'n/a'}
                                </span>
                              </div>
                            </div>
                          ))}
                        </div>
                      )}
                    </>
                  )}
                </div>
              </div>
            </div>
          </section>

          <section className="ops-section">
            <div className="ops-section-header">
              <h2>Weekly Quotas</h2>
              <QuotaBadge met={quotas?.all_met} />
            </div>
            <div className="stat-grid">
              <StatCard
                label="Sources Discovered"
                value={`${discoveryQuota?.sources_found_this_week ?? 0} / ${discoveryQuota?.required ?? 10}`}
                sub={discoveryQuota?.quota_met ? 'On target' : `${discoveryQuota?.shortfall ?? 0} needed`}
                highlight={!discoveryQuota?.quota_met}
              />
              <StatCard
                label="Active Strategies"
                value={`${strategyQuota?.active_count ?? 0} / ${strategyQuota?.required ?? 10}`}
                sub={strategyQuota?.quota_met ? 'On target' : `${strategyQuota?.shortfall ?? 0} shortfall`}
                highlight={!strategyQuota?.quota_met}
              />
              <StatCard
                label="Activated This Week"
                value={strategies?.activated_this_week ?? 0}
                sub={`${strategies?.retired_this_week ?? 0} retired`}
              />
              <StatCard
                label="Replacements Required"
                value={strategies?.replacement_strategies_required ?? 0}
                highlight={(strategies?.replacement_strategies_required ?? 0) > 0}
              />
            </div>
          </section>

          <section className="ops-section">
            <div className="ops-section-header">
              <h2>Pressure</h2>
              <span className={`pressure-badge pressure-badge--${pressureLevel}`}>
                {pressureLevel.toUpperCase()}
              </span>
            </div>
            {pressureSignals.length === 0 ? (
              <div className="pressure-clear">No active pressure signals. All systems nominal.</div>
            ) : (
              <div className="pressure-list">
                {pressureSignals.map((signal) => (
                  <div key={signal} className="pressure-item">
                    <span className="pressure-bullet">▲</span>
                    <span>{signal}</span>
                  </div>
                ))}
              </div>
            )}
          </section>

          <section className="ops-section">
            <div className="ops-section-header">
              <h2>Recent Events</h2>
              <span className="ops-count">{recentEvents.length}</span>
            </div>
            <div className="ops-event-list">
              {recentEvents.length === 0 ? (
                <div className="ops-no-data">No recent operational events available.</div>
              ) : (
                recentEvents.map((event, index) => (
                  <div key={event.id ?? `${event.event_type}-${index}`} className="ops-event-row">
                    <div className="ops-event-type">{event.event_type ?? 'event'}</div>
                    <div className="ops-event-meta">
                      <span>{event.source_id ?? 'system'}</span>
                      <span>{formatRelativeDate(event.created_at)}</span>
                    </div>
                  </div>
                ))
              )}
            </div>
          </section>

          <section className="ops-section">
            <h2>System Health</h2>
            <div className="stat-grid">
              <StatCard label="Total Opportunities" value={summary.total_opportunities} />
              <StatCard label="Active" value={summary.active_opportunities} />
              <StatCard
                label="Elite"
                value={summary.elite_opportunities}
                highlight={summary.elite_opportunities > 0}
              />
              <StatCard label="High" value={summary.high_opportunities} />
              <StatCard
                label="Unacked Alerts"
                value={summary.unacknowledged_alerts}
                highlight={summary.unacknowledged_alerts > 0}
              />
              <StatCard label="Ready Packets" value={summary.ready_packets} />
              <StatCard
                label="Underperforming"
                value={summary.underperforming_strategies}
                highlight={summary.underperforming_strategies > 0}
              />
            </div>
          </section>

          {atStatus && (
            <section className="ops-section">
              <h2>AutoTrader</h2>
              <div className="at-panel">
                <div className="at-status-row">
                  <StatusDot ok={liveFeedReady} label={liveFeedReady ? 'Live data ready' : 'Live data offline'} />
                  <StatusDot ok={fallbackActive} label={fallbackActive ? 'Seed fallback active' : 'Fallback idle'} />
                  <span className={`at-scan-status at-scan-status--${atStatus.last_scan_status ?? 'never'}`}>
                    {atStatus.last_scan_status ?? 'never run'}
                  </span>
                  {atStatus.last_scan_at && (
                    <span className="at-scan-time">
                      Last scan {new Date(atStatus.last_scan_at).toLocaleString()}
                    </span>
                  )}
                </div>
                <div className={`at-health-banner at-health-banner--${liveFeedReady ? 'live' : 'offline'}`}>
                  <strong>{autoTraderHeadline}</strong>
                  <span>{atStatus.live_data_message ?? atStatus.last_error}</span>
                </div>
                {fallbackActive && (
                  <div className="at-fallback-banner">
                    Seed fallback is active from <code>{atStatus.config?.seed_path}</code>.
                  </div>
                )}
                {atStatus.last_error && <div className="at-error">! {atStatus.last_error}</div>}
                <div className="at-counts">
                  {[
                    ['Scanned', atStatus.last_scan_counts?.scanned],
                    ['Inserted', atStatus.last_scan_counts?.inserted],
                    ['Updated', atStatus.last_scan_counts?.updated],
                    ['Skipped', atStatus.last_scan_counts?.skipped],
                    ['Errors', atStatus.last_scan_counts?.errors],
                  ].map(([label, value]) => (
                    <div key={label} className="at-count-cell">
                      <div className="at-count-value">{value ?? 0}</div>
                      <div className="at-count-label">{label}</div>
                    </div>
                  ))}
                </div>
                <div className="at-config">
                  <span className="at-config-key">mode</span>
                  <span className="at-config-val">{atStatus.current_data_mode}</span>
                  <span className="at-config-key">live status</span>
                  <span className="at-config-val">{liveDataStatus}</span>
                  <span className="at-config-key">source</span>
                  <span className="at-config-val">{atStatus.config?.source_type}</span>
                  <span className="at-config-key">profit</span>
                  <span className="at-config-val">
                    ${(intakeSummary?.total_estimated_monthly_profit ?? 0).toLocaleString()}
                  </span>
                  <span className="at-config-key">confidence</span>
                  <span className="at-config-val">
                    {intakeSummary?.average_confidence != null
                      ? `${(intakeSummary.average_confidence * 100).toFixed(0)}%`
                      : 'n/a'}
                  </span>
                  <span className="at-config-key">records</span>
                  <span className="at-config-val">{atStatus.live_data_record_count ?? 0}</span>
                  {atStatus.live_data_updated_at && (
                    <>
                      <span className="at-config-key">updated</span>
                      <span className="at-config-val">
                        {new Date(atStatus.live_data_updated_at).toLocaleString()}
                      </span>
                    </>
                  )}
                  {atStatus.config?.file_path && (
                    <>
                      <span className="at-config-key">path</span>
                      <span className="at-config-val">{atStatus.config.file_path}</span>
                    </>
                  )}
                  {atStatus.config?.http_url && (
                    <>
                      <span className="at-config-key">url</span>
                      <span className="at-config-val">{atStatus.config.http_url}</span>
                    </>
                  )}
                </div>
              </div>
            </section>
          )}

          {packets.length > 0 && (
            <section className="ops-section">
              <div className="ops-section-header">
                <h2>Routing Plan</h2>
                <span className="ops-count">{readyPackets.length} ready</span>
              </div>
              <div className="packet-list">
                {packets.slice(0, 10).map((packet) => (
                  <div key={packet.id ?? packet.packet_id} className={`packet-row packet-row--${packet.status}`}>
                    <span className={`packet-status packet-status--${packet.status}`}>{packet.status}</span>
                    <span className={`packet-band packet-band--${packet.priority_band ?? 'low'}`}>
                      {packet.priority_band?.toUpperCase() ?? '—'}
                    </span>
                    <span className="packet-title">{packet.source_id}</span>
                    {packet.estimated_return != null && (
                      <span className="packet-return">${packet.estimated_return?.toLocaleString()}</span>
                    )}
                    {packet.next_actions?.length > 0 && (
                      <span className="packet-actions">
                        {packet.next_actions.length} action{packet.next_actions.length > 1 ? 's' : ''}
                      </span>
                    )}
                  </div>
                ))}
                {packets.length > 10 && <div className="alert-more">+{packets.length - 10} more packets</div>}
              </div>
            </section>
          )}

          {alerts.length > 0 && (
            <section className="ops-section">
              <h2>
                Active Alerts <span className="ops-count">{alerts.length}</span>
              </h2>
              <div className="alerts-list">
                {alerts.slice(0, 10).map((alert) => (
                  <div key={alert.id} className={`alert-row alert-row--${alert.priority}`}>
                    <span className="alert-priority">{alert.priority.toUpperCase()}</span>
                    <span className="alert-title">{alert.title}</span>
                    <span className="alert-type">{alert.alert_type}</span>
                    <div className="alert-body">
                      {alert.body || 'No alert detail provided.'}
                    </div>
                    <div className="alert-meta">
                      {(() => {
                        const { taskId, canAcknowledge, canRetry } = alertActionHints(alert)
                        return (
                          <>
                            {taskId && <span className="alert-task-id">task {taskId}</span>}
                            <span className="alert-action-hint">
                              {canAcknowledge ? 'ack available' : 'ack unavailable'}
                            </span>
                            <span className="alert-action-hint">
                              {canRetry ? 'retry available' : 'retry unavailable'}
                            </span>
                          </>
                        )
                      })()}
                    </div>
                  </div>
                ))}
                {alerts.length > 10 && <div className="alert-more">+{alerts.length - 10} more alerts</div>}
              </div>
            </section>
          )}

          {strategies?.strategies?.length > 0 && (
            <section className="ops-section">
              <h2>
                Employed Strategies <span className="ops-count">{strategies.active_count}</span>
              </h2>
              <div className="strategy-list">
                {strategies.strategies.map((strategy) => (
                  <div key={strategy.strategy_id} className="strategy-row">
                    <div className="strategy-name">{strategy.strategy_name}</div>
                    <div className="strategy-meta">
                      <span className="strategy-cat">{strategy.category}</span>
                      {strategy.expected_return != null && (
                        <span className="strategy-return">exp ${strategy.expected_return.toLocaleString()}</span>
                      )}
                      {strategy.actual_return != null && (
                        <span className={`strategy-actual${strategy.actual_return >= 0 ? '' : ' neg'}`}>
                          act ${strategy.actual_return.toLocaleString()}
                        </span>
                      )}
                      <span
                        className={`strategy-evidence${
                          strategy.evidence_of_activity ? ' has-evidence' : ' no-evidence'
                        }`}
                      >
                        {strategy.evidence_of_activity ? '● active' : '○ no evidence'}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            </section>
          )}

          {(() => {
            const displayOpps = liveOpportunities?.opportunities?.length
              ? liveOpportunities.opportunities
              : top10
            const isLive = Boolean(liveOpportunities?.opportunities?.length)
            return displayOpps.length > 0 ? (
              <section className="ops-section">
                <div className="ops-section-header">
                  <h2>
                    Top Opportunities <span className="ops-count">{displayOpps.length}</span>
                  </h2>
                  <span className={`ops-data-source-badge ops-data-source-badge--${isLive ? 'live' : 'cached'}`}>
                    {isLive
                      ? `Live · ${liveOpportunities.source_type ?? 'sources'}`
                      : 'Pipeline cache'}
                  </span>
                </div>
                <div className="opp-grid">
                  {displayOpps.map((opportunity) => (
                    <div
                      key={opportunity.source_id}
                      className={`opp-card opp-card--${opportunity.priority_band ?? 'low'} opp-card--clickable`}
                      onClick={() => setSelectedOpportunity(opportunity)}
                      role="button"
                      tabIndex={0}
                      onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') setSelectedOpportunity(opportunity) }}
                    >
                      <div className="opp-card-header">
                        <span className={`opp-band opp-band--${opportunity.priority_band ?? 'low'}`}>
                          {opportunity.priority_band?.toUpperCase() ?? 'LOW'}
                        </span>
                        <span className="opp-score">{opportunity.score?.toFixed(0) ?? '—'}</span>
                      </div>
                      <div className="opp-desc">{opportunity.description}</div>
                      <div className="opp-footer">
                        <span className="opp-status">{opportunity.status}</span>
                        {opportunity.origin_module && (
                          <span className="opp-origin">{opportunity.origin_module.replace('_', ' ')}</span>
                        )}
                        {opportunity.estimated_profit != null && (
                          <span className="opp-profit">${opportunity.estimated_profit?.toLocaleString()}/mo</span>
                        )}
                      </div>
                      {opportunity.marketplace_lane && (
                        <div className="opp-card-mkt-row">
                          <span className="mkt-lane-badge">FB Marketplace</span>
                          {opportunity.marketplace_routing_label && (
                            <span className={`mkt-routing-badge mkt-routing-badge--${opportunity.marketplace_routing_label}`}>
                              {opportunity.marketplace_routing_label.replace(/_/g, ' ')}
                            </span>
                          )}
                          {opportunity.marketplace_execution_state && (
                            <span className={`mkt-exec-state mkt-exec-state--${opportunity.marketplace_execution_state}`}>
                              {opportunity.marketplace_execution_state.replace(/_/g, ' ')}
                            </span>
                          )}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </section>
            ) : null
          })()}

          <section className="ops-section">
            <div className="ops-section-header">
              <h2>
                Transaction Log{' '}
                {txSortedRows.length > 0 && (
                  <span className="ops-count">{txSortedRows.length}</span>
                )}
              </h2>
              {txSortedRows.length > 0 && (
                <button className="tx-export-btn" onClick={() => exportCsv(txSortedRows)}>
                  Export CSV
                </button>
              )}
            </div>
            {txSortedRows.length === 0 ? (
              <div className="tx-empty">
                {transactions === null
                  ? 'Loading transactions...'
                  : 'No transactions recorded yet. Allocate capital to see the log here.'}
              </div>
            ) : (
              <>
                <div className="tx-table-wrap">
                  <table className="tx-table">
                    <thead>
                      <tr>
                        <th onClick={() => txSort('timestamp')} className="tx-th tx-th--sortable">Timestamp{txSortArrow('timestamp')}</th>
                        <th onClick={() => txSort('allocation_name')} className="tx-th tx-th--sortable">Opportunity{txSortArrow('allocation_name')}</th>
                        <th onClick={() => txSort('amount_committed')} className="tx-th tx-th--sortable tx-th--num">Committed{txSortArrow('amount_committed')}</th>
                        <th onClick={() => txSort('actual_return')} className="tx-th tx-th--sortable tx-th--num">Realized P&L{txSortArrow('actual_return')}</th>
                        <th onClick={() => txSort('status')} className="tx-th tx-th--sortable">Status{txSortArrow('status')}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {txPageRows.map(row => (
                        <tr key={row.id} className="tx-row">
                          <td className="tx-td tx-td--ts">
                            {row.timestamp ? new Date(row.timestamp).toLocaleString() : '—'}
                          </td>
                          <td className="tx-td">
                            <div className="tx-opp-name">{row.allocation_name || '—'}</div>
                            {row.source_id && (
                              <div className="tx-source-id">{row.source_id}</div>
                            )}
                          </td>
                          <td className="tx-td tx-td--num">
                            {row.amount_committed != null ? `$${Number(row.amount_committed).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '—'}
                          </td>
                          <td className={`tx-td tx-td--num ${row.actual_return == null ? '' : row.actual_return >= 0 ? 'tx-pos' : 'tx-neg'}`}>
                            {row.actual_return != null
                              ? `${row.actual_return >= 0 ? '+' : ''}$${Math.abs(row.actual_return).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
                              : '—'}
                          </td>
                          <td className="tx-td">
                            <span className={`tx-status tx-status--${row.status}`}>{row.status}</span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {txPageCount > 1 && (
                  <div className="tx-pagination">
                    <button
                      className="tx-page-btn"
                      disabled={txPage === 0}
                      onClick={() => setTxPage(p => p - 1)}
                    >← Prev</button>
                    <span className="tx-page-info">Page {txPage + 1} of {txPageCount}</span>
                    <button
                      className="tx-page-btn"
                      disabled={txPage >= txPageCount - 1}
                      onClick={() => setTxPage(p => p + 1)}
                    >Next →</button>
                  </div>
                )}
              </>
            )}
          </section>

          {pipeline && (
            <section className="ops-section">
              <h2>Pipeline</h2>
              <div className="pipeline-cols">
                <div className="pipeline-block">
                  <div className="pipeline-block-title">By Status</div>
                  {Object.entries(pipeline.by_status ?? {}).map(([key, value]) => (
                    <div key={key} className="pipeline-row">
                      <span className="pipeline-key">{key}</span>
                      <span className="pipeline-val">{value}</span>
                    </div>
                  ))}
                </div>
                <div className="pipeline-block">
                  <div className="pipeline-block-title">By Band</div>
                  {Object.entries(pipeline.by_band ?? {}).map(([key, value]) => (
                    <div key={key} className={`pipeline-row pipeline-band--${key}`}>
                      <span className="pipeline-key">{key}</span>
                      <span className="pipeline-val">{value}</span>
                    </div>
                  ))}
                </div>
              </div>
            </section>
          )}

          {strategies && (
            <section className="ops-section">
              <h2>Module Health</h2>
              <div className="stat-grid">
                <StatCard
                  label="Expected Return (Active)"
                  value={`$${(strategies.total_expected_return ?? 0).toLocaleString()}`}
                />
                <StatCard
                  label="Actual Return (Active)"
                  value={`$${(strategies.total_actual_return ?? 0).toLocaleString()}`}
                  highlight={(strategies.total_actual_return ?? 0) > 0}
                />
                <StatCard
                  label="Candidates Available"
                  value={strategies.candidates_available ?? 0}
                  sub="pending activation"
                />
                <StatCard
                  label="Total Sources"
                  value={summary.total_opportunities}
                  sub={`${pipeline?.by_band?.elite ?? 0} elite · ${pipeline?.by_band?.high ?? 0} high`}
                />
              </div>
            </section>
          )}
        </main>
      )}
    </div>
  )
}
