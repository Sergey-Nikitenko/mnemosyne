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

**Concurrency (AD-027).** The store is safe under concurrent readers and writers:
each thread gets its OWN sqlite3 connection (thread-local), so no connection is
ever shared across threads. This is the SAME policy as the execution stores
(`execution/sqlite.SqliteStore`), implemented locally because `memory/` must not
reach into `execution/` (layer gate). The exclusive transitions
(`record_if_current` / `retire_if_current`) were already single conditional INSERT
statements, so the database — not Python timing — still arbitrates the race
across connections. `check_same_thread=False` exists only so `close()` can close
connections a (now-exited) worker thread opened; it does NOT reintroduce shared
connections (sharing is prevented by construction, one per thread).
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime

from core.contracts import Preference, Procedure, SemanticMemory, new_id, utcnow
from core.events import EventType


class MemoryStore:
    """A durable event log of memory changes + a deterministic projection of it."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._local = threading.local()
        self._connections: list[sqlite3.Connection] = []
        self._lock = threading.Lock()
        self._closed = False
        self._conn()  # create the first connection and apply the schema

    @staticmethod
    def _connect(path: str) -> sqlite3.Connection:
        """Open ONE connection with the deliberate SQLite policy (AD-027)."""
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS memory_events ("
            "event_id TEXT PRIMARY KEY, kind TEXT, memory_id TEXT, "
            "version INTEGER, payload TEXT, emitted_at TEXT)")

    def _conn(self) -> sqlite3.Connection:
        """The CURRENT thread's connection, created on first use (AD-027)."""
        if self._closed:
            raise RuntimeError("MemoryStore is closed")
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._connect(self.path)
            self._schema(conn)
            conn.commit()
            self._local.conn = conn
            with self._lock:
                self._connections.append(conn)
        return conn

    def connection_count(self) -> int:
        """Number of open connections (one per thread that has used this store)."""
        with self._lock:
            return len(self._connections)

    def record(self, kind: str, memory_id: str, content: dict,
               scope: str = "", provenance: dict | None = None, now=None):
        """The ONLY write path: emit an authoritative memory event.

        Returns the projected record at its new version. `content` is the
        kind-specific payload (name/steps/constraints for a procedure, ...).
        `now` is injectable so tests can control the event's `emitted_at`."""
        current = self.get(kind, memory_id)
        if current is not None and current.status == "retired":
            return None  # a retired memory cannot be resurrected via record()
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
        conn = self._conn()
        conn.execute(
            "INSERT INTO memory_events (event_id, kind, memory_id, version, payload, emitted_at) "
            "VALUES (?,?,?,?,?,?)",
            (event_id, kind, memory_id, version, json.dumps(payload), emitted))
        conn.commit()
        return self.get(kind, memory_id)

    def record_if_current(self, kind: str, memory_id: str, content: dict,
                          expected_version: int, scope: str = "",
                          provenance: dict | None = None, now=None):
        """Compare-and-append: append version `expected_version + 1` ONLY if the
        current version still equals `expected_version`, enforced in a single
        atomic conditional INSERT (a concurrent advance to a later version makes
        the INSERT insert zero rows). Returns the projected record, or None if the
        target moved (stale)."""
        current = self.get(kind, memory_id)
        if current is None or current.version != expected_version:
            return None
        if current.status == "retired":
            return None  # a retired memory cannot be adapted (no implicit resurrection)
        version = expected_version + 1
        event_id = new_id("evt")
        emitted = (now or utcnow()).isoformat()
        payload = {
            "kind": kind, "memory_id": memory_id, "version": version,
            "scope": scope, "provenance": {"event_id": event_id, **(provenance or {})},
            "created_at": current.created_at.isoformat(), "updated_at": emitted,
            **content,
        }
        conn = self._conn()
        cur = conn.execute(
            "INSERT INTO memory_events (event_id, kind, memory_id, version, payload, emitted_at) "
            "SELECT ?, ?, ?, ?, ?, ? "
            "WHERE ? = (SELECT COALESCE(MAX(version), 0) FROM memory_events "
            "           WHERE kind = ? AND memory_id = ?)",
            (event_id, kind, memory_id, version, json.dumps(payload), emitted,
             expected_version, kind, memory_id))
        conn.commit()
        if cur.rowcount == 0:
            return None
        return self.get(kind, memory_id)

    def retire_if_current(self, kind: str, memory_id: str, expected_version: int,
                          provenance: dict | None = None, now=None):
        """Append-only retirement (AD-051): mark the logical memory no longer
        participating from now forward, ONLY if its current content version still
        equals `expected_version` (freshness at the write boundary). Returns the
        projected record (status "retired"), or None if stale/unknown. Idempotent:
        re-retiring at the same version returns it unchanged."""
        current = self.get(kind, memory_id)
        if current is None or current.version != expected_version:
            return None  # stale or unknown — never silently retire a newer version
        if current.status == "retired":
            return current  # idempotent
        event_id = new_id("evt")
        emitted = (now or utcnow()).isoformat()
        payload = {
            "event": "retired", "kind": kind, "memory_id": memory_id,
            "retired_at": emitted,
            "provenance": {"event_id": event_id, **(provenance or {})},
        }
        # version=0 is the retirement sentinel (content events are version >= 1),
        # so MAX(version) over content events is the current content version.
        conn = self._conn()
        cur = conn.execute(
            "INSERT INTO memory_events (event_id, kind, memory_id, version, payload, emitted_at) "
            "SELECT ?, ?, ?, 0, ?, ? "
            "WHERE ? = (SELECT COALESCE(MAX(version), 0) FROM memory_events "
            "           WHERE kind = ? AND memory_id = ? AND version >= 1)",
            (event_id, kind, memory_id, json.dumps(payload), emitted,
             expected_version, kind, memory_id))
        conn.commit()
        if cur.rowcount == 0:
            return None
        return self.get(kind, memory_id)

    def retirement_history(self, kind: str, memory_id: str) -> list[dict]:
        """The retirement events for a logical memory, oldest first."""
        rows = self._conn().execute(
            "SELECT payload FROM memory_events "
            "WHERE kind=? AND memory_id=? AND version=0 ORDER BY rowid",
            (kind, memory_id)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def get(self, kind: str, memory_id: str):
        """Project the event log into the CURRENT version (or None), with its
        lifecycle status (active / retired)."""
        rows = self._conn().execute(
            "SELECT payload FROM memory_events "
            "WHERE kind=? AND memory_id=? AND version >= 1 ORDER BY version",
            (kind, memory_id)).fetchall()
        if not rows:
            return None
        status = "retired" if self._is_retired(kind, memory_id) else "active"
        return self._project(kind, json.loads(rows[-1][0]), status=status)

    def history(self, kind: str, memory_id: str) -> list:
        """All CONTENT versions, oldest first (retirement is a lifecycle event,
        never a content version). Proves v1 is never silently mutated."""
        rows = self._conn().execute(
            "SELECT payload FROM memory_events "
            "WHERE kind=? AND memory_id=? AND version >= 1 ORDER BY version",
            (kind, memory_id)).fetchall()
        return [self._project(kind, json.loads(r[0])) for r in rows]

    def list_as_of(self, kind: str, as_of) -> list:
        """Every record of a kind, projected to its version in force at `as_of`,
        with lifecycle status: ACTIVE unless retired on or before `as_of`.
        Deterministic: sorted by memory_id."""
        rows = self._conn().execute(
            "SELECT memory_id, payload FROM memory_events "
            "WHERE kind=? AND version >= 1 AND emitted_at <= ? ORDER BY memory_id, version",
            (kind, as_of.isoformat())).fetchall()
        latest: dict[str, dict] = {}
        for memory_id, payload_json in rows:
            latest[memory_id] = json.loads(payload_json)
        retired = self._retired_ids(kind, as_of)
        return [self._project(kind, latest[mid],
                              status="retired" if mid in retired else "active")
                for mid in sorted(latest)]

    def _is_retired(self, kind: str, memory_id: str) -> bool:
        row = self._conn().execute(
            "SELECT 1 FROM memory_events WHERE kind=? AND memory_id=? AND version=0",
            (kind, memory_id)).fetchone()
        return row is not None

    def _retired_ids(self, kind: str, as_of) -> set:
        rows = self._conn().execute(
            "SELECT memory_id FROM memory_events "
            "WHERE kind=? AND version=0 AND emitted_at <= ?",
            (kind, as_of.isoformat())).fetchall()
        return {r[0] for r in rows}

    @staticmethod
    def _ts(value):
        return datetime.fromisoformat(value) if value else utcnow()

    @staticmethod
    def _project(kind: str, payload: dict, status: str = "active"):
        base = dict(
            memory_id=payload["memory_id"], version=payload["version"],
            scope=payload.get("scope", ""), provenance=payload.get("provenance", {}),
            status=status,
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
        """Close every connection the store ever opened, whichever thread opened
        it. Idempotent. Writes nothing — closing never modifies authoritative
        state or fabricates a terminal event."""
        if self._closed:
            return
        self._closed = True
        with self._lock:
            conns, self._connections = self._connections, []
        for conn in conns:
            conn.close()
        self._local.conn = None
