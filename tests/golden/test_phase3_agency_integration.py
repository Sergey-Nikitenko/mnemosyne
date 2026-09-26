"""Phase 3.x/8.x golden task — agency integration (OBS-03).

The canonical /ask task loop routes each model tool proposal through the Phase-8
ActionRunner, so a real side effect acquires a logical action_id (A) AND a physical
call_id (C). This proves: canonical-path agency, dual identity, correct ordering,
idempotent retry, crash -> attempted/unknown, deterministic reconstruction, and
Phase-9 learning evidence — without weakening the Phase-3 physical layer.

Run:  py tests/golden/test_phase3_agency_integration.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.contracts import (  # noqa: E402
    ActionRequest, AgentIdentity, Capability, ModelIdentity, ModelResponse, Risk,
    ToolCall, UserIdentity, utcnow,
)
from core.events import EventType  # noqa: E402
from core.state import action_attempted, reconstruct_action  # noqa: E402
from control.authority import ContinuityAuthority  # noqa: E402
from control.policy import PolicyEngine, PolicyRules  # noqa: E402
from control.tools import ToolRegistry, ToolSpec  # noqa: E402
from execution.action import ActionRunner  # noqa: E402
from execution.approvals import ApprovalStore  # noqa: E402
from execution.composite import CompositeExecutor  # noqa: E402
from execution.continuation import RunContinuationStore  # noqa: E402
from execution.durable import DurableEventBus  # noqa: E402
from execution.fake import FakeExecutor  # noqa: E402
from execution.filesystem import FilesystemToolExecutor  # noqa: E402
from execution.queue import TaskQueue  # noqa: E402
from knowledge.inmemory import ComposedRetriever  # noqa: E402
from memory.continuity import ContinuityProjector  # noqa: E402
from memory.memory import MemoryStore  # noqa: E402
from apps.runtime import NexusRuntime  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


class WriteThenCrash:
    """Writes the file (side effect) inside its root, then raises — a crash after
    the physical side effect, before the logical terminal event."""

    def __init__(self, root):
        self._root = Path(root).resolve()

    def run_model(self, request):
        return ModelResponse(model="fake", content="done", success=True)

    def execute_tool(self, call):
        (self._root / call.arguments["path"]).write_text(str(call.arguments["content"]))
        raise RuntimeError("crash after side effect")


def build_runtime(tmp, project, script, *, tool_exec=None, allowlist=(), max_tool_rounds=8):
    bus = DurableEventBus(os.path.join(tmp, "events.db"))
    queue = TaskQueue(os.path.join(tmp, "queue.db"), bus=bus)
    approvals = ApprovalStore(os.path.join(tmp, "approvals.db"), bus=bus)
    continuation = RunContinuationStore(os.path.join(tmp, "continuation.db"))
    retriever = ComposedRetriever()
    retriever.ingest("filesystem", "docs/x.md", "v1", "context")
    tools = ToolRegistry()
    tools.register(ToolSpec("list_dir", "list", Risk.READ))
    tools.register(ToolSpec("read_file", "read", Risk.READ))
    tools.register(ToolSpec("write_file", "write", Risk.WRITE))
    policy = PolicyEngine(PolicyRules(allowlist=list(allowlist)))
    capabilities = [Capability(t.name, t.description, t.risk, t.parameters) for t in tools.list()]
    authority = ContinuityAuthority(capabilities, policy)
    model = FakeExecutor(model_script=list(script))
    executor = CompositeExecutor(model, tool_exec or FilesystemToolExecutor(project),
                                 model_identity=ModelIdentity(model_id="fake"))
    runner = ActionRunner(authority, executor, bus)
    memory = MemoryStore(os.path.join(tmp, "memory.db"))
    projector = ContinuityProjector()
    user = UserIdentity(user_id="alice")
    agent = AgentIdentity(agent_id="researcher", role="operator")
    m = ModelIdentity(model_id="fake")

    def ncs_provider():
        return projector.project(utcnow(), memory, user=user, agent=agent, model=m)

    runtime = NexusRuntime(retriever=retriever, executor=executor, queue=queue,
                           event_bus=bus, tools=tools, approvals=approvals, policy=policy,
                           max_tool_rounds=max_tool_rounds, continuation=continuation,
                           action_runner=runner, ncs_provider=ncs_provider)
    return runtime, bus, queue, approvals, continuation, runner, memory


SCRIPT = [
    ModelResponse(model="fake", content="", success=True, tool_calls=[
        ToolCall(tool_name="write_file", arguments={"path": "sentinel.txt", "content": "hello"},
                 correlation_id="call_alpha")]),
    ModelResponse(model="fake", content="done", success=True),
]


def main():
    print("Phase 3.x/8.x golden task: agency integration")
    tmp = tempfile.mkdtemp()
    project = tempfile.mkdtemp()

    # -- normal path: /ask -> ActionRequest -> Authority -> action.* + tool.* ----
    runtime, bus, queue, approvals, continuation, runner, memory = build_runtime(
        tmp, project, SCRIPT, allowlist=["write_file"])
    task_id = runtime.ask("write the sentinel", user="alice", agent="researcher")
    runtime.run_one()
    evs = runtime.events(task_id=task_id)

    # 1. canonical-path agency: the /ask tool op acquired an action_id
    a_req = [e for e in evs if e.event_type == EventType.ACTION_REQUESTED]
    a_done = [e for e in evs if e.event_type == EventType.ACTION_COMPLETED]
    check(len(a_req) == 1 and len(a_done) == 1, "the canonical /ask tool op became ONE logical action")
    A = a_req[0].payload["action_id"]
    C = a_done[0].payload["call_id"]
    check(A == "call_alpha", "the logical action_id is the model's proposed identity")

    # 2. dual identity: distinct A and C, explicitly correlated
    check(C and C != A, "the physical call_id (C) is distinct from the logical action_id (A)")
    check(a_done[0].payload.get("call_id") == C, "action.completed(A) carries the physical call_id (C)")

    # 3. ordering: action.requested(A) -> tool.requested(C) -> tool.completed(C) -> action.completed(A)
    sig = [e.event_type for e in evs if e.event_type in
           (EventType.ACTION_REQUESTED, EventType.TOOL_REQUESTED,
            EventType.TOOL_COMPLETED, EventType.ACTION_COMPLETED)]
    check(sig == [EventType.ACTION_REQUESTED, EventType.TOOL_REQUESTED,
                  EventType.TOOL_COMPLETED, EventType.ACTION_COMPLETED],
          "action.requested precedes tool.* which precedes action.completed")
    check(os.path.exists(os.path.join(project, "sentinel.txt")),
          "the write_file physically wrote the sentinel")

    # 7. reconstruction through the existing Phase-8 reconstruct_action
    recon = reconstruct_action(A, evs)
    check(recon is not None and recon.success and recon.call_id == C,
          "reconstruct_action(A, events) reconstructs the /ask side effect")

    # 6. logical retry: retry A returns the SAME result, no second physical call
    before = len([e for e in runtime.events() if e.event_type == EventType.TOOL_REQUESTED])
    retried = runner.run(ActionRequest(capability="write_file", action_id=A,
                                       parameters={"path": "sentinel.txt", "content": "hello"},
                                       requested_by=AgentIdentity(agent_id="researcher")),
                         _ncs(memory), run_id="r", task_id="t")
    after = len([e for e in runtime.events() if e.event_type == EventType.TOOL_REQUESTED])
    check(retried is not None and retried.action_id == A and after == before,
          "retrying terminal A reconstructs without another physical execution")

    # 8. learning evidence: action.completed(A) is Phase-9 eligible
    terminal_ids = {e.payload.get("action_id") for e in runtime.events()
                    if e.event_type in (EventType.ACTION_COMPLETED, EventType.ACTION_FAILED)}
    check(A in terminal_ids, "the /ask action is valid Phase-9 learning evidence")
    bus.close(); queue.close(); approvals.close(); continuation.close(); memory.close()

    # -- crash path: side effect happens, no logical terminal, no auto re-execute --
    tmp2 = tempfile.mkdtemp()
    project2 = tempfile.mkdtemp()
    crash = WriteThenCrash(project2)
    r2, b2, q2, a2, c2, runner2, mem2 = build_runtime(tmp2, project2, SCRIPT,
                                                      tool_exec=crash, allowlist=["write_file"])
    try:
        r2.ask("write the sentinel", user="alice", agent="researcher")
        r2.run_one()
    except RuntimeError:
        pass
    evs2 = r2.events()
    A2 = next(e.payload["action_id"] for e in evs2 if e.event_type == EventType.ACTION_REQUESTED)
    check(os.path.exists(os.path.join(project2, "sentinel.txt")),
          "the sentinel was written before the crash")
    check(action_attempted(A2, evs2) and reconstruct_action(A2, evs2) is None,
          "after crash, A is attempted/unknown (never a fabricated terminal)")
    # attempted/unknown A must NOT auto-re-execute
    again = runner2.run(ActionRequest(capability="write_file", action_id=A2,
                                      parameters={"path": "sentinel.txt", "content": "hello"},
                                      requested_by=AgentIdentity(agent_id="researcher")),
                        _ncs(mem2), run_id="r2", task_id="t2")
    check(again is None, "attempted/unknown A is NOT automatically re-executed")
    b2.close(); q2.close(); a2.close(); c2.close(); mem2.close()

    print("\nPASS: the canonical task loop composes Phase-8 agency (action_id) with "
          "Phase-3 physical execution (call_id) — idempotent, reconstructible, and "
          "Phase-9-visible, without weakening either layer.")


def _ncs(memory):
    return ContinuityProjector().project(utcnow(), memory,
                                         user=UserIdentity(user_id="alice"),
                                         agent=AgentIdentity(agent_id="researcher", role="operator"),
                                         model=ModelIdentity(model_id="fake"))


if __name__ == "__main__":
    main()
