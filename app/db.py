"""SQLite storage: schema + idempotent seed. Path from DATABASE_URL, else data/autopay_voice.db."""
import hashlib
import hmac
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.models import CustomerSeed

repo_root = Path(__file__).resolve().parent.parent
db_file = repo_root / "data" / "autopay_voice.db"

try:
    from dotenv import load_dotenv
except ImportError:  # stdlib-only fallback: env must be exported manually
    load_dotenv = None
if load_dotenv is not None:
    load_dotenv(repo_root / ".env", override=False)

schema_sql = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS customers (
  customer_id          TEXT PRIMARY KEY,
  name                 TEXT NOT NULL,
  gender               TEXT,
  phone                TEXT NOT NULL,
  amount_due           REAL NOT NULL,
  due_date             TEXT,
  failure_reason       TEXT CHECK (failure_reason IN
                         ('insufficient_balance','mandate_expired','bank_decline','other')),
  payment_status       TEXT NOT NULL DEFAULT 'failed'
                         CHECK (payment_status IN
                         ('failed','link_sent','recovered','handoff','opted_out')),
  default_history      INTEGER NOT NULL DEFAULT 0,
  last_call_at         TEXT,
  last_message_at      TEXT,
  attempts_total       INTEGER NOT NULL DEFAULT 0,
  past_call_notes      TEXT,
  security_question    TEXT,
  security_answer_hash TEXT,
  security_salt        TEXT,
  do_not_call          INTEGER NOT NULL DEFAULT 0 CHECK (do_not_call IN (0,1))
);

CREATE TABLE IF NOT EXISTS calls (
  call_id        INTEGER PRIMARY KEY AUTOINCREMENT,
  customer_id    TEXT NOT NULL REFERENCES customers(customer_id),
  vapi_call_id   TEXT UNIQUE,
  mode           TEXT CHECK (mode IN ('web','phone','sim')),
  started_at     TEXT,
  ended_at       TEXT,
  tier           TEXT CHECK (tier IN ('short','standard','extended')),
  p_pay          REAL,
  score_reasons  TEXT,
  verified       INTEGER NOT NULL DEFAULT 0 CHECK (verified IN (0,1)),
  verify_attempts INTEGER NOT NULL DEFAULT 0,
  outcome        TEXT CHECK (outcome IN
                   ('link_sent','retry_scheduled','handoff','no_answer',
                    'wrong_person','refused','opted_out','failed')),
  retry_at       TEXT,
  transcript     TEXT,
  judge_json     TEXT
);

CREATE TABLE IF NOT EXISTS payment_links (
  token        TEXT PRIMARY KEY,
  customer_id  TEXT NOT NULL REFERENCES customers(customer_id),
  call_id      INTEGER REFERENCES calls(call_id),
  kind         TEXT CHECK (kind IN ('pay_now','update_mandate')),
  channel      TEXT CHECK (channel IN ('inapp','console','razorpay_notify','whatsapp','sms')),
  created_at   TEXT NOT NULL,
  expires_at   TEXT NOT NULL,
  used_at      TEXT
);

CREATE TABLE IF NOT EXISTS handoffs (
  handoff_id   INTEGER PRIMARY KEY AUTOINCREMENT,
  customer_id  TEXT NOT NULL REFERENCES customers(customer_id),
  call_id      INTEGER REFERENCES calls(call_id),
  reason       TEXT NOT NULL,
  status       TEXT NOT NULL DEFAULT 'open'
                 CHECK (status IN ('open','in_progress','done')),
  created_at   TEXT NOT NULL,
  resolved_at  TEXT,
  notes        TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  ts           TEXT NOT NULL,
  call_id      INTEGER REFERENCES calls(call_id),
  actor        TEXT,
  tool         TEXT NOT NULL,
  args_masked  TEXT,
  status       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_calls_customer ON calls(customer_id, started_at);
CREATE INDEX IF NOT EXISTS idx_links_customer ON payment_links(customer_id);
CREATE INDEX IF NOT EXISTS idx_handoffs_open  ON handoffs(status);
CREATE INDEX IF NOT EXISTS idx_audit_call     ON audit_log(call_id);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def db_path_from_url(url: str | None = None) -> Path | str:
    """Resolve the sqlite path. Relative paths anchor at repo root. Only sqlite:/// for now."""
    raw = (url or os.environ.get("DATABASE_URL", "")).strip()
    if not raw:
        return db_file
    if raw == "sqlite:///:memory:":
        return ":memory:"
    if raw.startswith("sqlite:///"):
        candidate = Path(raw.removeprefix("sqlite:///"))
        return candidate if candidate.is_absolute() else repo_root / candidate
    raise ValueError(f"unsupported DATABASE_URL (sqlite:/// only): {raw!r}")


def get_conn(db_path: Path | str | None = None) -> sqlite3.Connection:
    resolved = db_path_from_url() if db_path is None else db_path
    if resolved != ":memory:":
        resolved = Path(resolved)
        resolved.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(resolved))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(schema_sql)
    conn.commit()


def seed_from_json(json_path: Path | str, conn: sqlite3.Connection) -> int:
    """Idempotent seed: validates via pydantic, INSERT OR IGNORE. Returns new rows."""
    records = json.loads(Path(json_path).read_text(encoding="utf-8"))
    inserted = 0
    for raw in records:
        c = CustomerSeed(**raw)  # type-check first, fail fast on bad seed data
        cur = conn.execute(
            """INSERT OR IGNORE INTO customers
               (customer_id, name, gender, phone, amount_due, due_date,
                failure_reason, payment_status, default_history,
                last_call_at, last_message_at, attempts_total,
                past_call_notes, security_question,
                security_answer_hash, security_salt, do_not_call)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (c.customer_id, c.name, c.gender, c.phone, c.amount_due, c.due_date,
             c.failure_reason, c.payment_status, c.default_history,
             c.last_call_at, c.last_message_at, c.attempts_total,
             c.past_call_notes, c.security_question,
             c.security_answer_hash, c.security_salt, c.do_not_call),
        )
        inserted += cur.rowcount
    conn.commit()
    return inserted


def verify_security_answer(stored_hash: str, salt: str, candidate: str) -> bool:
    """Compare salted sha256. Never log or return the plaintext answer."""
    if not stored_hash or not salt or candidate is None:
        return False
    cand = hashlib.sha256((salt + candidate).encode()).hexdigest()
    return hmac.compare_digest(cand, stored_hash)


def count_calls_today(conn: sqlite3.Connection, customer_id: str) -> int:
    """Daily attempt count from calls rows (UTC date). Reliable daily cap source."""
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM calls "
        "WHERE customer_id = ? AND DATE(started_at) = DATE('now')",
        (customer_id,),
    ).fetchone()
    return int(row["n"])


def mask_phone(phone: str | None) -> str:
    if not phone or len(phone) < 4:
        return "****"
    return f"****-**{phone[-4:]}"


if __name__ == "__main__":
    target = db_path_from_url()
    conn = get_conn()
    init_db(conn)
    n = seed_from_json(repo_root / "data" / "customers.json", conn)
    total = conn.execute("SELECT COUNT(*) AS n FROM customers").fetchone()["n"]
    print(f"seeded {n} new customers, {total} total in {target}")
    conn.close()
