# Hunter

Hunter is an autonomous revenue/trading operations engine. Two parts:

- **Backend** (`backend/`): FastAPI + SQLModel (SQLite) app served by `uvicorn`. Entry point `app.main:app`. All API routes are exposed under `/api/...` (an ASGI middleware strips the `/api` prefix).
- **Frontend** (`frontend/`): React + Vite single-page app. In dev it runs on its own Vite server and proxies `/api` to the backend on port 8000.

There is also an optional Playwright-based `worker` (`python -m app.worker.main`) used for the Facebook Marketplace lane; it is not needed to run or test the core product.

## Cursor Cloud specific instructions

The update script provisions a Python venv at `/workspace/.venv` (backend deps + `pytest`) and installs frontend deps. It does not start any services.

### Running the app (two dev servers)

Both servers must run together (frontend proxies `/api` to the backend):

- Backend (from `backend/`): `HUNTER_COOKIE_SECURE=false HUNTER_DB_PATH=./hunter.db ../.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload`
- Frontend (from `frontend/`): `npm run dev` (serves on port 3000, proxies `/api` → `http://localhost:8000`)

Non-obvious gotchas:

- **`HUNTER_COOKIE_SECURE=false` is required for local login.** The login cookie is `Secure` by default, so over plain HTTP (localhost) the browser silently drops it and you stay logged out. Set this env var when launching the backend in dev.
- **Default dev login:** username `admin`, password `hunter-admin-2024` (see `backend/app/auth/users.py`). A `guest`/`guest-demo` account also exists.
- **SQLite DB auto-creates on startup.** With `HUNTER_DB_PATH=./hunter.db` it writes `backend/hunter.db` (gitignored). No migration step is needed; tables + lightweight column migrations run at startup.
- **The "Trading" dashboard tab shows a degraded-broker banner / `503 Missing Alpaca credentials` by design.** Live trading needs real Alpaca `LIVE_API_KEY` / `LIVE_SECRET_KEY` (real money) which are intentionally absent in dev. Non-broker sections (Opportunities, Performance, etc.) still render normally. This is expected, not a setup failure.
- Other AI/API integrations (advisors, leads, SMS/email) are no-ops without their respective keys and do not block startup.

### Tests / lint / build

- Tests: from `backend/`, run `../.venv/bin/python -m pytest`. (`pytest` is not in `requirements.txt`; the update script installs it.)
- There is no Python linter config and no frontend lint script in this repo.
- Production build (not needed for dev): `build.sh` builds the frontend and copies `frontend/dist` → `backend/frontend_dist`, after which the backend serves the SPA itself. In dev, leave `frontend_dist` absent and use the Vite dev server.
