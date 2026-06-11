"""SQLite storage behind a small repository interface.

The full ClaimRecord (including its trace) is stored as one canonical JSON document;
a few columns are denormalized for the ops list view. Nothing outside this module
knows the backend is SQLite, so swapping to Postgres later touches only this file.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Optional

from app.models import ClaimRecord


class ClaimRepository:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS claims (
                    claim_id        TEXT PRIMARY KEY,
                    status          TEXT NOT NULL,
                    member_id       TEXT NOT NULL,
                    claim_category  TEXT NOT NULL,
                    claimed_amount  REAL NOT NULL,
                    decision        TEXT,
                    approved_amount REAL,
                    confidence      REAL,
                    created_at      TEXT NOT NULL,
                    updated_at      TEXT NOT NULL,
                    record_json     TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS files (
                    claim_id    TEXT NOT NULL,
                    file_id     TEXT NOT NULL,
                    file_name   TEXT,
                    stored_path TEXT,
                    PRIMARY KEY (claim_id, file_id)
                );
                """
            )
            self._conn.commit()

    def save(self, record: ClaimRecord) -> None:
        decision = record.decision
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO claims
                (claim_id, status, member_id, claim_category, claimed_amount, decision,
                 approved_amount, confidence, created_at, updated_at, record_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.claim_id,
                    record.status.value,
                    record.submission.member_id,
                    record.submission.claim_category,
                    record.claimed_amount,
                    decision.decision.value if decision else None,
                    decision.approved_amount if decision else None,
                    record.confidence,
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                    record.model_dump_json(),
                ),
            )
            for doc in record.submission.documents:
                self._conn.execute(
                    "INSERT OR REPLACE INTO files (claim_id, file_id, file_name, stored_path) VALUES (?, ?, ?, ?)",
                    (record.claim_id, doc.file_id, doc.file_name, doc.stored_path),
                )
            self._conn.commit()

    def get(self, claim_id: str) -> Optional[ClaimRecord]:
        with self._lock:
            row = self._conn.execute(
                "SELECT record_json FROM claims WHERE claim_id = ?", (claim_id,)
            ).fetchone()
        if row is None:
            return None
        return ClaimRecord.model_validate(json.loads(row[0]))

    def list_claims(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT claim_id, status, member_id, claim_category, claimed_amount,
                       decision, approved_amount, confidence, created_at
                FROM claims ORDER BY created_at DESC
                """
            ).fetchall()
        keys = [
            "claim_id", "status", "member_id", "claim_category", "claimed_amount",
            "decision", "approved_amount", "confidence", "created_at",
        ]
        return [dict(zip(keys, row)) for row in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
