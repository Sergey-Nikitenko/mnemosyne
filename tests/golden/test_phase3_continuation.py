"""Phase 3.x golden task — durable approval continuation (WORK-001-OBS-05).

An approval pause is short-lived execution state, not Phase-7 continuity: on resume
the orchestrator must continue the interrupted model/tool conversation — executing
the EXACT approved invocation — rather than restarting reasoning from the original
request. This test proves exact continuation across a process restart, and that an
approval for invocation A cannot execute a regenerated invocation B.

Run:  py tests/golden/test_phase3_continuation.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.contracts import ModelResponse, Risk, ToolCall, ToolResult  # noqa: E402
from core.events import EventType  # noqa: E402
from control.tools import ToolRegistry, ToolSpec  # noqa: E402
from execution.approvals import ApprovalStore  # noqa: E402
from execution.continuation import RunContinuationStore  # noqa: E402
from execution.durable import DurableEventBus  # noqa: E402
from execution.fake import FakeExecutor  # noqa: E402
from execution.queue import TaskQueue  # noqa: E402
from knowledge.inmemory import ComposedRetriever  # noqa: E402
from apps.runtime import NexusRuntime  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


class RecordingExecutor(FakeExecutor):
    """Plays a scripted model and records every tool invocation (name/args/correlation)."""

    def __init__(self, script, log):
        super().__init__(model_script=script)
        self.log = log

    def execute_tool(self, call):
        self.log.append((call.tool_name, dict(call.arguments), call.correlation_id))
        return ToolResult(tool_call=call, success=True, output={"echo": dict(call.arguments)})


def build_runtime(tmp, script, log):
    bus = DurableEventBus(os.path.join(tmp, "events.db"))
    queue = TaskQueue(os.path.join(tmp, "queue.db"), bus=bus)
    approvals = ApprovalStore(os.path.join(tmp, "approvals.db"), bus=bus)
    continuation = RunContinuationStore(os.path.join(tmp, "continuation.db"))
    retriever = ComposedRetriever()
    retriever.ingest("filesystem", "docs/x.md", "v1", "context")
    tools = ToolRegistry()
    tools.register(ToolSpec("list_dir", "list files", Risk.READ))
    tools.register(ToolSpec("read_file", "read a file", Risk.READ))
    tools.register(ToolSpec("write_file", "write a file", Risk.WRITE))
    runtime = NexusRuntime(retriever=retriever, executor=RecordingExecutor(script, log),
                           queue=queue, event_bus=bus, tools=tools, approvals=approvals,
                           continuation=continuation)
    return runtime, bus, queue, approvals, continuation


def approval_id(runtime, task_id):
    return next(e.payload["approval_id"] for e in runtime.events(task_id=task_id)
                if e.event_type == EventType.APPROVAL_REQUIRED)


def main():
    print("Phase 3.x golden task: durable approval continuation")

    # -- part 1: exact continuation across a process restart --------------------
    tmp = tempfile.mkdtemp()
    log = []
    script = [
        ModelResponse(model="fake", content="", success=True, tool_calls=[
            ToolCall(tool_name="list_dir", arguments={"path": "."}, correlation_id="call_alpha")]),
        ModelResponse(model="fake", content="", success=True, tool_calls=[
            ToolCall(tool_name="read_file", arguments={"path": "README.md"}, correlation_id="call_beta")]),
        ModelResponse(model="fake", content="", success=True, tool_calls=[
            ToolCall(tool_name="write_file", arguments={"path": "output.txt", "content": "hello"},
                      correlation_id="call_gamma")]),
    ]
    r1, bus, queue, approvals, continuation = build_runtime(tmp, script, log)
    task_id = r1.ask("inspect and write")
    outcome = r1.run_one()
    check(outcome.waiting, "the run pauses for approval on write_file")
    check([n for n, _, _ in log] == ["list_dir", "read_file"],
          "list_dir and read_file executed once before the pause; write_file did not")

    aid = approval_id(r1, task_id)
    bus.close(); queue.close(); approvals.close(); continuation.close()

    # rebuild from the same workspace, approve, resume
    r2, bus2, queue2, approvals2, continuation2 = build_runtime(tmp, [
        ModelResponse(model="fake", content="Done.", success=True)], log)
    r2.approve(aid)  # runtime.approve marks + REQUES the task
    r2.run_one()

    names = [n for n, _, _ in log]
    check(names == ["list_dir", "read_file", "write_file"],
          "resume executed ONLY the pending write_file — list_dir/read_file were NOT re-executed")
    write = [a for n, a, c in log if n == "write_file"]
    check(write == [{"path": "output.txt", "content": "hello"}],
          "the approved invocation's exact parameters were executed")
    check([c for n, _, c in log if n == "write_file"] == ["call_gamma"],
          "the correlation_id survives the pause unchanged")
    check(any(e.event_type == EventType.RUN_COMPLETED for e in r2.events(task_id=task_id)),
          "the run completes after resume")
    bus2.close(); queue2.close(); approvals2.close(); continuation2.close()

    # -- part 2: approval A cannot execute regenerated invocation B -------------
    tmp2 = tempfile.mkdtemp()
    log2 = []
    rA, bA, qA, aA, cA = build_runtime(tmp2, [
        ModelResponse(model="fake", content="", success=True, tool_calls=[
            ToolCall(tool_name="write_file", arguments={"path": "output.txt", "content": "safe"},
                     correlation_id="call_A")])], log2)
    taskA = rA.ask("write output")
    rA.run_one()  # pauses on write_file("output.txt")
    aidA = approval_id(rA, taskA)
    bA.close(); qA.close(); aA.close(); cA.close()

    # resume, but the scripted model now regenerates a DIFFERENT invocation
    rB, bB, qB, aB, cB = build_runtime(tmp2, [
        ModelResponse(model="fake", content="", success=True, tool_calls=[
            ToolCall(tool_name="write_file", arguments={"path": "other.txt", "content": "different"},
                     correlation_id="call_B")])], log2)
    rB.approve(aidA)
    outcomeB = rB.run_one()
    check(outcomeB.waiting, "the regenerated invocation B pauses for its OWN approval")
    check([a for n, a, _ in log2 if n == "write_file"] == [{"path": "output.txt", "content": "safe"}],
          "approval A executed A only — B (other.txt) was never executed")
    bB.close(); qB.close(); aB.close(); cB.close()

    print("\nPASS: an approval resumes the exact interrupted invocation; completed work "
          "is not repeated, correlation survives, and approval A cannot execute a "
          "regenerated invocation B.")


if __name__ == "__main__":
    main()
