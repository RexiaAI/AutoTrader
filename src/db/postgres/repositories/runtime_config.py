from __future__ import annotations

import json
from typing import Any

from src.db.postgres.pool import _connect_ro, _pg_write_conn


def get_runtime_config() -> dict[str, Any]:
    conn = _connect_ro()
    try:
        cur = conn.cursor()
        cur.execute("SELECT config_json FROM runtime_config WHERE id = 1")
        row = cur.fetchone()
        if not row:
            raise RuntimeError("runtime_config row (id=1) not found")
        raw = row[0]
        if raw is None:
            raise RuntimeError("runtime_config.config_json is NULL")
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8", errors="replace")
        raw_str = str(raw)
        try:
            doc = json.loads(raw_str)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"runtime_config.config_json is not valid JSON: {e}") from e
        if not isinstance(doc, dict):
            raise RuntimeError(f"runtime_config.config_json must be a JSON object; got {type(doc).__name__}")
        return doc
    finally:
        conn.close()


def set_runtime_config(doc: dict[str, Any]) -> None:
    raw = json.dumps(doc, separators=(",", ":"), ensure_ascii=False)
    with _pg_write_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO runtime_config (id, config_json, updated_at)
            VALUES (1, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (id) DO UPDATE
            SET config_json = EXCLUDED.config_json,
                updated_at = EXCLUDED.updated_at
            """,
            (raw,),
        )


