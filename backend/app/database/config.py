import os
import sqlite3
from pathlib import Path
from dotenv import load_dotenv
from sqlmodel import SQLModel, Session, create_engine

BACKEND_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(BACKEND_ROOT.parent / "backend.env")
load_dotenv(BACKEND_ROOT / ".env")
load_dotenv(BACKEND_ROOT.parent / "config" / ".env")

_db_path_raw = os.getenv("HUNTER_DB_PATH", "./hunter.db")

# Ensure the database directory exists.
# On Render with a persistent disk, /data is mounted before the app starts.
# On plans without a disk (or on first boot before the disk is attached),
# we fall back to a local path so the app still starts.
_db_dir = os.path.dirname(os.path.abspath(_db_path_raw))
if _db_dir and _db_dir != "/" and not os.path.isdir(_db_dir):
    try:
        os.makedirs(_db_dir, exist_ok=True)
    except OSError:
        # Directory could not be created (read-only parent).
        # Fall back to a writable local path inside the backend directory.
        _db_path_raw = os.path.join(str(BACKEND_ROOT), "hunter.db")

_db_path = _db_path_raw
DATABASE_URL = f"sqlite:///{_db_path}"

engine = create_engine(DATABASE_URL, echo=False, connect_args={"check_same_thread": False})


def create_db_and_tables() -> None:
    # Import all table models so SQLModel registers them before create_all
    import app.models.income_source  # noqa: F401
    import app.models.budget         # noqa: F401
    import app.models.event          # noqa: F401
    import app.models.alert          # noqa: F401
    import app.models.action_packet  # noqa: F401
    import app.models.strategy       # noqa: F401
    import app.models.advisor        # noqa: F401
    import app.models.execution_outcome  # noqa: F401
    import app.models.provider_execution  # noqa: F401
    import app.models.decision           # noqa: F401
    import app.models.marketplace        # noqa: F401
    import app.models.task               # noqa: F401
    SQLModel.metadata.create_all(engine)
    _migrate_sqlite_tables()


def get_session():
    with Session(engine) as session:
        yield session


def _migrate_sqlite_tables() -> None:
    if not DATABASE_URL.startswith("sqlite:///"):
        return

    db_path = DATABASE_URL.replace("sqlite:///", "", 1)
    conn = sqlite3.connect(db_path)
    try:
        _ensure_columns(
            conn,
            "weeklybudget",
            {
                "starting_bankroll": "REAL DEFAULT 100.0",
                "current_bankroll": "REAL DEFAULT 100.0",
                "evaluation_start_date": "DATE",
                "evaluation_end_date": "DATE",
                "capital_match_eligible": "BOOLEAN DEFAULT 0",
                "capital_match_amount": "REAL DEFAULT 0.0",
                "manual_injection_total": "REAL DEFAULT 0.0",
            },
        )
        _ensure_columns(
            conn,
            "actionpacket",
            {
                "execution_state": "TEXT DEFAULT 'planned'",
                "execution_started_at": "TIMESTAMP",
                "execution_updated_at": "TIMESTAMP",
                "execution_completed_at": "TIMESTAMP",
                "execution_failed_at": "TIMESTAMP",
                "execution_canceled_at": "TIMESTAMP",
                "execution_notes": "TEXT",
            },
        )
        _ensure_columns(
            conn,
            "budgetallocation",
            {
                "started_at": "TIMESTAMP",
                "completed_at": "TIMESTAMP",
                "failed_at": "TIMESTAMP",
                "canceled_at": "TIMESTAMP",
                "updated_at": "TIMESTAMP",
            },
        )
        _ensure_columns(
            conn,
            "opportunitydecision",
            {
                "feedback_adjustment": "REAL DEFAULT 0.0",
                "approval_required": "BOOLEAN DEFAULT 0",
                "approval_reason": "TEXT",
                "execution_ready": "BOOLEAN DEFAULT 0",
                "blocked_by": "TEXT",
                "capital_recommendation": "REAL",
                "action_payload_json": "TEXT",
                "reviewed_at": "TIMESTAMP",
                "reviewer_note": "TEXT",
            },
        )
        _ensure_columns(
            conn,
            "budgetoutcome",
            {
                "success_reason": "TEXT",
                "failure_reason": "TEXT",
                "time_to_completion_hours": "REAL",
                "source_id": "TEXT",
                "strategy_id": "TEXT",
                "action_packet_id": "INTEGER",
                "lane": "TEXT",
                "category": "TEXT",
            },
        )
        _ensure_columns(
            conn,
            "incomesource",
            {
                "marketplace_lane": "TEXT",
                "marketplace_routing_label": "TEXT",
                "marketplace_provider": "TEXT",
                "marketplace_execution_state": "TEXT",
                "marketplace_blocked_reason": "TEXT",
            },
        )
        conn.execute(
            """
            UPDATE weeklybudget
            SET
                starting_bankroll = COALESCE(starting_bankroll, starting_budget, 100.0),
                current_bankroll = COALESCE(current_bankroll, starting_bankroll, starting_budget, 100.0),
                evaluation_start_date = COALESCE(evaluation_start_date, week_start_date),
                evaluation_end_date = COALESCE(evaluation_end_date, week_end_date)
            """
        )
        conn.commit()
    finally:
        conn.close()


def _ensure_columns(conn: sqlite3.Connection, table_name: str, columns: dict[str, str]) -> None:
    existing = {
        row[1]
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    for column_name, column_sql in columns.items():
        if column_name not in existing:
            conn.execute(
                f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}"
            )
