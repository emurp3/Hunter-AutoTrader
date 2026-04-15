# Hunter-AutoTrader — INTRADAY_RECYCLE Implementation Report

**Status:** 34/34 tests passing · app starts clean · all behaviors verified  
**Date:** 2026-04-13

---

## 1. Root Causes Fixed

| Problem | Root Cause | Fix |
|---|---|---|
| Buys fail with code 40310000 / buying_power ~$2.17 | No exit logic existed; Hunter kept buying until BP exhausted | `recycle_engine.run_recycle_cycle()` enforces exits before any entry attempt |
| Available Capital shows $100 seed when positions exist | Dashboard read from internal `WeeklyBudget` ledger, which never recorded Alpaca-opened positions | `/budget/capital-state` now calls `get_broker_reconciled_capital_state()` which fetches live Alpaca account |
| Committed Capital / Funded Packets show $0 / 0 | Same split-brain: internal ledger had no record of broker positions | Broker truth: `committed_capital = sum(position.market_value)`, `funded_packets = open_positions_count` |

---

## 2. Files Changed

### New files
| File | Purpose |
|---|---|
| `backend/app/services/broker_reconciliation.py` | Authoritative capital-state from Alpaca; stale order cancellation; symbol-held/order-pending guards |
| `backend/app/services/recycle_engine.py` | INTRADAY_RECYCLE strategy: exit evaluation, entry evaluation, replacement logic, full cycle orchestration |
| `backend/tests/__init__.py` | Makes `tests/` a package |
| `backend/tests/test_broker_reconciliation.py` | 13 deterministic unit tests for broker reconciliation |
| `backend/tests/test_recycle_engine.py` | 21 deterministic unit tests for recycle engine + dashboard capital-state |

### Modified files
| File | What changed |
|---|---|
| `backend/app/config.py` | +64 lines: all INTRADAY_RECYCLE strategy constants (see §3) |
| `backend/app/services/budget.py` | +`get_broker_reconciled_capital_state()` — broker-reconciled capital view for all display |
| `backend/app/routers/budget.py` | +`GET /budget/capital-state` endpoint; `/budget/current` now calls `get_broker_reconciled_capital_state()` |
| `backend/app/services/scheduler.py` | +`recycle_cycle_task()` async task; +`build_weekly_report_now()` |
| `backend/app/main.py` | Registers `recycle_cycle_task` on APScheduler when `STRATEGY_MODE=RECYCLE` and `ALPACA_ENABLED=True` |
| `frontend/src/pages/OperationsPage.jsx` | Fetches `/budget/capital-state`; capital cards use broker truth; unrealized P/L card; mismatch warning banner; strategy mode chips; recycle observability strip |

---

## 3. Strategy Defaults

All overridable via environment variable.

| Constant | Default | Meaning |
|---|---|---|
| `STRATEGY_MODE` | `RECYCLE` | Activates sell-first cycle |
| `LIVE_EXECUTION_STRATEGY` | `INTRADAY_RECYCLE` | Dashboard label |
| `MAX_OPEN_POSITIONS` | `3` | Simultaneous position cap |
| `MAX_POSITION_DOLLARS` | `30.0` | Hard notional cap per position |
| `MAX_POSITION_PCT_OF_BANKROLL` | `0.30` | 30% of current bankroll max per position |
| `MIN_REQUIRED_BUYING_POWER` | `5.0` | Minimum BP before any buy is attempted |
| `CAPITAL_RESERVE_BUFFER` | `2.0` | Dollars always withheld from BP calculations |
| `TARGET_PROFIT_PCT` | `0.02` | 2% unrealized gain → profit-target exit |
| `STOP_LOSS_PCT` | `-0.015` | −1.5% unrealized loss → stop-loss exit |
| `MAX_HOLD_MINUTES` | `240` | 4-hour max hold → forced exit |
| `STALE_ORDER_TIMEOUT_SECONDS` | `120` | Unfilled buy orders older than this are cancelled |
| `RECYCLE_CYCLE_INTERVAL_SECONDS` | `60` | How often the recycle loop fires |
| `REPLACEMENT_RANK_THRESHOLD` | `0.15` | Candidate must score 15% better to trigger replacement |
| `FORCE_SELL_END_OF_DAY` | `True` | Flatten all positions before close |
| `ALLOW_OVERNIGHT_HOLD` | `False` | No overnight positions |
| `RECYCLE_EOD_FLATTEN_MINUTES_BEFORE` | `15` | Begin EOD exits 15 min before 16:00 ET |

---

## 4. Capital-State Formulas

These are the authoritative values returned by `GET /budget/capital-state` in live mode:

```
available_capital   = broker.buying_power - CAPITAL_RESERVE_BUFFER
committed_capital   = sum(position.market_value for position in broker.positions)
current_bankroll    = broker.portfolio_value  (cash + all position market values)
funded_packets      = broker.open_positions_count
effective_buying_power = available_capital - reserved_by_open_orders
unrealized_pl       = sum(position.unrealized_pl for position in broker.positions)
```

In sandbox mode or when broker sync fails, falls back to internal `WeeklyBudget` ledger values.

Mismatch is flagged when `abs(internal_available - broker_available) > $1.00`.

---

## 5. Where Logic Lives

### Entry logic
`recycle_engine.evaluate_entries(broker_state, candidates) → list[EntryDecision]`

Guards (all must pass):
1. `symbol` not already held (`broker_reconciliation.is_symbol_held`)
2. No pending order for symbol (`broker_reconciliation.is_symbol_order_pending`)
3. `open_positions_count < MAX_OPEN_POSITIONS`
4. `effective_buying_power >= MIN_REQUIRED_BUYING_POWER`
5. Notional capped at `min(candidate_notional, MAX_POSITION_DOLLARS, MAX_POSITION_PCT_OF_BANKROLL × bankroll)`
6. `effective_buying_power >= notional + MIN_REQUIRED_BUYING_POWER` (post-trade floor)

### Exit logic
`recycle_engine.evaluate_exits(broker_state) → list[ExitDecision]`

Triggers (first match wins):
1. `unrealized_pl_pct >= TARGET_PROFIT_PCT` → `profit_target`
2. `unrealized_pl_pct <= STOP_LOSS_PCT` → `stop_loss`
3. `hold_minutes >= MAX_HOLD_MINUTES` → `max_hold_time`
4. Near market close and `FORCE_SELL_END_OF_DAY` → `end_of_day`
5. After-hours and `not ALLOW_OVERNIGHT_HOLD` → `overnight_prevention`

### Replacement logic
`recycle_engine.evaluate_replacements(broker_state, candidates) → list[ExitDecision]`

Triggers only when all slots are full (`open_positions_count >= MAX_OPEN_POSITIONS`) and a candidate's confidence exceeds the weakest current position's score by `REPLACEMENT_RANK_THRESHOLD`.

### Broker reconciliation
`broker_reconciliation.get_broker_capital_state(...) → BrokerCapitalState`

Calls `adapter.get_balance()` + `adapter.get_positions()` + `adapter.get_orders()`. Computes all capital fields. Detects mismatch vs internal ledger. Returns `sync_success=False` with `sync_error` on any exception — caller falls back to internal ledger.

### Stale order cancellation
`broker_reconciliation.cancel_stale_buy_orders() → list[str]`

Cancels open buy orders with `submitted_at` older than `STALE_ORDER_TIMEOUT_SECONDS`. Skips sell orders. Returns list of cancelled `order_id`s.

---

## 6. Recycle Cycle Sequence

Runs every `RECYCLE_CYCLE_INTERVAL_SECONDS` seconds via APScheduler (`max_instances=1`):

```
1. cancel_stale_buy_orders()          ← free slots blocked by hanging orders
2. get_broker_capital_state()         ← sync #1: authoritative snapshot
3. evaluate_exits() → execute_exits() ← sell first
4. sleep(2)                           ← let fills settle
5. get_broker_capital_state()         ← sync #2: post-exit capital snapshot
6. evaluate_replacements() → execute_exits()  ← swap weaker positions
7. evaluate_entries() → execute_entries()     ← buy only with freed capital
8. get_broker_capital_state()         ← sync #3: final state for dashboard
```

Only runs when `STRATEGY_MODE == "RECYCLE"` and `ALPACA_ENABLED == True`.

---

## 7. Verification Endpoints

| Endpoint | Returns |
|---|---|
| `GET /budget/capital-state` | Broker-reconciled available/committed/bankroll + positions + mismatch flag |
| `GET /budget/current` | Same broker-reconciled values + full allocation history |

Both call `get_broker_reconciled_capital_state(session)` and never return seed bankroll as available when broker has open positions.

---

## 8. Test Coverage

```
backend/tests/test_broker_reconciliation.py  — 13 tests
  TestGetBrokerCapitalState (7): no positions, open positions, reserve buffer,
    mismatch detection, no false mismatch, graceful failure, effective BP deduction
  TestCancelStaleBuyOrders (4): cancels old, skips fresh, skips sells, disabled mode
  TestIsSymbolHeld (2): case-insensitive match, not held

backend/tests/test_recycle_engine.py  — 21 tests
  TestEvaluateExits (6): profit_target, stop_loss, max_hold_time, healthy=no exit,
    failed sync=no exits, overnight_prevention
  TestEvaluateEntries (6): approves when BP available, rejects held, rejects at max
    positions, rejects insufficient BP, caps at MAX_POSITION_DOLLARS, orders by confidence
  TestEvaluateReplacements (3): triggers when full+better, no trigger when slots open,
    no trigger below threshold
  TestRecycleCycle (3): skipped when ACCUMULATE, exits before buying, no entries when BP low
  TestDashboardCapitalState (3): broker values win over internal, no entries while tied up,
    entries allowed after capital freed

Total: 34/34 passed
```
