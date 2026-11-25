from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from app.config import DB_PATH as CONFIG_DB_PATH

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "scrapweigh.db"
# Keep a mutable module global so tests can monkeypatch app.db.DB_PATH.
DB_PATH = CONFIG_DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS sites (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT '营业中' CHECK(status IN ('营业中','停收','已注销')),
  address TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS scales (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  site_id INTEGER NOT NULL,
  code TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT '空闲' CHECK(status IN ('空闲','占用','故障')),
  note TEXT NOT NULL DEFAULT '',
  pinned_ticket_id INTEGER,
  pinned_at TEXT,
  FOREIGN KEY(site_id) REFERENCES sites(id),
  UNIQUE(site_id, code)
);
CREATE TABLE IF NOT EXISTS grades (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  category TEXT NOT NULL,
  grade_name TEXT NOT NULL,
  unit_price REAL NOT NULL,
  UNIQUE(category, grade_name)
);
CREATE TABLE IF NOT EXISTS shifts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  business_day TEXT NOT NULL,
  name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT '未落印' CHECK(status IN ('未落印','已落印')),
  stamped_at TEXT,
  UNIQUE(business_day, name)
);
CREATE TABLE IF NOT EXISTS tickets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  serial TEXT NOT NULL UNIQUE,
  site_id INTEGER NOT NULL,
  scale_id INTEGER NOT NULL,
  shift_id INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT '待定级' CHECK(status IN ('待定级','待结价','已结价','已销')),
  gross_kg REAL NOT NULL,
  tare_kg REAL NOT NULL,
  deduct_kg REAL NOT NULL DEFAULT 0,
  net_kg REAL NOT NULL,
  grade_id INTEGER,
  locked_price REAL,
  amount REAL,
  note TEXT NOT NULL DEFAULT '',
  chain_no TEXT NOT NULL DEFAULT '',
  receipt_no TEXT NOT NULL DEFAULT '',
  seat_card_id INTEGER,
  created_at TEXT NOT NULL,
  settled_at TEXT,
  voided_at TEXT,
  FOREIGN KEY(site_id) REFERENCES sites(id),
  FOREIGN KEY(scale_id) REFERENCES scales(id),
  FOREIGN KEY(shift_id) REFERENCES shifts(id),
  FOREIGN KEY(grade_id) REFERENCES grades(id)
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  ref_id INTEGER,
  action TEXT NOT NULL,
  created_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS seat_cards (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  card_no TEXT NOT NULL UNIQUE,
  verify_code TEXT NOT NULL UNIQUE,
  idempotency_key TEXT NOT NULL DEFAULT '',
  site_id INTEGER NOT NULL,
  scale_id INTEGER NOT NULL,
  shift_id INTEGER,
  kind TEXT NOT NULL DEFAULT '排队席位',
  queue_no INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL CHECK(status IN ('候席','已落条','已补落','已超时作废','已取消','已拒绝')),
  request_json TEXT NOT NULL DEFAULT '{}',
  ticket_id INTEGER,
  ttl_seconds INTEGER NOT NULL DEFAULT 1800,
  issued_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  activated_at TEXT,
  finished_at TEXT,
  outcome TEXT NOT NULL DEFAULT '',
  fail_reason TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  FOREIGN KEY(site_id) REFERENCES sites(id),
  FOREIGN KEY(scale_id) REFERENCES scales(id),
  FOREIGN KEY(shift_id) REFERENCES shifts(id),
  FOREIGN KEY(ticket_id) REFERENCES tickets(id)
);
CREATE TABLE IF NOT EXISTS seat_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  card_id INTEGER NOT NULL,
  scale_id INTEGER NOT NULL,
  seq INTEGER NOT NULL,
  event_type TEXT NOT NULL,
  from_status TEXT NOT NULL DEFAULT '',
  to_status TEXT NOT NULL DEFAULT '',
  ticket_id INTEGER,
  note TEXT NOT NULL DEFAULT '',
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  UNIQUE(card_id, seq),
  FOREIGN KEY(card_id) REFERENCES seat_cards(id),
  FOREIGN KEY(scale_id) REFERENCES scales(id),
  FOREIGN KEY(ticket_id) REFERENCES tickets(id)
);
CREATE TABLE IF NOT EXISTS occupancy_segments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  segment_no TEXT NOT NULL UNIQUE,
  site_id INTEGER NOT NULL,
  scale_id INTEGER NOT NULL,
  ticket_id INTEGER,
  seat_card_id INTEGER,
  status TEXT NOT NULL CHECK(status IN ('占用中','已释放')),
  source TEXT NOT NULL DEFAULT '正式磅条',
  started_at TEXT NOT NULL,
  ended_at TEXT,
  end_reason TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(site_id) REFERENCES sites(id),
  FOREIGN KEY(scale_id) REFERENCES scales(id),
  FOREIGN KEY(ticket_id) REFERENCES tickets(id),
  FOREIGN KEY(seat_card_id) REFERENCES seat_cards(id)
);
CREATE TABLE IF NOT EXISTS weigh_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_no TEXT NOT NULL UNIQUE,
  ticket_id INTEGER,
  site_id INTEGER NOT NULL,
  scale_id INTEGER NOT NULL,
  event_type TEXT NOT NULL,
  weight_kind TEXT NOT NULL DEFAULT '',
  weight_kg REAL NOT NULL,
  chain_no TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT '过磅入口',
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  FOREIGN KEY(ticket_id) REFERENCES tickets(id),
  FOREIGN KEY(site_id) REFERENCES sites(id),
  FOREIGN KEY(scale_id) REFERENCES scales(id)
);
"""

INDEX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS uq_open_ticket_per_scale
ON tickets(scale_id)
WHERE status IN ('待定级', '待结价');

CREATE UNIQUE INDEX IF NOT EXISTS uq_active_occupancy_per_scale
ON occupancy_segments(scale_id)
WHERE status = '占用中';

CREATE UNIQUE INDEX IF NOT EXISTS uq_seat_idempotency_key
ON seat_cards(idempotency_key)
WHERE idempotency_key IS NOT NULL AND idempotency_key <> '';

CREATE INDEX IF NOT EXISTS idx_seat_cards_scale_status
ON seat_cards(scale_id, status, id);

CREATE INDEX IF NOT EXISTS idx_seat_events_card_seq
ON seat_events(card_id, seq);

CREATE INDEX IF NOT EXISTS idx_weigh_events_ticket
ON weigh_events(ticket_id, id);

CREATE INDEX IF NOT EXISTS idx_occupancy_scale_status
ON occupancy_segments(scale_id, status);
"""

TICKET_STATUSES = ("待定级", "待结价", "已结价", "已销")
SCALE_STATUSES = ("空闲", "占用", "故障")
SITE_STATUSES = ("营业中", "停收", "已注销")
SHIFT_STATUSES = ("未落印", "已落印")
OPEN_TICKET_STATUSES = ("待定级", "待结价")


def current_db_path() -> Path:
    raw = os.environ.get("SCRAPWEIGH_DB")
    if raw:
        return Path(raw)
    # Read the module global at call time; tests may monkeypatch app.db.DB_PATH.
    return Path(DB_PATH)


def connect() -> sqlite3.Connection:
    db_path = current_db_path()
    if str(db_path) != ":memory:":
        db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


@contextmanager
def db():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after the initial snapshot without dropping data."""
    migrations = {
        "scales": (
            ("pinned_ticket_id", "INTEGER"),
            ("pinned_at", "TEXT"),
        ),
        "tickets": (
            ("chain_no", "TEXT NOT NULL DEFAULT ''"),
            ("receipt_no", "TEXT NOT NULL DEFAULT ''"),
            ("seat_card_id", "INTEGER"),
        ),
        "seat_cards": (
            ("idempotency_key", "TEXT NOT NULL DEFAULT ''"),
        ),
    }
    for table, columns in migrations.items():
        existing = _existing_columns(conn, table)
        for name, ddl in columns:
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")

    conn.execute("UPDATE tickets SET receipt_no=serial WHERE receipt_no IS NULL OR receipt_no=''")

    # Repair databases created while the old code could leave multiple open
    # formal tickets on one scale. Keep the earliest ticket and close the rest
    # so the partial unique index can be installed.
    duplicate_scales = conn.execute(
        """
        SELECT scale_id
        FROM tickets
        WHERE status IN ('待定级', '待结价')
        GROUP BY scale_id
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    for dup in duplicate_scales:
        open_ids = [
            row["id"]
            for row in conn.execute(
                """
                SELECT id FROM tickets
                WHERE scale_id=? AND status IN ('待定级', '待结价')
                ORDER BY id ASC
                """,
                (dup["scale_id"],),
            ).fetchall()
        ]
        for extra_id in open_ids[1:]:
            conn.execute(
                """
                UPDATE tickets
                SET status='已销',
                    voided_at=COALESCE(voided_at, ?),
                    note=CASE WHEN note LIKE '[迁移核销重复未结票]%' THEN note
                              ELSE '[迁移核销重复未结票] ' || COALESCE(note, '') END
                WHERE id=?
                """,
                (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), extra_id),
            )

    # Repair projections for databases created before occupancy segments existed.
    open_tickets = conn.execute(
        """
        SELECT id, site_id, scale_id, created_at
        FROM tickets
        WHERE status IN ('待定级', '待结价')
        ORDER BY scale_id, id
        """
    ).fetchall()
    for ticket in open_tickets:
        conn.execute(
            """
            UPDATE scales
            SET status='占用', pinned_ticket_id=?, pinned_at=COALESCE(pinned_at, ?)
            WHERE id=?
            """,
            (ticket["id"], ticket["created_at"], ticket["scale_id"]),
        )
        has_segment = conn.execute(
            "SELECT id FROM occupancy_segments WHERE scale_id=? AND status='占用中'",
            (ticket["scale_id"],),
        ).fetchone()
        if not has_segment:
            segment_no = f"ZY-LEGACY-{ticket['id']}"
            conn.execute(
                """
                INSERT INTO occupancy_segments(
                  segment_no, site_id, scale_id, ticket_id, status, source, started_at
                ) VALUES (?, ?, ?, ?, '占用中', '正式磅条迁移', ?)
                """,
                (
                    segment_no,
                    ticket["site_id"],
                    ticket["scale_id"],
                    ticket["id"],
                    ticket["created_at"],
                ),
            )


def init_db() -> None:
    with db() as conn:
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError:
            # Some restricted filesystems do not support WAL; rollback journal still works.
            pass
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.executescript(INDEX_SQL)


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def rows_to_list(rows) -> list[dict]:
    return [dict(r) for r in rows]
