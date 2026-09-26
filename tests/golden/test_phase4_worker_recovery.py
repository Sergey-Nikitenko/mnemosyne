"""Phase 4.11 golden task — worker recovery preserves action idempotency (sharpened bar).

A worker crashes mid-action: `action.requested(A)` with no terminal (the physical side
effect may have happened). Recovery re-claims the TASK (generation N+1). The attempted /
unknown action A must NOT be re-executed, and if the model proposes again, the new action
B is a NEW action_id minted by the ordinary agency path — never "A2" mechanically produced
by recovery.
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.contracts import (  # noqa: E402
    AgentIdentity, Capability, ModelIdentity, ModelResponse, Risk, ToolCall,
    ToolResult, UserIdentity, utcnow,
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
from execution.filesystem import FilesystemToolExecutor  # noqa: E402
from execution.queue import TaskQueue  # noqa: E402
from execution.recovery import RecoveryManager  # noqa: E402
from knowledge.inmemory import ComposedRetriever  # noqa: E402
from memory.continuity import ContinuityProjector  # noqa: E402
from memory.memory import MemoryStore  # noqa: E402
from apps.runtime import NexusRuntime  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


class WriteThenCrash:
    """Writes the file, then raises — a crash after the side effect, before the terminal."""

    def __init__(self, root):
        self._root = Path(root)

    def run_model(self, request):
        return ModelResponse(model="fake", content="done", success=True)

    def execute_tool(self, call):
        (self._root / call.arguments["path"]).write_text(str(call.arguments["content"]))
        raise RuntimeError("crash after side effect")


class ProposeOnce:
    """Proposes write_file with a FIXED correlation id, then answers 'done'."""

    def __init__(self, correlation, path, content):
        self._corr = correlation
        self._path = path
        self._content = content

    def run_model(self, request):
        for m in request.messages:
            if m.get("role") == "tool":
                return ModelResponse(model="fake", content="done", success=True)
        return ModelResponse(model="fake", content="", success=True, tool_calls=[
            ToolCall(tool_name="write_file",
                     arguments={"path": self._path, "content": self._content},
                     correlation_id=self._corr)])


def build(ws, project, model, tool_exec):
    os.makedirs(ws, exist_ok=True)
    bus = DurableEventBus(os.path.join(ws, "events.db"))
    queue = TaskQueue(os.path.join(ws, "queue.db"), bus=bus)
    approvals = ApprovalStore(os.path.join(ws, "approvals.db"), bus=bus)
    continuation = RunContinuationStore(os.path.join(ws, "continuation.db"))
    retriever = ComposedRetriever()
    tools = ToolRegistry()
    tools.register(ToolSpec("write_file", "write", Risk.WRITE))
    policy = PolicyEngine(PolicyRules(allowlist=["write_file"]))
    capabilities = [Capability(t.name, t.description, t.risk, t.parameters) for t in tools.list()]
    authority = ContinuityAuthority(capabilities, policy)
    executor = CompositeExecutor(model, tool_exec, model_identity=ModelIdentity(model_id="fake"))
    runner = ActionRunner(authority, executor, bus)
    memory = MemoryStore(os.path.join(ws, "memory.db"))
    projector = ContinuityProjector()
    user = UserIdentity(user_id="alice")
    agent = AgentIdentity(agent_id="mnemosyne")
    m = ModelIdentity(model_id="fake")

    def ncs_provider():
        return projector.project(utcnow(), memory, user=user, agent=agent, model=m)

    runtime = NexusRuntime(retriever=retriever, executor=executor, queue=queue, event_bus=bus,
                           tools=tools, approvals=approvals, policy=policy, max_tool_rounds=8,
                           continuation=continuation, action_runner=runner, ncs_provider=ncs_provider)
    return runtime, bus, queue, approvals, continuation, runner, memory


def main():
    print("Phase 4.11 golden task: worker recovery preserves action idempotency")
    ws = tempfile.mkdtemp()
    project = tempfile.mkdtemp()

    # -- generation 1: worker claims, action A crashes (side effect may have happened) --
    r1, b1, q1, a1, c1, runner1, mem1 = build(ws, project, ProposeOnce("A-call", "probe.txt", "once"),
                                              WriteThenCrash(project))
    tid = r1.ask("write the probe", user="alice", agent="mnemosyne")
    claim = q1.claim("doomed")  # generation 1: the worker claims
    try:
        # run the orchestrator directly (NOT the Worker.run_one fail-path) so a mid-action
        # crash leaves the task CLAIMED — exactly what hard process death produces.
        r1.orchestrator.run(claim.task)
    except RuntimeError:
        pass
    evs1 = r1.events(task_id=tid)
    A = next(e.payload["action_id"] for e in evs1 if e.event_type == EventType.ACTION_REQUESTED)
    check(A == "A-call" and action_attempted(A, evs1) and reconstruct_action(A, evs1) is None,
          "after crash, A is attempted/unknown (no fabricated terminal)")
    b1.close(); q1.close(); a1.close(); c1.close(); mem1.close()

    # -- recovery: a new worker starts and its startup pass requeues the stale task --
    r2, b2, q2, a2, c2, runner2, mem2 = build(ws, project, ProposeOnce("B-call", "probe2.txt", "twice"),
                                              FilesystemToolExecutor(project))
    recovered = RecoveryManager(q2, lease_seconds=0.0).recover()
    check(recovered == [tid], "recovery requeued the stale task (generation N+1)")
    check(q2.generation(tid) == 1, "generation before re-claim is still 1")

    # -- generation 2: the model proposes a NEW action B; A is never re-executed --
    r2.run_one()
    evs2 = r2.events(task_id=tid)
    check(q2.generation(tid) == 2, "re-claim advanced generation N -> N+1")
    check(reconstruct_action(A, evs2) is None, "A remains attempted/unknown after recovery")
    check(not any(e.event_type == EventType.ACTION_COMPLETED and e.payload.get("action_id") == A
                  for e in evs2), "A was NOT re-executed after recovery")
    b_comp = [e for e in evs2 if e.event_type == EventType.ACTION_COMPLETED
              and e.payload.get("action_id") == "B-call"]
    check(len(b_comp) == 1, "the new proposal B executed exactly once")
    check("B-call" != A, "B is a new action_id (agency path), never A2 minted by recovery")
    check(os.path.exists(os.path.join(project, "probe2.txt")), "B's side effect happened")
    b2.close(); q2.close(); a2.close(); c2.close(); mem2.close()

    print("\nPASS: recovery re-claims the task but never re-executes an uncertain action; "
          "a new proposal is a new action_id from the agency path.")


if __name__ == "__main__":
    main()
