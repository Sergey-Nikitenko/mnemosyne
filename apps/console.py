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
import json
import os
import queue as _queue
import string
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from core.contracts import (
    AgentIdentity, ModelIdentity, NexusContinuityState, Risk, UserIdentity, new_id, utcnow,
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
                       models=None,
                       project_dir: str = "",
                       workspace: str = "",
                       project_dir_selector=None,
                       stop_signal=None,
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

    @app.get("/api/workspace")
    def workspace_info():
        name = Path(project_dir).name if project_dir else ""
        return {"project_dir": project_dir, "project_name": name, "workspace": workspace}

    @app.get("/api/models")
    def models_list():
        if models is None:
            return {"models": [], "active": ""}
        return {"models": models.list(), "active": models.active_name()}

    @app.post("/api/models/select")
    def models_select(payload: dict):
        name = payload.get("name", "")
        if models is None or not models.select(name):
            raise HTTPException(status_code=404, detail="unknown model")
        return {"active": name, "models": models.list()}

    @app.get("/api/permissions")
    def permissions_list():
        """Active capability grants (durable continuity preferences), projected read-only."""
        if memory_store is None:
            return {"permissions": []}
        prefs = [p for p in memory_store.list_as_of("preference", utcnow())
                 if p.status == "active"]
        return {"permissions": [{"key": p.key, "value": p.value, "scope": p.scope}
                                for p in prefs]}

    @app.post("/api/permissions/grant")
    def permissions_grant(payload: dict):
        """An operator-authority command (like approve/deny): record a durable
        capability preference. The model can never call this — it is the operator's
        grant, never self-authorization."""
        capability = payload.get("capability", "")
        value = payload.get("value", "allow")
        if not capability:
            raise HTTPException(status_code=400, detail="capability required")
        if value not in ("allow", "deny", "approval_required"):
            raise HTTPException(status_code=400, detail="bad value")
        if memory_store is None:
            raise HTTPException(status_code=409, detail="no memory store")
        key = f"capability.{capability}"
        memory_store.record("preference", key, {"key": key, "value": value})
        return {"key": key, "value": value}

    @app.post("/api/permissions/revoke")
    def permissions_revoke(payload: dict):
        """Retire a capability preference — returns it to its base policy verdict
        (the operator's revoke, never a deny-by-default)."""
        capability = payload.get("capability", "")
        if not capability:
            raise HTTPException(status_code=400, detail="capability required")
        if memory_store is None:
            raise HTTPException(status_code=409, detail="no memory store")
        key = f"capability.{capability}"
        current = memory_store.get("preference", key)
        if current is None or getattr(current, "status", "") == "retired":
            return {"key": key, "removed": False}
        memory_store.retire_if_current("preference", key, current.version)
        return {"key": key, "removed": True}

    @app.get("/api/permission-mode")
    def permission_mode_get():
        """The session permission tier: readonly / workspace / full (DSH-style)."""
        mode = "workspace"
        if workspace:
            try:
                with open(os.path.join(workspace, "permission-mode.json"), "r", encoding="utf-8") as f:
                    mode = json.load(f).get("mode", "workspace")
            except (OSError, ValueError):
                pass
        return {"mode": mode}

    @app.post("/api/permission-mode")
    def permission_mode_set(payload: dict):
        """An operator-authority command: set the session permission tier by writing
        durable capability preferences (readonly denies writes; workspace auto-allows
        writes but keeps elevation gated; full auto-allows writes and elevation)."""
        mode = payload.get("mode", "")
        if mode not in ("readonly", "workspace", "full"):
            raise HTTPException(status_code=400, detail="bad mode")
        if memory_store is None:
            raise HTTPException(status_code=409, detail="no memory store")
        tools = runtime.tools.list() if getattr(runtime, "tools", None) else []
        for spec in tools:
            if spec.risk == Risk.READ:
                val = "allow"  # reads are always permitted; clears any stale denial
            elif spec.name == "run_elevated":
                val = "allow" if mode == "full" else "approval_required"
            elif mode == "readonly":
                val = "deny"
            else:
                val = "allow"
            key = f"capability.{spec.name}"
            memory_store.record("preference", key, {"key": key, "value": val})
        if workspace:
            try:
                with open(os.path.join(workspace, "permission-mode.json"), "w", encoding="utf-8") as f:
                    json.dump({"mode": mode}, f)
            except OSError:
                pass
        return {"mode": mode}

    @app.get("/api/dirs")
    def dirs(path: str = ""):
        """A server-side directory browser (read-only) for choosing a project root."""
        if path:
            base = path
        elif os.name == "nt":
            drives = [d + ":\\" for d in string.ascii_uppercase if os.path.exists(d + ":\\")]
            return {"path": "", "parent": None, "dirs": drives}
        else:
            base = "/"
        try:
            entries = sorted(e for e in os.listdir(base) if os.path.isdir(os.path.join(base, e)))
        except OSError:
            entries = []
        parent = os.path.dirname(base.rstrip("\\/")) or None
        return {"path": base, "parent": parent, "dirs": entries}

    @app.get("/api/project-dir")
    def project_dir_get():
        return {"project_dir": project_dir_selector.current() if project_dir_selector else project_dir}

    @app.post("/api/project-dir")
    def project_dir_set(payload: dict):
        path = payload.get("path", "")
        if project_dir_selector is None:
            raise HTTPException(status_code=409, detail="no switchable project dir")
        if not project_dir_selector.select(path):
            raise HTTPException(status_code=400, detail="not a directory")
        return {"project_dir": project_dir_selector.current()}

    def _projects_file():
        return os.path.join(workspace, "projects.json") if workspace else ""

    def _load_projects():
        f = _projects_file()
        if not f:
            return {}
        try:
            with open(f, "r", encoding="utf-8") as fh:
                return json.load(fh).get("projects", {})
        except (OSError, ValueError):
            return {}

    def _save_projects(projects):
        f = _projects_file()
        if not f:
            return
        try:
            with open(f, "w", encoding="utf-8") as fh:
                json.dump({"projects": projects}, fh)
        except OSError:
            pass

    @app.get("/api/projects")
    def projects_list():
        """The remembered project registry (name -> path) + the active project."""
        projects = _load_projects()
        current = project_dir_selector.current() if project_dir_selector else project_dir
        current = current.rstrip("\\/")
        active = next((n for n, p in projects.items() if p.rstrip("\\/") == current),
                      (os.path.basename(current) if current else ""))
        return {"active": active,
                "projects": [{"name": n, "path": p, "active": p.rstrip("\\/") == current}
                             for n, p in projects.items()]}

    @app.post("/api/projects/add")
    def projects_add(payload: dict):
        """Remember a path under a name (default: its basename) and switch to it."""
        path = payload.get("path", "")
        if not path or not os.path.isdir(path):
            raise HTTPException(status_code=400, detail="not a directory")
        name = payload.get("name") or os.path.basename(path.rstrip("\\/")) or path
        projects = _load_projects()
        projects[name] = path
        _save_projects(projects)
        if project_dir_selector is not None:
            project_dir_selector.select(path)
        return {"name": name, "path": path}

    @app.post("/api/projects/select")
    def projects_select(payload: dict):
        """Switch to a remembered project by name (no path re-selection)."""
        name = payload.get("name", "")
        projects = _load_projects()
        if name not in projects:
            raise HTTPException(status_code=404, detail="unknown project")
        path = projects[name]
        if project_dir_selector is not None:
            if not project_dir_selector.select(path):
                raise HTTPException(status_code=400, detail="not a directory")
        return {"name": name, "path": path}

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
        """The authoritative event stream: durable replay, then a durable-log poll.

        The worker is a SEPARATE process: it publishes into the same durable SQLite
        log, but the in-memory bus only notifies same-process subscribers. Polling
        `_events()` (the durable read path) is what lets a worker's events reach the
        browser live across processes. Read-only — no mutation, no new authority."""
        await websocket.accept()
        cursor = 0

        def drain():
            nonlocal cursor
            evs = _events()
            batch = evs[cursor:]
            cursor = len(evs)
            return batch

        try:
            while True:
                for e in drain():
                    await websocket.send_json({"kind": "event", **_serialize_event(e)})
                await asyncio.sleep(0.5)
        except WebSocketDisconnect:
            pass
        try:
            await websocket.close()
        except Exception:
            pass

    # ---- conversation sessions (server-side memory, NOT a browser transcript) ----
    def _session_file():
        return os.path.join(workspace, "sessions.json") if workspace else ""

    def _load_sessions():
        f = _session_file()
        if not f:
            return {"active": "", "sessions": {}}
        try:
            with open(f, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {"active": "", "sessions": {}}

    def _save_sessions(state):
        f = _session_file()
        if not f:
            return
        try:
            with open(f, "w", encoding="utf-8") as fh:
                json.dump(state, fh)
        except OSError:
            pass

    @app.get("/api/session")
    def session_get():
        state = _load_sessions()
        sid = state.get("active", "")
        s = state["sessions"].get(sid) if sid else None
        messages = s["user_messages"] if s else []
        return {"active": sid,
                "sessions": [{"id": k, "active": k == sid,
                              "count": len(v["user_messages"]),
                              "preview": (v["user_messages"][-1]["text"][:40]
                                          if v["user_messages"] else "")}
                             for k, v in state["sessions"].items()],
                "messages": messages}

    @app.post("/api/session/new")
    def session_new():
        state = _load_sessions()
        sid = new_id("session")
        state["sessions"][sid] = {"user_messages": [], "created_at": utcnow().isoformat()}
        state["active"] = sid
        _save_sessions(state)
        return {"session_id": sid}

    @app.post("/api/session/select")
    def session_select(payload: dict):
        sid = payload.get("session_id", "")
        state = _load_sessions()
        if sid not in state["sessions"]:
            raise HTTPException(status_code=404, detail="unknown session")
        state["active"] = sid
        _save_sessions(state)
        return {"session_id": sid}

    @app.post("/api/session/message")
    def session_message(payload: dict):
        """Append a user message to the ACTIVE session and run a task whose context
        is the server-side transcript — keyed to the MODEL, not the session.

        Continuity lives in the MODEL identity, not in a session: a model's
        injected history is ITS OWN answers only. So the same model continuing
        across a server restart sees one continuous line, while a SWAPPED model
        correctly sees none of its own answers and reports "I did not produce
        this work." The operator's messages are shared context; the answers are
        attributed via `model.completed.payload.model`."""
        message = payload.get("message", "")
        if not message:
            raise HTTPException(status_code=400, detail="message required")
        state = _load_sessions()
        sid = state.get("active", "")
        if not sid or sid not in state["sessions"]:
            sid = new_id("session")
            state["sessions"][sid] = {"user_messages": [], "created_at": utcnow().isoformat()}
            state["active"] = sid
        current_model = models.active_name() if models else ""
        # continuity key = the STABLE ModelIdentity.key, not the mutable display
        # name. A model swap changes the key -> a clean line; a config change that
        # keeps the identity keeps the line. The display name stays for the header.
        current_key = model.key if model else current_model
        answers = {}
        answer_models = {}
        settled = set()
        for e in _events():
            if e.event_type == EventType.MODEL_COMPLETED and e.task_id:
                answer_models[e.task_id] = e.payload.get("model_key") or e.payload.get("model")
            if e.event_type == EventType.RUN_COMPLETED and e.task_id and e.payload.get("answer"):
                answers[e.task_id] = e.payload["answer"]
            if e.event_type in (EventType.RUN_COMPLETED, EventType.RUN_FAILED,
                                EventType.RUN_STOPPED) and e.task_id:
                settled.add(e.task_id)
        lines = []
        for um in state["sessions"][sid]["user_messages"]:
            if um.get("actioned"):
                continue  # drained — don't re-inject
            status = "settled" if um["task_id"] in settled else "open"
            lines.append(f"user ({status}): " + um["text"])
            a = answers.get(um["task_id"])
            if a and answer_models.get(um["task_id"]) == current_key:
                lines.append("assistant: " + a)
        lines.append("user (current): " + message)
        transcript = ("This is a continuing conversation with the model named "
                      f"{current_model or 'unknown'}. Each of the operator's past "
                      "turns is marked SETTLED (already handled — context only, "
                      "do NOT redo) or OPEN (not yet resolved). The CURRENT turn "
                      "is the objective to act on. Earlier turns that YOU (this "
                      "same model) produced:\n" + "\n".join(lines))
        task_id = runtime.ask(transcript)
        state["sessions"][sid]["user_messages"].append(
            {"text": message, "task_id": task_id, "ts": utcnow().isoformat(),
             "actioned": False})
        _save_sessions(state)
        return {"task_id": task_id, "session_id": sid}

    @app.post("/api/session/drain")
    def session_drain():
        """Mark every message in the ACTIVE session as actioned, so the next turn
        injects only NEW messages. The model stops re-processing already-done work
        (its own answers remain durable in the event log — only the injected
        prompt history is cleared)."""
        state = _load_sessions()
        sid = state.get("active", "")
        if sid and sid in state["sessions"]:
            for um in state["sessions"][sid]["user_messages"]:
                um["actioned"] = True
            _save_sessions(state)
        return {"session_id": sid, "drained": True}

    @app.post("/api/tasks/{task_id}/stop")
    def task_stop(task_id: str):
        """Operator interrupt: request the worker to stop a running task at the
        next round boundary. A command (writes a durable flag), never an execution."""
        if stop_signal is None:
            raise HTTPException(status_code=409, detail="stop signal not wired")
        stop_signal.request(task_id)
        return {"task_id": task_id, "stopped": "requested"}

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(html_path or str(_STATIC_DIR / "console.html"))

    return app
