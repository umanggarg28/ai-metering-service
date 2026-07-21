"""Storage layer.

`Repository` is the interface the rest of the app depends on; `SqliteRepository`
is the concrete implementation. Keeping this behind a Protocol means we could swap
SQLite for Postgres without touching the metering logic.

Concurrency note: the reservation is performed as a single atomic conditional
UPDATE. SQLite serializes writers, so two near-simultaneous requests cannot both
reserve the same remaining balance -- the database, not a Python lock, is the
source of truth. This is why we chose a real store over pure in-memory state.
"""

from __future__ import annotations

import sqlite3
import uuid
from decimal import Decimal
from typing import Protocol

from app.models import Reservation, UsageRecord, UserConfig


class QuotaSummary:
    def __init__(self, quota: int, used: int, reserved: int):
        self.quota = quota
        self.used = used
        self.reserved = reserved

    @property
    def remaining(self) -> int:
        return self.quota - self.used - self.reserved


class Repository(Protocol):
    def upsert_config(self, config: UserConfig) -> None: ...
    def get_config(self, user_id: str) -> UserConfig | None: ...
    def try_reserve(self, user_id: str, estimated_credits: int, multiplier: Decimal) -> Reservation | None: ...
    def finalize(self, reservation: Reservation, credits_charged: int, record: UsageRecord) -> None: ...
    def get_summary(self, user_id: str) -> QuotaSummary | None: ...
    def list_records(self, user_id: str) -> list[UsageRecord]: ...


_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id           TEXT PRIMARY KEY,
    quota_credits     INTEGER NOT NULL,
    credit_multiplier TEXT    NOT NULL,
    used_credits      INTEGER NOT NULL DEFAULT 0,
    reserved_credits  INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS usage_records (
    id                TEXT PRIMARY KEY,
    user_id           TEXT NOT NULL,
    status            TEXT NOT NULL,
    prompt_tokens     INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    multiplier        TEXT NOT NULL,
    credits_charged   INTEGER NOT NULL,
    created_at        TEXT NOT NULL
);
"""


class SqliteRepository:
    def __init__(self, db_path: str):
        self._db_path = db_path
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        # A short-lived connection per operation is thread-safe (each thread its
        # own connection); SQLite's file lock serializes writers. busy_timeout
        # avoids spurious "database is locked" under concurrent access.
        conn = sqlite3.connect(self._db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def upsert_config(self, config: UserConfig) -> None:
        # NOTE: updating config must NOT reset used/reserved counters.
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO users (user_id, quota_credits, credit_multiplier)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    quota_credits = excluded.quota_credits,
                    credit_multiplier = excluded.credit_multiplier
                """,
                (config.user_id, config.quota_credits, str(config.credit_multiplier)),
            )

    def get_config(self, user_id: str) -> UserConfig | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT user_id, quota_credits, credit_multiplier FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if row is None:
            return None
        return UserConfig(
            user_id=row["user_id"],
            quota_credits=row["quota_credits"],
            credit_multiplier=Decimal(row["credit_multiplier"]),
        )

    def try_reserve(self, user_id: str, estimated_credits: int, multiplier: Decimal) -> Reservation | None:
        # THE atomic step: add to reserved only if the user can afford it. A single
        # UPDATE statement is atomic, so concurrent callers cannot double-spend.
        with self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE users
                   SET reserved_credits = reserved_credits + ?
                 WHERE user_id = ?
                   AND (quota_credits - used_credits - reserved_credits) >= ?
                """,
                (estimated_credits, user_id, estimated_credits),
            )
            reserved = cur.rowcount == 1
        if not reserved:
            return None
        return Reservation(
            id=str(uuid.uuid4()),
            user_id=user_id,
            estimated_credits=estimated_credits,
            multiplier=multiplier,
        )

    def finalize(self, reservation: Reservation, credits_charged: int, record: UsageRecord) -> None:
        # Release the hold and apply the real charge, then append the ledger entry.
        # Works for success (credits_charged = actual) AND failure (0 or partial).
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE users
                   SET used_credits = used_credits + ?,
                       reserved_credits = reserved_credits - ?
                 WHERE user_id = ?
                """,
                (credits_charged, reservation.estimated_credits, reservation.user_id),
            )
            conn.execute(
                """
                INSERT INTO usage_records
                    (id, user_id, status, prompt_tokens, completion_tokens,
                     multiplier, credits_charged, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id, record.user_id, record.status.value,
                    record.prompt_tokens, record.completion_tokens,
                    str(record.multiplier), record.credits_charged,
                    record.created_at.isoformat(),
                ),
            )

    def get_summary(self, user_id: str) -> QuotaSummary | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT quota_credits, used_credits, reserved_credits FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if row is None:
            return None
        return QuotaSummary(row["quota_credits"], row["used_credits"], row["reserved_credits"])

    def list_records(self, user_id: str) -> list[UsageRecord]:
        from app.models import RecordStatus
        from datetime import datetime

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM usage_records WHERE user_id = ? ORDER BY created_at",
                (user_id,),
            ).fetchall()
        return [
            UsageRecord(
                id=r["id"],
                user_id=r["user_id"],
                status=RecordStatus(r["status"]),
                prompt_tokens=r["prompt_tokens"],
                completion_tokens=r["completion_tokens"],
                multiplier=Decimal(r["multiplier"]),
                credits_charged=r["credits_charged"],
                created_at=datetime.fromisoformat(r["created_at"]),
            )
            for r in rows
        ]
