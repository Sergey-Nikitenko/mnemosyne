"""Event-sourced memory — durable, versioned, provenance-aware (AD-036).

Memory objects are PROJECTIONS of authoritative events. The only write path is
`record()`, which emits a `memory.created` / `memory.updated` event and persists
it. There is no direct UPDATE: a caller (or a model) cannot rewrite authoritative
memory except through an event. `get()` re-projects from the log each time, so
mutating a returned object never touches the store.

    model proposes  ->  record() emits event  ->  projection updated

This is the memory twin of the run/task/approval event-sourcing already in the
framework: the event log is the source of truth; the object is derived.

The store is stdlib-only (raw sqlite3, like knowledge/persistent.py) so `memory/`
keeps importing core only.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from core.contracts import Preference, Procedure, SemanticMemory, new_id, utcnow
from core.events import EventType


class MemoryStore:
    """A durable event log of memory changes + a deterministic projection of it."""

    def __init__(self, path: str) -> None:
        self._conn = sqlite3.connect(path)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS memory_events ("
            "event_id TEXT PRIMARY KEY, kind TEXT, memory_id TEXT, "
            "version INTEGER, payload TEXT, emitted_at TEXT)")
        self._conn.commit()

    def record(self, kind: str, memory_id: str, content: dict,
               scope: str = "", provenance: dict | None = None, now=None):
        """The ONLY write path: emit an authoritative memory event.

        Returns the projected record at its new version. `content` is the
        kind-specific payload (name/steps/constraints for a procedure, ...).
        `now` is injectable so tests can control the event's `emitted_at`."""
        current = self.get(kind, memory_id)
        version = (current.version + 1) if current is not None else 1
        event_type = EventType.MEMORY_UPDATED if current is not None else EventType.MEMORY_CREATED
        event_id = new_id("evt")
        emitted = (now or utcnow()).isoformat()
        created = current.created_at.isoformat() if current is not None else emitted
        payload = {
            "kind": kind, "memory_id": memory_id, "version": version,
            "scope": scope, "provenance": {"event_id": event_id, **(provenance or {})},
            "created_at": created, "updated_at": emitted,
            **content,
        }
        self._conn.execute(
            "INSERT INTO memory_events (event_id, kind, memory_id, version, payload, emitted_at) "
            "VALUES (?,?,?,?,?,?)",
            (event_id, kind, memory_id, version, json.dumps(payload), emitted))
        self._conn.commit()
        return self.get(kind, memory_id)

    def get(self, kind: str, memory_id: str):
        """Project the event log into the CURRENT version (or None)."""
        rows = self._conn.execute(
            "SELECT payload FROM memory_events WHERE kind=? AND memory_id=? ORDER BY version",
            (kind, memory_id)).fetchall()
        if not rows:
            return None
        return self._project(kind, json.loads(rows[-1][0]))

    def history(self, kind: str, memory_id: str) -> list:
        """All versions, oldest first (proves v1 is never silently mutated)."""
        rows = self._conn.execute(
            "SELECT payload FROM memory_events WHERE kind=? AND memory_id=? ORDER BY version",
            (kind, memory_id)).fetchall()
        return [self._project(kind, json.loads(r[0])) for r in rows]

    def list_as_of(self, kind: str, as_of) -> list:
        """Every record of a kind, each projected to its version in force at
        `as_of` (the latest version whose event was emitted on or before it).
        Deterministic: sorted by memory_id."""
        rows = self._conn.execute(
            "SELECT memory_id, payload FROM memory_events "
            "WHERE kind=? AND emitted_at <= ? ORDER BY memory_id, version",
            (kind, as_of.isoformat())).fetchall()
        latest: dict[str, dict] = {}
        for memory_id, payload_json in rows:
            latest[memory_id] = json.loads(payload_json)
        return [self._project(kind, latest[mid]) for mid in sorted(latest)]

    @staticmethod
    def _ts(value):
        return datetime.fromisoformat(value) if value else utcnow()

    @staticmethod
    def _project(kind: str, payload: dict):
        base = dict(
            memory_id=payload["memory_id"], version=payload["version"],
            scope=payload.get("scope", ""), provenance=payload.get("provenance", {}),
            created_at=MemoryStore._ts(payload.get("created_at")),
            updated_at=MemoryStore._ts(payload.get("updated_at")))
        if kind == "procedure":
            return Procedure(name=payload.get("name", ""),
                             steps=payload.get("steps", []),
                             constraints=payload.get("constraints", []), **base)
        if kind == "semantic":
            return SemanticMemory(subject=payload.get("subject", ""),
                                  predicate=payload.get("predicate", ""),
                                  object=payload.get("object", ""), **base)
        if kind == "preference":
            return Preference(key=payload.get("key", ""), value=payload.get("value", ""), **base)
        raise ValueError(f"unknown memory kind: {kind}")

    def close(self) -> None:
        self._conn.close()
