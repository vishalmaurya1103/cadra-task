"""Small SQLite store for runs, decisions and the ledger."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config


class Store:
    def __init__(self, path: Optional[Path] = None) -> None:
        config.BUILD_DIR.mkdir(parents=True, exist_ok=True)
        self.path = Path(path) if path else config.BUILD_DIR / "state.sqlite"
        self._lock = threading.RLock()
        self._init()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def _init(self) -> None:
        with self._lock, self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    ticket_id TEXT NOT NULL,
                    fingerprint TEXT,
                    duplicate_of TEXT,
                    run_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_runs_fingerprint ON runs(fingerprint);
                CREATE TABLE IF NOT EXISTS ledger (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    ticket_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    amount_usd REAL,
                    status TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    order_id TEXT,
                    lines_json TEXT,
                    UNIQUE(run_id, action)
                );
                CREATE TABLE IF NOT EXISTS decisions (
                    run_id TEXT PRIMARY KEY,
                    decision TEXT NOT NULL,
                    approver TEXT NOT NULL,
                    decided_at TEXT NOT NULL
                );
                """
            )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(ledger)").fetchall()
            }
            if "order_id" not in columns:
                connection.execute("ALTER TABLE ledger ADD COLUMN order_id TEXT")
            if "lines_json" not in columns:
                connection.execute("ALTER TABLE ledger ADD COLUMN lines_json TEXT")

    def find_by_fingerprint(self, fingerprint: str) -> Optional[Dict[str, Any]]:
        if not fingerprint:
            return None
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT run_json FROM runs WHERE fingerprint = ? ORDER BY created_at LIMIT 1",
                (fingerprint,),
            ).fetchone()
        return json.loads(row["run_json"]) if row else None

    def save_run(self, run: Dict[str, Any], fingerprint: str, created_at: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO runs(run_id, ticket_id, fingerprint, duplicate_of, run_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    run["run_id"],
                    run["ticket_id"],
                    fingerprint,
                    run.get("duplicate_of"),
                    json.dumps(run, default=str),
                    created_at,
                ),
            )

    def update_run(self, run: Dict[str, Any]) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE runs SET run_json = ?, duplicate_of = ? WHERE run_id = ?",
                (json.dumps(run, default=str), run.get("duplicate_of"), run["run_id"]),
            )

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT run_json FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return json.loads(row["run_json"]) if row else None

    def add_ledger(
        self,
        run_id: str,
        ticket_id: str,
        action: str,
        amount_usd: Optional[float],
        status: str,
        recorded_at: str,
        order_id: Optional[str] = None,
        lines: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO ledger("
                "run_id, ticket_id, action, amount_usd, status, recorded_at, order_id, lines_json"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    ticket_id,
                    action,
                    amount_usd,
                    status,
                    recorded_at,
                    order_id,
                    json.dumps(lines or [], default=str),
                ),
            )

    def active_refund_lines(self, order_id: str) -> Dict[str, str]:
        """Map order line_no -> run_id for PROPOSED/APPROVED REFUND ledger entries."""
        if not order_id:
            return {}
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT run_id, order_id, lines_json FROM ledger "
                "WHERE action = 'REFUND' AND status IN ('PROPOSED', 'APPROVED') "
                "ORDER BY id"
            ).fetchall()
        active: Dict[str, str] = {}
        for row in rows:
            try:
                lines = json.loads(row["lines_json"] or "[]")
            except (TypeError, ValueError):
                continue
            for line in lines:
                line_order = line.get("order_id") or row["order_id"]
                if line_order != order_id:
                    continue
                line_no = str(line.get("line_no"))
                if line_no and line_no not in active:
                    active[line_no] = row["run_id"]
        return active

    def set_ledger_status(self, run_id: str, status: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE ledger SET status = ? WHERE run_id = ? AND status = 'PROPOSED'",
                (status, run_id),
            )

    def get_ledger(self) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT run_id, ticket_id, action, amount_usd, status, recorded_at "
                "FROM ledger ORDER BY id"
            ).fetchall()
        return [dict(row) for row in rows]

    def record_decision(
        self, run_id: str, decision: str, approver: str, decided_at: str
    ) -> bool:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO decisions(run_id, decision, approver, decided_at) "
                "VALUES (?, ?, ?, ?)",
                (run_id, decision, approver, decided_at),
            )
            return cursor.rowcount > 0

    def get_decision(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT decision, approver, decided_at FROM decisions WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        return dict(row) if row else None


_STORE: Optional[Store] = None


def get_store() -> Store:
    global _STORE
    if _STORE is None:
        _STORE = Store()
    return _STORE
