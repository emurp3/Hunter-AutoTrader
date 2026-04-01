import { useEffect, useMemo, useState } from 'react'

const API = '/api'

const fallbackData = {
  summary: {
    weekly_quotas: {
      all_met: false,
      source_discovery: {
        sources_found_this_week: 6,
        required: 10,
        shortfall: 4,
        quota_met: false,
      },
      strategy_deployment: {
        active_count: 7,
        required: 10,
        shortfall: 3,
        quota_met: false,
      },
    },
    total_opportunities: 24,
    active_opportunities: 7,
    elite_opportunities: 2,
    high_opportunities: 5,
    unacknowledged_alerts: 3,
    ready_packets: 4,
    underperforming_strategies: 2,
  },
  alerts: [
    { id: 1, priority: 'high', title: 'Strategy quota below target', alert_type: 'quota' },
    { id: 2, priority: 'medium', title: 'AutoTrader source not live', alert_type: 'autotrader' },
    { id: 3, priority: 'medium', title: 'Budget approvals waiting', alert_type: 'budget' },
  ],
  strategies: {
    active_count: 7,
    activated_this_week: 3,
    retired_this_week: 1,
    replacement_strategies_required: 2,
    total_expected_return: 570,
    total_actual_return: 119,
    candidates_available: 6,
    strategies: [
      {
        strategy_id: 'auto-intake',
        strategy_name: 'AutoTrader opportunity intake',
        category: 'intake',
        expected_return: 0,
        actual_return: 0,
        evidence_of_activity: false,
      },
      {
        strategy_id: 'small-flips',
        strategy_name: 'Small-win cash flips',
        category: 'active',
        expected_return: 180,
        actual_return: 57,
        evidence_of_activity: true,
      },
      {
        strategy_id: 'digital-scouting',
        strategy_name: 'Digital product scouting',
        category: 'active',
        expected_return: 125,
        actual_return: 34,
        evidence_of_activity: true,
      },
    ],
  },
  budget: {
    starting_bankroll: 100,
    current_bankroll: 119.42,
    available_capital: 62,
    committed_capital: 38,
    realized_profit: 19.42,
    unrealized_exposure: 24,
    allocation_count: 4,
    evaluation_start_date: '2026-03-23',
    evaluation_end_date: '2026-04-22',
    capital_match_eligible: false,
    capital_match_amount: 0,
    month_end_review: {
      starting_bankroll: 100,
      ending_bankroll: 119.42,
      net_gain_loss: 19.42,
      growth_pct: 19.42,
      doubled_bankroll: false,
      capital_match_eligible: false,
      recommended_match_amount: 0,
      next_cycle_bankroll_if_matched: 119.42,
      evaluation_start_date: '2026-03-23',
      evaluation_end_date: '2026-04-22',
      days_remaining: 24,
    },
    budget: {
      starting_bankroll: 100,
      current_bankroll: 119.42,
      evaluation_start_date: '2026-03-23',
      evaluation_end_date: '2026-04-22',
      status: 'open',
    },
    allocations_by_source: [
      {
        source_id: 'AT-4821',
        allocation_name: 'High-margin vehicle opportunity with local buyer demand.',
        amount_allocated: 18,
        status: 'planned',
      },
      {
        source_id: 'DP-112',
        allocation_name: 'Digital product bundle with low setup cost and rapid distribution.',
        amount_allocated: 12,
        status: 'planned',
      },
    ],
  },
  allocations: [
    {
      id: 1,
      source_id: 'AT-4821',
      allocation_name: 'High-margin vehicle opportunity with local buyer demand.',
      amount_allocated: 18,
      status: 'planned',
      expected_return: 240,
    },
    {
      id: 2,
      source_id: 'DP-112',
      allocation_name: 'Digital product bundle with low setup cost and rapid distribution.',
      amount_allocated: 12,
      status: 'planned',
      expected_return: 125,
    },
  ],
  atStatus: {
    source_configured: true,
    source_reachable: false,
    last_scan_status: 'never_run',
    last_scan_at: null,
    last_error: 'AutoTrader offline / no live data. Export file is missing.',
    live_data_status: 'missing',
    live_data_message: 'AutoTrader offline / no live data. Export file is missing.',
    live_data_updated_at: null,
    live_data_record_count: 0,
    stale_after_hours: 24,
    using_fallback: false,
    fallback_reason: null,
    fallback_record_count: 7,
    current_data_mode: 'offline',
    last_scan_counts: {
      scanned: 0,
      inserted: 0,
      updated: 0,
      skipped: 0,
      errors: 0,
    },
    config: {
      source_type: 'file',
      file_path: 'C:\\Murph\\agents\\hunter\\backend\\data\\autotrader.json',
      seed_path: 'C:\\Murph\\agents\\hunter\\backend\\data\\seed_opportunities.json',
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
  packets: [
    {
      id: 'packet-01',
      status: 'ready',
      priority_band: 'elite',
      source_id: 'AT-4821',
      estimated_return: 240,
      next_actions: ['review', 'approve'],
    },
    {
      id: 'packet-02',
      status: 'draft',
      priority_band: 'high',
      source_id: 'DP-112',
      estimated_return: 125,
      next_actions: ['score'],
    },
  ],
  pipeline: {
    by_status: {
      ingested: 24,
      scored: 18,
      review_ready: 6,
      budget_candidates: 4,
      active: 7,
    },
    by_band: {
      elite: 2,
      high: 5,
      medium: 9,
      low: 8,
    },
    top_10: [
      {
        source_id: 'AT-4821',
        priority_band: 'elite',
        score: 96,
        description: 'High-margin vehicle opportunity with local buyer demand.',
        status: 'review_ready',
        estimated_profit: 240,
      },
      {
        source_id: 'DP-112',
        priority_band: 'high',
        score: 84,
        description: 'Digital product bundle with low setup cost and rapid distribution.',
        status: 'budget_candidate',
        estimated_profit: 125,
      },
    ],
  },
  events: {
    count: 3,
    events: [
      { id: 'evt-1', event_type: 'packet_ready', source_id: 'AT-4821', created_at: '2026-03-27T19:20:00Z' },
      { id: 'evt-2', event_type: 'quota_shortfall', source_id: 'weekly', created_at: '2026-03-27T18:02:00Z' },
      { id: 'evt-3', event_type: 'autotrader_blocked', source_id: 'autotrader', created_at: '2026-03-27T17:14:00Z' },
    ],
  },
  executionStatus: {
    active_executions: [
      {
        packet_id: 8,
        source_id: 'github:repo:josh-xt/agixt',
        opportunity_summary: 'AGiXT monetization and setup opportunity.',
        status: 'acknowledged',
        execution_state: 'in_progress',
        priority_band: 'high',
        estimated_return: 860,
        budget_recommendation: 7.39,
      },
    ],
    completed_executions: [],
    failed_executions: [],
    recent_outcomes: [],
    counts: {
      active: 1,
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

async function requestJson(path, options = {}) {
  const response = await fetch(`${API}${path}`, {
    headers: {
      Accept: 'application/json',
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
      ...options.headers,
    },
    ...options,
  })

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
    budgetReviewResult,
    allocationsResult,
    intakeResult,
    eventsResult,
    executionStatusResult,
    performanceSummaryResult,
  ] = await Promise.allSettled([
    requestJson('/operations/summary'),
    requestJson('/alerts/?active_only=true'),
    requestJson('/strategies/weekly'),
    requestJson('/autotrader/status'),
    requestJson('/packets/'),
    requestJson('/operations/pipeline'),
    requestJson('/budget/current'),
    requestJson('/budget/review'),
    requestJson('/budget/allocations'),
    requestJson('/autotrader/intake-summary'),
    requestJson('/operations/events?limit=8'),
    requestJson('/execution/status'),
    requestJson('/performance/summary'),
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
    budgetReview: budgetReviewResult.status === 'fulfilled' ? budgetReviewResult.value : null,
    allocations: allocationsResult.status === 'fulfilled' && Array.isArray(allocationsResult.value)
      ? allocationsResult.value
      : [],
    intakeSummary: intakeResult.status === 'fulfilled' ? intakeResult.value : null,
    events: eventsResult.status === 'fulfilled' ? eventsResult.value : null,
    executionStatus: executionStatusResult.status === 'fulfilled' ? executionStatusResult.value : null,
    performanceSummary:
      performanceSummaryResult.status === 'fulfilled' ? performanceSummaryResult.value : null,
  }
}

export default function OperationsPage({ onBack }) {
  const [summary, setSummary] = useState(fallbackData.summary)
  const [alerts, setAlerts] = useState(fallbackData.alerts)
  const [strategies, setStrategies] = useState(fallbackData.strategies)
  const [budget, setBudget] = useState(fallbackData.budget)
  const [budgetReview, setBudgetReview] = useState(fallbackData.budget.month_end_review)
  const [allocations, setAllocations] = useState(fallbackData.allocations)
  const [atStatus, setAtStatus] = useState(fallbackData.atStatus)
  const [intakeSummary, setIntakeSummary] = useState(fallbackData.intakeSummary)
  const [packets, setPackets] = useState(fallbackData.packets)
  const [pipeline, setPipeline] = useState(fallbackData.pipeline)
  const [events, setEvents] = useState(fallbackData.events)
  const [executionStatus, setExecutionStatus] = useState(fallbackData.executionStatus)
  const [performanceSummary, setPerformanceSummary] = useState(fallbackData.performanceSummary)
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
    setBudgetReview(data.budgetReview ?? data.budget?.month_end_review ?? null)
    setAllocations(data.allocations)
    setAtStatus(data.atStatus)
    setIntakeSummary(data.intakeSummary)
    setPackets(data.packets)
    setPipeline(data.pipeline)
    setEvents(data.events)
    setExecutionStatus(data.executionStatus ?? fallbackData.executionStatus)
    setPerformanceSummary(data.performanceSummary ?? fallbackData.performanceSummary)
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
      } catch (_) {
        if (!active) {
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
        setError('Hunter backend is unavailable, so the dashboard is showing local fallback data.')
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
  const fundedPackets = packets.filter(
    (packet) => packet.status === 'acknowledged' || packet.status === 'executed',
  )
  const activeExecutions = executionStatus?.active_executions ?? []
  const completedExecutions = executionStatus?.completed_executions ?? []
  const failedExecutions = executionStatus?.failed_executions ?? []
  const top10 = pipeline?.top_10 ?? []
  const recentEvents = events?.events ?? []
  const endpointStatus = usingFallback ? 'Fallback' : 'Live'
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
      return 'Backend unavailable. Dashboard is rendering with local fallback data only.'
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

  const budgetStatus = budget?.budget?.status ?? budget?.status ?? 'no_open_budget'
  const startingBankroll =
    budget?.starting_bankroll ?? budget?.budget?.starting_bankroll ?? budget?.budget?.starting_budget ?? 0
  const currentBankroll =
    budget?.current_bankroll ?? budget?.budget?.current_bankroll ?? startingBankroll
  const availableCapital = budget?.available_capital ?? budget?.available_budget ?? budget?.remaining_budget ?? 0
  const committedCapital =
    budget?.committed_capital ?? budget?.allocated_budget ?? budget?.total_allocated ?? 0
  const realizedProfit = budget?.realized_profit ?? budget?.realized_return ?? budget?.budget?.realized_return ?? 0
  const monthEndReview = budgetReview ?? budget?.month_end_review ?? null
  const originalBaseCapital = monthEndReview?.starting_bankroll ?? startingBankroll
  const doublingTarget =
    monthEndReview?.doubling_threshold ??
    (originalBaseCapital > 0 ? originalBaseCapital * 2 : budget?.flip_target ?? currentBankroll ?? 0)
  const doublingProgressPct =
    monthEndReview?.progress_to_doubling_threshold ??
    (doublingTarget > 0 ? Math.max(0, (currentBankroll / doublingTarget) * 100) : 0)
  const evaluationEndDate = budget?.evaluation_end_date ?? budget?.budget?.evaluation_end_date ?? null
  const capitalMatchAmount =
    budget?.capital_match_amount ?? monthEndReview?.recommended_match_amount ?? 0
  const capitalMatchEligible =
    budget?.capital_match_eligible ?? monthEndReview?.capital_match_eligible ?? false
  const fundedPacketCount = fundedOpportunities.length || fundedPackets.length

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
      const result = await requestJson(command.path, { method: 'POST' })
      setCommandState({
        type,
        status: 'success',
        message:
          type === 'intake'
            ? result.fallback_used
              ? `Intake completed using seed fallback. Live source status: ${result.live_data_status ?? 'offline'}.`
              : `AutoTrader intake completed with live data. ${result.records_loaded ?? result.scanned ?? 0} findings loaded.`
            : 'Weekly quota enforcement completed successfully.',
      })

      const refreshed = await loadOperationalData()
      applyOperationalData(refreshed)
      setUsingFallback(false)
      setError(null)
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
      <header className="ops-header">
        <button className="ops-back" onClick={onBack}>
          ← Hunter
        </button>
        <h1 className="ops-title">Operations Dashboard</h1>
        <div className="ops-header-meta">
          <span className={`ops-status-badge ops-status-badge--${usingFallback ? 'fallback' : 'live'}`}>
            {endpointStatus}
          </span>
          <span className="ops-version">v0.2.0</span>
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
                  {fallbackActive || autotraderOffline
                    ? 'AutoTrader is offline, so this run will use the seeded opportunity file instead of silent empty data.'
                    : 'Best live operational test once the AutoTrader bridge is healthy.'}
                </div>
                <button
                  className="ops-action-button"
                  onClick={() => runCommand('intake')}
                  disabled={commandState.status === 'running'}
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
                  disabled={commandState.status === 'running'}
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

          {usingFallback && (
            <div className="ops-no-data">
              Running in local fallback mode so the operations page still renders cleanly in Vite.
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
              <h2>Capital Deployment</h2>
              <span className={`budget-status-pill budget-status-pill--${budgetStatus}`}>
                {budgetStatus.toUpperCase()}
              </span>
            </div>
            {!budget ? (
              <div className="ops-no-data">No active bankroll cycle is available yet.</div>
            ) : (
              <div className="budget-execution-shell">
                <div className="budget-execution-hero">
                  <div className="budget-execution-copy">
                    <div className="ops-kicker">Live Capital State</div>
                    <h3>Hunter is compounding a live bankroll across the current 30-day evaluation window.</h3>
                    <p>
                      Live capital data is coming from <code>/budget/current</code> and{' '}
                      <code>/budget/allocations</code>. This block reflects current bankroll,
                      committed capital, available capital, realized profit, and per-opportunity deployment.
                    </p>
                  </div>
                  <div className="budget-execution-stats">
                    <div className="budget-execution-stat budget-execution-stat--available">
                      <span className="budget-execution-label">Available Capital</span>
                      <strong>{formatCurrency(availableCapital)}</strong>
                    </div>
                    <div className="budget-execution-stat budget-execution-stat--allocated">
                      <span className="budget-execution-label">Committed Capital</span>
                      <strong>{formatCurrency(committedCapital)}</strong>
                    </div>
                    <div className="budget-execution-stat budget-execution-stat--current">
                      <span className="budget-execution-label">Current Bankroll</span>
                      <strong>{formatCurrency(currentBankroll)}</strong>
                    </div>
                    <div className="budget-execution-stat budget-execution-stat--funded">
                      <span className="budget-execution-label">Funded Packets</span>
                      <strong>{fundedPacketCount}</strong>
                    </div>
                    <div className="budget-execution-stat budget-execution-stat--return">
                      <span className="budget-execution-label">Realized Profit</span>
                      <strong>{formatCurrency(realizedProfit)}</strong>
                    </div>
                  </div>
                </div>

                <div className="budget-row budget-row--primary">
                  <div className="budget-cell">
                    <div className="budget-value">{formatCurrency(startingBankroll)}</div>
                    <div className="budget-label">Starting Bankroll</div>
                  </div>
                  <div className="budget-cell">
                    <div className="budget-value">{formatCurrency(availableCapital)}</div>
                    <div className="budget-label">Available Capital</div>
                  </div>
                  <div className="budget-cell">
                    <div className="budget-value">{formatCurrency(committedCapital)}</div>
                    <div className="budget-label">Committed Capital</div>
                  </div>
                  <div className="budget-cell">
                    <div
                      className={`budget-value${
                        realizedProfit >= 0 ? ' budget-value--pos' : ' budget-value--neg'
                      }`}
                    >
                      {formatCurrency(realizedProfit)}
                    </div>
                    <div className="budget-label">Realized Profit</div>
                  </div>
                </div>

                <div className="budget-row budget-row--secondary">
                  <div className="budget-cell">
                    <div className="budget-value">{formatCurrency(originalBaseCapital)}</div>
                    <div className="budget-label">Original Base Capital</div>
                  </div>
                  <div className="budget-cell">
                    <div className="budget-value">{formatCurrency(doublingTarget)}</div>
                    <div className="budget-label">Doubling Threshold</div>
                  </div>
                  <div className="budget-cell">
                    <div className="budget-value">{`${doublingProgressPct.toFixed(0)}%`}</div>
                    <div className="budget-label">Progress to Threshold</div>
                  </div>
                  <div className="budget-cell">
                    <div className={`budget-value${capitalMatchEligible ? ' budget-value--pos' : ''}`}>
                      {capitalMatchEligible ? 'YES' : 'NO'}
                    </div>
                    <div className="budget-label">Capital Match Eligible</div>
                  </div>
                  <div className="budget-cell">
                    <div className={`budget-value${capitalMatchAmount > 0 ? ' budget-value--pos' : ''}`}>
                      {formatCurrency(capitalMatchAmount)}
                    </div>
                    <div className="budget-label">Recommended Match Amount</div>
                  </div>
                </div>

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

                <div className="budget-meta">
                  <span>Status {budgetStatus}</span>
                  <span className="budget-sep">·</span>
                  <span>Cycle start {budget?.evaluation_start_date ?? budget?.budget?.evaluation_start_date}</span>
                  <span className="budget-sep">·</span>
                  <span>Review end {evaluationEndDate ?? 'n/a'}</span>
                  <span className="budget-sep">·</span>
                  <span>Match eligible {budget?.capital_match_eligible ? 'yes' : 'no'}</span>
                </div>

                <div className="budget-allocation-panel">
                  <div className="budget-allocation-header">
                    <h3>Funded Opportunities</h3>
                    <span className="ops-count">{fundedOpportunities.length}</span>
                  </div>
                  {fundedOpportunities.length === 0 ? (
                    <div className="ops-no-data">No live allocations recorded yet.</div>
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
            )}
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

          {top10.length > 0 && (
            <section className="ops-section">
              <h2>
                Top Opportunities <span className="ops-count">{top10.length}</span>
              </h2>
              <div className="opp-grid">
                {top10.map((opportunity) => (
                  <div
                    key={opportunity.source_id}
                    className={`opp-card opp-card--${opportunity.priority_band ?? 'low'}`}
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
                      {opportunity.estimated_profit != null && (
                        <span className="opp-profit">${opportunity.estimated_profit?.toLocaleString()}/mo</span>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </section>
          )}

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


