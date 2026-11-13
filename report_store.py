"""Small SQLite persistence layer for shareable GreenYield reports."""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.environ.get("GREENYIELD_DB_PATH", Path(__file__).resolve().parent / "data" / "reports.sqlite3"))


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    return db


def initialize():
    with connect() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS reports (
            id TEXT PRIMARY KEY, created_at TEXT NOT NULL, inputs_json TEXT NOT NULL,
            analysis_json TEXT NOT NULL, narrative_json TEXT NOT NULL,
            ai_used INTEGER NOT NULL DEFAULT 0, ai_calls INTEGER NOT NULL DEFAULT 0,
            ai_provider TEXT NOT NULL DEFAULT ''
        )""")
        columns = {row[1] for row in db.execute("PRAGMA table_info(reports)")}
        if "ai_provider" not in columns:
            db.execute("ALTER TABLE reports ADD COLUMN ai_provider TEXT NOT NULL DEFAULT ''")


def save(report_id: str, inputs: dict, analysis: dict, narrative: dict,
         ai_used: bool, ai_calls: int = 1, created_at: str | None = None, ai_provider: str = ""):
    created_at = created_at or datetime.now(timezone.utc).isoformat()
    with connect() as db:
        db.execute("""INSERT INTO reports
            (id, created_at, inputs_json, analysis_json, narrative_json, ai_used, ai_calls, ai_provider)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET inputs_json=excluded.inputs_json,
            analysis_json=excluded.analysis_json, narrative_json=excluded.narrative_json,
            ai_used=excluded.ai_used, ai_calls=excluded.ai_calls, ai_provider=excluded.ai_provider""",
            (report_id, created_at, json.dumps(inputs), json.dumps(analysis),
             json.dumps(narrative), int(ai_used), int(ai_calls), ai_provider))
    return created_at


def get(report_id: str) -> dict | None:
    with connect() as db:
        row = db.execute("SELECT * FROM reports WHERE id=?", (report_id,)).fetchone()
    if not row:
        return None
    return {"id": row["id"], "created_at": row["created_at"],
            "inputs": json.loads(row["inputs_json"]),
            "analysis": json.loads(row["analysis_json"]),
            "narrative": json.loads(row["narrative_json"]),
            "ai_used": bool(row["ai_used"]), "ai_calls": int(row["ai_calls"]),
            "ai_provider": row["ai_provider"] if "ai_provider" in row.keys() else ""}


initialize()
