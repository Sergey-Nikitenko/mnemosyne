"""Run continuation — where an interrupted execution was, durably.

An approval pause is short-lived execution state, NOT Phase-7 continuity. When a
run pauses for human approval, the orchestrator checkpoints its model-neutral
conversation + the exact pending invocation + the tool-round budget here. On
resume it continues from that checkpoint instead of restarting reasoning from the
original request.

This is a CHECKPOINT, not an event log and not memory: it records the current
position of an interrupted run, is keyed by task_id (INSERT OR REPLACE = the
current position), and is cleared once the run resumes/terminates. It never
rewrites the append-only model/tool events, never contains provider wire-format
(DeepSeek/OpenAI) state, and never promotes transient model conversation into
Phase-7 durable semantic memory.
"""
from __future__ import annotations

import json

from execution.sqlite import SqliteStore


class RunContinuationStore(SqliteStore):
    """A durable checkpoint of one interrupted run, keyed by task_id."""

    def _schema(self, conn) -> None:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS run_continuation ("
            "task_id TEXT PRIMARY KEY, conversation TEXT, pending_call TEXT, "
            "rounds INTEGER)")

    def save(self, task_id: str, conversation: list[dict], pending_call: dict,
             rounds: int) -> None:
        conn = self._conn()
        conn.execute(
            "INSERT OR REPLACE INTO run_continuation VALUES (?,?,?,?)",
            (task_id, json.dumps(conversation), json.dumps(pending_call), rounds))
        conn.commit()

    def load(self, task_id: str) -> dict | None:
        conn = self._conn()
        row = conn.execute(
            "SELECT conversation, pending_call, rounds FROM run_continuation "
            "WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            return None
        return {"conversation": json.loads(row[0]),
                "pending_call": json.loads(row[1]),
                "rounds": row[2]}

    def clear(self, task_id: str) -> None:
        conn = self._conn()
        conn.execute("DELETE FROM run_continuation WHERE task_id=?", (task_id,))
        conn.commit()
