"""Operator Console — a projection-only operator surface (NOT a chat app, NOT a phase).

The console composes the SAME `NexusRuntime`, contracts, and event projections the
REST, WebSocket, dashboard, and CLI already share, and adds the read-only
projections an operator needs: identities, the continuity/NCS summary, the pending
approval queue, the raw event log, and the action lifecycle. It introduces no new
authority, no new mutation path, and no new phase — it is a Phase-4 surface
exercising the frozen 0–10 guarantees.

Hard rule (the frontend carries it verbatim):

    The Operator Console is a projection of authoritative Mnemosyne state. It may
    request operations and display evidence; it never defines truth, authority,
    identity, or continuity.

Every route here is either (a) already on the runtime/HTTP surface (submit a task,
approve/deny, read a trace) or (b) a pure projection of durable events + injected
identity/memory. No route writes to the memory store, the event log, or any store
directly. The only write path is the authorized one:

    UI -> API -> authorized path -> transition -> event -> UI updates

An interrupted action (an `action.requested` with no terminal event) is projected
as `attempted=true, terminal=false, outcome=null` — the UI renders "attempted /
unknown", never a FAILED verdict. That verdict is the projection of AD-050, not a
UI-side guess.
"""
from __future__ import annotations

import asyncio
import queue as _queue
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from core.contracts import (
    AgentIdentity, ModelIdentity, NexusContinuityState, UserIdentity, utcnow,
)
from core.events import EventType
from core.state import ApprovalState, action_attempted, reconstruct_action
from memory.continuity import ContinuityProjector
from apps.http import _serialize_event, create_app

MAX_QUEUE = 1024

_STATIC_DIR = Path(__file__).resolve().parent / "static"


def _identity(user: UserIdentity, agent: AgentIdentity, model: ModelIdentity) -> dict:
    """Project the three stable identities — never the provider config behind them."""
    return {
        "user": {"user_id": user.user_id, "key": user.key},
        "agent": {"agent_id": agent.agent_id, "role": agent.role,
                  "version": agent.version, "key": agent.key},
        "model": {"model_id": model.model_id, "family": model.family,
                  "version": model.version, "key": model.key},
    }


def _record(m, extra: dict) -> dict:
    base = {
        "memory_id": m.memory_id, "version": m.version, "scope": m.scope,
        "status": m.status,
        "created_at": m.created_at.isoformat(),
        "updated_at": m.updated_at.isoformat(),
    }
    base.update(extra)
    return base


def _serialize_ncs(ncs: NexusContinuityState) -> dict:
    procedures = [_record(p, {"name": p.name, "steps": p.steps,
                              "constraints": p.constraints}) for p in ncs.procedures]
    semantic = [_record(s, {"subject": s.subject, "predicate": s.predicate,
                            "object": s.object}) for s in ncs.semantic_memories]
    preferences = [_record(p, {"key": p.key, "value": p.value})
                   for p in ncs.preferences]
    return {
        "as_of": ncs.as_of.isoformat(),
        "identity": _identity(ncs.user, ncs.agent, ncs.model),
        "summary": {
            "procedures": len(procedures),
            "semantic_memories": len(semantic),
            "preferences": len(preferences),
        },
        "procedures": procedures,
        "semantic_memories": semantic,
        "preferences": preferences,
    }


def _serialize_outcome(outcome) -> dict | None:
    if outcome is None:
        return None  # no terminal event -> unknown, NEVER a failure verdict
    return {
        "action_id": outcome.action_id,
        "capability": outcome.capability,
        "success": outcome.success,
        "output": outcome.output,
        "error": outcome.error,
        "parameters": outcome.parameters,
        "requested_by": outcome.requested_by,
        "scope": outcome.scope,
        "run_id": outcome.run_id,
        "task_id": outcome.task_id,
        "completed_at": outcome.completed_at,
    }


def _approvals(events) -> list[dict]:
    """Every approval, projected from approval.* events (the authoritative lifecycle).

    `approval.required` names the proposal (tool/risk/task/run); the status is
    `ApprovalState.reconstruct`, exactly as the REST surface projects it. Pending
    approvals are the ones the operator may command (approve/deny) — never execute."""
    events = list(events)
    seen: dict[str, dict] = {}
    for e in events:
        if e.event_type != EventType.APPROVAL_REQUIRED:
            continue
        approval_id = e.payload.get("approval_id")
        if not approval_id:
            continue
        seen[approval_id] = {
            "approval_id": approval_id,
            "tool": e.payload.get("tool"),
            "risk": e.payload.get("risk"),
            "task_id": e.task_id,
            "run_id": e.run_id,
        }
    result = []
    for approval_id, meta in seen.items():
        meta["status"] = ApprovalState.reconstruct(approval_id, events).status
        result.append(meta)
    return sorted(result, key=lambda a: a["approval_id"])


def create_console_app(runtime, *, memory_store=None,
                       user: UserIdentity | None = None,
                       agent: AgentIdentity | None = None,
                       model: ModelIdentity | None = None,
                       projector: ContinuityProjector | None = None,
                       html_path: str | None = None) -> FastAPI:
    """The console surface: `create_app(runtime)` + read-only operator projections.

    `memory_store` (Phase 7) and the three identities are injected here because
    `NexusRuntime` (Phase 4) does not own them — the console is the composition
    point that exposes them read-only. With no `memory_store`, the NCS projection
    is identity-only (empty memory)."""
    app = create_app(runtime)

    projector = projector or ContinuityProjector()
    user = user or UserIdentity(user_id="operator")
    agent = agent or AgentIdentity(agent_id="console", role="operator")
    model = model or ModelIdentity(model_id="unset")

    def _events():
        return runtime.events()

    def _project_ncs() -> NexusContinuityState:
        as_of = utcnow()
        if memory_store is None:
            return NexusContinuityState(as_of=as_of, user=user,
                                        agent=agent, model=model)
        return projector.project(as_of, memory_store, user=user,
                                 agent=agent, model=model)

    @app.get("/api/identities")
    def identities():
        return _identity(user, agent, model)

    @app.get("/api/ncs")
    def ncs():
        return _serialize_ncs(_project_ncs())

    @app.get("/api/approvals")
    def approvals():
        return {"approvals": _approvals(_events())}

    @app.get("/api/events")
    def events():
        return {"events": [_serialize_event(e) for e in _events()]}

    @app.get("/api/actions/{action_id}")
    def action(action_id: str):
        events = _events()
        attempted = action_attempted(action_id, events)
        outcome = reconstruct_action(action_id, events)
        requested = [e for e in events
                     if e.event_type == EventType.ACTION_REQUESTED
                     and e.payload.get("action_id") == action_id]
        return {
            "action_id": action_id,
            "attempted": attempted,
            "terminal": outcome is not None,
            "outcome": _serialize_outcome(outcome),
            "requested_events": [_serialize_event(e) for e in requested],
        }

    @app.websocket("/ws/events")
    async def ws_events(websocket: WebSocket):
        """The authoritative event stream: durable replay, then a live tail.

        Read-only — it subscribes to the bus (like `/ws/runs/{run_id}`), never to
        the orchestrator, and pushes raw serialized events so the console's stream
        is the SAME projection as `/api/events` and `/traces/{run_id}`."""
        await websocket.accept()
        bus = runtime.event_bus
        for e in _events():
            await websocket.send_json({"kind": "event", **_serialize_event(e)})

        q: _queue.Queue = _queue.Queue(maxsize=MAX_QUEUE)
        overflowed: list[bool] = []

        def on_event(e):
            try:
                q.put_nowait(e)
            except _queue.Full:
                overflowed.append(True)

        bus.subscribe_all(on_event)
        try:
            while True:
                e = await asyncio.to_thread(q.get)
                if overflowed:
                    await websocket.send_json({"kind": "error",
                                               "detail": "slow consumer"})
                    break
                await websocket.send_json({"kind": "event", **_serialize_event(e)})
        except WebSocketDisconnect:
            pass
        finally:
            bus.unsubscribe_all(on_event)
            try:
                await websocket.close()
            except Exception:
                pass

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(html_path or str(_STATIC_DIR / "console.html"))

    return app
