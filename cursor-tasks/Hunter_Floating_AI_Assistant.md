# Cursor Task: Hunter Floating AI Assistant Button

**Repo:** `emurp3/Hunter-AutoTrader`
**Files to create:** `frontend/src/components/HunterAssistant.tsx`, `backend/routers/assistant.py`
**Files to modify:** `frontend/src/App.tsx` (mount component), `backend/main.py` (register router)
**Design language:** Dark navy / black / gold (`#c9a84c`) / electric blue (`#00D4FF`) — match existing Hunter UI

---

## What to Build

A floating AI button that lives on every page of the Hunter frontend. When clicked, a chat panel slides up. The AI knows everything Hunter knows in real time — account balance, top opportunities, active signals, positions, forge, budget, performance — and can answer natural-language questions about any of it.

**Example question the user wants to ask:**
> "What do I need to do to execute number 1 of top opportunities?"

The AI should pull the ranked opportunity list, read the top entry, explain what it is, what action is required, and how to execute it — in plain language.

---

## 1. Backend: `backend/routers/assistant.py`

### New endpoint: `POST /assistant/chat`

Request body:
```json
{ "message": "What do I need to do to execute number 1 of top opportunities?" }
```

Response:
```json
{ "response": "...", "context_snapshot": { "account_cash": 156.95, ... } }
```

**How it works:**

1. **Aggregate Hunter context** — call internal service functions (not HTTP) to gather:
   - `GET /opportunities/ranked?limit=5` → top 5 ranked opportunities (title, score, estimated_profit, status, next_action, category)
   - `GET /execution/account` → account cash, buying_power, status
   - `GET /execution/positions` → open positions
   - `GET /signals/summary` → total signals, recent mirrors
   - `GET /forge/opportunities` → forge opps count
   - `GET /budget/capital-state` → available capital, committed
   - `GET /performance/summary` → success_rate, completed, failed
   - `GET /advisors/daily-opportunity/today` → today's advisor opp
   - `GET /strategies/active` → active strategy names + categories

2. **Build system prompt:**
```
You are Hunter's onboard AI advisor. You have real-time access to the following Hunter state:

ACCOUNT: Cash ${{cash}}, Buying Power ${{buying_power}}, Status: {{status}}
POSITIONS: {{positions_summary}}
CAPITAL STATE: Available ${{available_capital}}, Committed ${{committed}}
BUDGET: Remaining ${{remaining_budget}}

TOP OPPORTUNITIES (ranked):
{{#each top_opps}}
{{rank}}. [{{category}}] {{title}} | Score: {{score}} | Est. Profit: ${{estimated_profit}} | Status: {{status}} | Next Action: {{next_action}}
{{/each}}

TODAY'S ADVISOR OPP: {{advisor_opp_title}} via {{advisor_opp_ticker}} ({{advisor_opp_lane}})

ACTIVE STRATEGIES: {{strategy_names}}
SIGNALS: {{total_ingested}} ingested, {{mirror_count}} mirrored
FORGE OPPS: {{forge_count}} opportunities queued
PERFORMANCE: {{success_rate}}% success rate, {{completed}} completed, {{failed}} failed

Answer the user's question clearly and actionably. Be direct. If an action is required, specify the exact step. Reference specific opportunity names, tickers, and amounts from the data above when relevant.
```

3. **Call OpenAI GPT-4o:**
```python
import openai
client = openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])
response = client.chat.completions.create(
    model="gpt-4o",
    messages=[
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": message}
    ],
    max_tokens=600
)
```

4. Return `{ "response": response_text, "context_snapshot": { key metrics } }`

**Auth:** Require JWT auth (use same `get_current_user` dependency as other endpoints).

---

## 2. Frontend: `frontend/src/components/HunterAssistant.tsx`

A self-contained React component. Zero new dependencies — use existing fetch + React hooks only.

### Visual Design

**Floating button (collapsed state):**
- Fixed position: `bottom: 28px, right: 28px`
- Circle, 56px diameter
- Background: gold gradient (`#c9a84c` → `#a8893d`)
- Icon: ⚡ or a brain/chat SVG in black
- Box shadow: `0 4px 20px rgba(201, 168, 76, 0.5)`
- Tooltip on hover: `"Ask Hunter"`
- Pulse animation when new data is available (optional)

**Chat panel (expanded state):**
- Slides up from bottom-right, `width: 380px, height: 520px`
- Background: `#0a0e1a` (dark navy)
- Border: `1px solid rgba(201, 168, 76, 0.3)`
- Border radius: `16px`
- Box shadow: `0 8px 40px rgba(0,0,0,0.6)`
- Header: "⚡ Hunter AI" in gold, small `×` close button
- Scrollable message history area
- Input bar at bottom: dark input field + gold Send button

### Behavior

- **State:** `isOpen: boolean`, `messages: {role, content}[]`, `loading: boolean`, `input: string`
- On open: show welcome message — `"Hunter AI online. I have full visibility into your opportunities, account, and signals. Ask me anything."`
- User types + hits Enter or Send → POST to `/api/assistant/chat` with `{ message: input }`
- Show typing indicator (3 animated dots) while loading
- Display response in chat bubble (AI = gold border, user = right-aligned blue)
- Maintain scroll at bottom on new messages
- Input clears after send
- Error state: `"Hunter AI is temporarily offline. Check your connection."`

### Context badge (optional but recommended)
Small live stats strip inside the panel header:
- `${{account_cash}} cash` · `{{top_opp_count}} opps` — pulled from the context_snapshot in the response

---

## 3. Mount in App

In `frontend/src/App.tsx`, import and render `<HunterAssistant />` at the root level (outside all routes, so it appears on every page):

```tsx
import HunterAssistant from './components/HunterAssistant';

// Inside the return, after all route components:
<HunterAssistant />
```

---

## 4. Register Router in Backend

In `backend/main.py`:
```python
from routers import assistant
app.include_router(assistant.router, prefix="/assistant", tags=["assistant"])
```

---

## Do Not Change
- Existing route structure or auth logic
- Any existing components or pages
- Trading execution logic
- The `OPENAI_API_KEY` env var name

## Acceptance Criteria
- [ ] Floating gold ⚡ button visible on every Hunter page (Opportunities, Trading, Performance, Executive Summary)
- [ ] Click opens chat panel smoothly
- [ ] Typing "What do I need to do to execute number 1 of top opportunities?" returns a specific, data-grounded answer naming the actual opportunity
- [ ] Context snapshot includes live account balance, top 5 opps, and signals
- [ ] Panel closes with × button or clicking outside
- [ ] Works on mobile (button stays fixed, panel is scrollable)
- [ ] No console errors in production build
