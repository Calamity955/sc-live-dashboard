"""SQLite storage: deliveries_current (snapshot) + events_log (append)."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator

from .models import Delivery, Event


SCHEMA = """
CREATE TABLE IF NOT EXISTS deliveries_current (
    cid TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS events_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    type TEXT NOT NULL,
    cid TEXT NOT NULL,
    targa TEXT,
    catena TEXT,
    cliente TEXT,
    old_stato TEXT,
    new_stato TEXT,
    puntualita TEXT,
    delta_minutes INTEGER,
    audio_silent INTEGER NOT NULL DEFAULT 0,
    payload TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_ts ON events_log(ts);
CREATE INDEX IF NOT EXISTS idx_events_cid ON events_log(cid);
"""


class Database:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
        finally:
            conn.close()

    def replace_current(self, deliveries: Iterable[Delivery]) -> None:
        with self._conn() as c:
            c.execute("BEGIN")
            c.execute("DELETE FROM deliveries_current")
            c.executemany(
                "INSERT INTO deliveries_current(cid, payload) VALUES (?, ?)",
                [(d.cid, d.model_dump_json()) for d in deliveries],
            )
            c.execute("COMMIT")

    def load_current(self) -> list[Delivery]:
        with self._conn() as c:
            rows = c.execute("SELECT payload FROM deliveries_current").fetchall()
        return [Delivery.model_validate_json(r["payload"]) for r in rows]

    def get_delivery(self, cid: str) -> Delivery | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT payload FROM deliveries_current WHERE cid = ?", (cid,)
            ).fetchone()
        return Delivery.model_validate_json(row["payload"]) if row else None

    def add_events(self, events: Iterable[Event]) -> list[Event]:
        out: list[Event] = []
        with self._conn() as c:
            for e in events:
                cur = c.execute(
                    """INSERT INTO events_log
                       (ts, type, cid, targa, catena, cliente, old_stato, new_stato,
                        puntualita, delta_minutes, audio_silent, payload)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        e.ts.isoformat(),
                        e.type,
                        e.cid,
                        e.targa,
                        e.catena,
                        e.cliente,
                        e.old_stato,
                        e.new_stato,
                        e.puntualita,
                        e.delta_minutes,
                        1 if e.audio_silent else 0,
                        e.model_dump_json(),
                    ),
                )
                e.id = cur.lastrowid
                out.append(e)
        return out

    def events_since(self, since_id: int = 0, limit: int = 200) -> list[Event]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, payload FROM events_log WHERE id > ? ORDER BY id ASC LIMIT ?",
                (since_id, limit),
            ).fetchall()
        out = []
        for r in rows:
            ev = Event.model_validate_json(r["payload"])
            ev.id = r["id"]
            out.append(ev)
        return out

    def recent_events(self, limit: int = 20) -> list[Event]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, payload FROM events_log ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        out = []
        for r in rows:
            ev = Event.model_validate_json(r["payload"])
            ev.id = r["id"]
            out.append(ev)
        return list(reversed(out))
