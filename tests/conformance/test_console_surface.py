"""Conformance: the Operator Console is a projection-only surface.

The console (`apps/console.py` + `apps/static/console.html`) composes the SAME
NexusRuntime/contracts/events as REST/WS/CLI/dashboard, and adds read-only
operator projections: identities, the continuity/NCS summary, the pending
approval queue, the raw event log, and the action lifecycle. This test proves the
four things that make it a projection and not a new authority:

1. Projection-only — every console GET is observational (no event-log / memory /
   approval mutation). The only writes ride the already-authorized paths.
2. Three views agree — REST, CLI, and the raw TaskState projection report the
   SAME status for the SAME durable task.
3. An interrupted action (action.requested, no terminal event) projects as
   attempted / unknown — NEVER a failure verdict (the AD-050 projection).
4. Restart reconstructs — a fresh runtime + console over the same durable stores
   report the same task, NCS, and action lifecycle.

Run:  py tests/conformance/test_console_surface.py
"""
import os
import re
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi.testclient import TestClient  # noqa: E402

from core.contracts import (  # noqa: E402
    AgentIdentity, Event, ModelIdentity, ModelResponse, Risk, ToolCall,
    UserIdentity, new_id, utcnow,
)
from core.events import EventType  # noqa: E402
from core.state import TaskState  # noqa: E402
from control.tools import ToolRegistry, ToolSpec  # noqa: E402
from execution.durable import DurableEventBus  # noqa: E402
from execution.fake import FakeExecutor  # noqa: E402
from execution.queue import TaskQueue  # noqa: E402
from knowledge.inmemory import ComposedRetriever  # noqa: E402
from memory.continuity import ContinuityProjector  # noqa: E402
from memory.memory import MemoryStore  # noqa: E402
from apps.cli import dispatch  # noqa: E402
from apps.console import create_console_app  # noqa: E402
from apps.runtime import NexusRuntime  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


SCRIPT = [
    ModelResponse(model="fake", content="", success=True, tool_calls=[
        ToolCall(tool_name="github.read_file", arguments={"path": "a.py"})]),
    ModelResponse(model="fake", content="done", success=True),
]

IDENTITIES = (
    UserIdentity(user_id="operator-alice"),
    AgentIdentity(agent_id="researcher", role="operator"),
    ModelIdentity(model_id="fake", family="fake", version="1"),
)

HARD_RULE = ("The Operator Console is a projection of authoritative Mnemosyne "
             "state. It may request operations and display evidence; it never "
             "defines truth, authority, identity, or continuity.")


def _norm(s):
    return re.sub(r"\s+", " ", s)


def build_runtime(tmp):
    bus = DurableEventBus(os.path.join(tmp, "events.db"))
    queue = TaskQueue(os.path.join(tmp, "queue.db"), bus=bus)
    retriever = ComposedRetriever()
    retriever.ingest("filesystem", "docs/x.md", "v1", "context for the task")
    tools = ToolRegistry()
    tools.register(ToolSpec("github.read_file", "read a file", Risk.READ))
    runtime = NexusRuntime(retriever=retriever, executor=FakeExecutor(model_script=SCRIPT),
                           queue=queue, event_bus=bus, tools=tools)
    return runtime, bus, queue


def build_memory(tmp):
    store = MemoryStore(os.path.join(tmp, "memory.db"))
    now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    store.record("procedure", "proc.active", {
        "name": "Ship a release", "steps": ["build", "test", "publish"],
        "constraints": []}, scope="project", now=now)
    store.record("procedure", "proc.retired", {
        "name": "Old runbook", "steps": [], "constraints": []},
        scope="project", now=now)
    store.retire_if_current("procedure", "proc.retired", 1, now=now)
    store.record("preference", "pref.verbosity", {"key": "verbosity", "value": "concise"},
                 scope="project", now=now)
    return store


def publish(runtime, event_type, run_id, task_id, payload, status="running"):
    runtime.event_bus.publish(Event(
        event_id=new_id("evt"), event_type=event_type, timestamp=utcnow(),
        run_id=run_id, task_id=task_id, component="test", status=status,
        payload=payload))


def main():
    print("Conformance: Operator Console is projection-only")
    tmp = tempfile.mkdtemp()
    user, agent, model = IDENTITIES
    runtime, bus, queue = build_runtime(tmp)
    store = build_memory(tmp)
    client = TestClient(create_console_app(
        runtime, memory_store=store, user=user, agent=agent, model=model,
        projector=ContinuityProjector()))

    # -- 1. the console page carries the hard rule verbatim --------------------
    resp = client.get("/")
    check(resp.status_code == 200, "GET / serves the operator console page")
    html = resp.text
    check(_norm(HARD_RULE) in _norm(re.sub(r"<[^>]+>", " ", html)),
          "the page states the hard rule: projection of authoritative state, never truth/authority/identity/continuity")
    readme = open(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "apps", "static", "README.md"), encoding="utf-8").read()
    check(_norm(HARD_RULE) in _norm(readme),
          "the frontend README carries the hard rule verbatim")

    # -- 2. identities are the injected stable identities ----------------------
    ident = client.get("/api/identities").json()
    check(ident["user"]["key"] == "user/operator-alice", "user identity projected by key")
    check(ident["agent"]["key"] == "agent/researcher@1", "agent identity projected by key")
    check(ident["model"]["key"] == "model/fake@1", "model identity projected by key")

    # -- 3. the NCS summary is the memory projection (retired excluded) ---------
    ncs = client.get("/api/ncs").json()
    check(ncs["summary"]["procedures"] == 1, "retired procedure excluded from NCS (active only)")
    check(ncs["summary"]["preferences"] == 1, "preference counted in NCS")
    check(any(p["name"] == "Ship a release" for p in ncs["procedures"]),
          "active procedure appears by name")
    check(ncs["identity"]["user"]["key"] == "user/operator-alice",
          "NCS carries the same identity projection as /api/identities")

    # -- 4. an interrupted action is attempted / unknown, never failed ----------
    publish(runtime, EventType.ACTION_REQUESTED, "run-crash", "task-crash",
            {"action_id": "act.crashed", "capability": "github.read_file",
             "parameters": {}, "scope": "", "requested_by": "agent/researcher@1"})
    a = client.get("/api/actions/act.crashed").json()
    check(a["attempted"] is True, "interrupted action is attempted (action.requested present)")
    check(a["terminal"] is False, "interrupted action has NO terminal event")
    check(a["outcome"] is None, "interrupted action outcome is null — never a failure verdict")
    check(len(a["requested_events"]) == 1, "the request event is exposed for inspection")

    # -- 5. a completed action projects its authoritative terminal outcome ------
    publish(runtime, EventType.ACTION_REQUESTED, "run-done", "task-done",
            {"action_id": "act.done", "capability": "github.read_file",
             "parameters": {}, "scope": "", "requested_by": "agent/researcher@1"})
    publish(runtime, EventType.ACTION_COMPLETED, "run-done", "task-done",
            {"action_id": "act.done", "capability": "github.read_file",
             "success": True, "output": {"ok": True}, "error": None,
             "parameters": {}, "requested_by": "agent/researcher@1", "scope": "",
             "run_id": "run-done", "task_id": "task-done"},
            status="success")
    b = client.get("/api/actions/act.done").json()
    check(b["attempted"] is True and b["terminal"] is True, "completed action is terminal")
    check(b["outcome"]["success"] is True, "completed action outcome is the recorded success")

    # -- 6. approvals project from approval.* events ----------------------------
    publish(runtime, EventType.APPROVAL_REQUIRED, "run-appr", "task-appr",
            {"approval_id": "appr-1", "tool": "github.force_push", "risk": "destructive"},
            status="waiting")
    appr = client.get("/api/approvals").json()["approvals"]
    check(any(x["approval_id"] == "appr-1" and x["status"] == "pending" for x in appr),
          "a pending approval projects as pending (approval.required, no terminal)")
    check(appr[0]["tool"] == "github.force_push" and appr[0]["risk"] == "destructive",
          "approval carries its bound proposal (tool/risk)")

    # -- 7. the raw event log is exposed ---------------------------------------
    evs = client.get("/api/events").json()["events"]
    check(any(e["event_type"] == EventType.ACTION_REQUESTED for e in evs),
          "the raw event log exposes action.requested")

    # -- 8. projection-only: GETs never mutate the event log --------------------
    before = len(runtime.events())
    for url in ("/", "/api/identities", "/api/ncs", "/api/approvals",
                "/api/events", "/api/actions/act.crashed", "/api/actions/act.done"):
        client.get(url)
    check(len(runtime.events()) == before,
          "console GETs are observational (no event-log mutation)")
    hist_before = len(store.history("procedure", "proc.active"))
    client.get("/api/ncs")
    check(len(store.history("procedure", "proc.active")) == hist_before,
          "the NCS projection never writes the memory store")

    # -- 9. three views agree (REST / CLI / raw state) --------------------------
    task_id = runtime.ask("demo task for the console", user="operator-alice",
                          agent="researcher")
    runtime.run_one()
    rest_status = client.get(f"/tasks/{task_id}").json()["status"]
    cli_status = next(ln.split("Status: ", 1)[1] for ln in dispatch(
        runtime, ["task", task_id]).splitlines() if ln.startswith("Status: "))
    raw_status = TaskState.reconstruct(
        runtime.task(task_id), runtime.events(task_id=task_id)).status.value
    check(rest_status == cli_status == raw_status == "done",
          f"REST/CLI/raw state agree on '{rest_status}' for the same durable task")

    # -- 10. restart reconstructs from authoritative state -----------------------
    bus.close(); queue.close(); store.close()
    runtime2, _, _ = build_runtime(tmp)
    store2 = MemoryStore(os.path.join(tmp, "memory.db"))  # reopen, never re-record
    client2 = TestClient(create_console_app(
        runtime2, memory_store=store2, user=user, agent=agent, model=model))
    check(client2.get(f"/tasks/{task_id}").json()["status"] == "done",
          "after restart the task is still done (reconstructed from events)")
    a2 = client2.get("/api/actions/act.crashed").json()
    check(a2["attempted"] is True and a2["terminal"] is False and a2["outcome"] is None,
          "after restart the interrupted action is still attempted/unknown, never failed")
    b2 = client2.get("/api/actions/act.done").json()
    check(b2["terminal"] is True and b2["outcome"]["success"] is True,
          "after restart the completed action reconstructs its terminal outcome")
    check(client2.get("/api/ncs").json()["summary"]["procedures"] == 1,
          "after restart the NCS reconstructs the same active memory")

    bus.close(); queue.close(); store2.close()
    print("\nPASS: the Operator Console is a projection of authoritative state, "
          "never a new authority.")


if __name__ == "__main__":
    main()
