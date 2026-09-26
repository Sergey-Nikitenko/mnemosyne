"""Phase 3.x golden task — bounded multi-round tool loop (WORK-001-OBS-04).

A real model performs multi-step work: inspect, read, write, test. The orchestrator
must repeat the model -> tools -> model cycle until the model returns a terminal
answer (content with no tool calls), bounded by a Nexus-owned `max_tool_rounds`
budget. The model may request another round; it never decides iteration is unbounded.

This test proves the loop executes multiple rounds in order, evaluates only after
the terminal model turn, and that a model perpetually requesting another tool stops
(and fails explicitly) when the budget is exhausted.

Run:  py tests/golden/test_phase3_tool_loop.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.contracts import ModelResponse, Risk, ToolCall  # noqa: E402
from core.events import EventType  # noqa: E402
from control.tools import ToolRegistry, ToolSpec  # noqa: E402
from execution.durable import DurableEventBus  # noqa: E402
from execution.fake import FakeExecutor  # noqa: E402
from execution.queue import TaskQueue  # noqa: E402
from knowledge.inmemory import ComposedRetriever  # noqa: E402
from apps.runtime import NexusRuntime  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


def build_runtime(tmp, script, max_tool_rounds=8):
    bus = DurableEventBus(os.path.join(tmp, "events.db"))
    queue = TaskQueue(os.path.join(tmp, "queue.db"), bus=bus)
    retriever = ComposedRetriever()
    retriever.ingest("filesystem", "docs/x.md", "v1", "context")
    tools = ToolRegistry()
    tools.register(ToolSpec("list_dir", "list files", Risk.READ))
    tools.register(ToolSpec("read_file", "read a file", Risk.READ))
    runtime = NexusRuntime(retriever=retriever, executor=FakeExecutor(model_script=script),
                           queue=queue, event_bus=bus, tools=tools,
                           max_tool_rounds=max_tool_rounds)
    return runtime, bus, queue


def significant(events):
    keep = {EventType.MODEL_REQUESTED, EventType.MODEL_COMPLETED,
            EventType.TOOL_REQUESTED, EventType.TOOL_COMPLETED,
            EventType.EVALUATION_COMPLETED, EventType.RUN_COMPLETED,
            EventType.RUN_FAILED}
    return [e.event_type for e in events if e.event_type in keep]


def main():
    print("Phase 3.x golden task: bounded multi-round tool loop")
    tmp = tempfile.mkdtemp()

    # -- multiple rounds, in order, evaluation only after the terminal turn -------
    script = [
        ModelResponse(model="fake", content="", success=True, tool_calls=[
            ToolCall(tool_name="list_dir", arguments={"path": "."}, correlation_id="call_alpha")]),
        ModelResponse(model="fake", content="", success=True, tool_calls=[
            ToolCall(tool_name="read_file", arguments={"path": "README.md"}, correlation_id="call_beta")]),
        ModelResponse(model="fake", content="The project contains a README.", success=True),
    ]
    runtime, bus, queue = build_runtime(tmp, script)
    task_id = runtime.ask("inspect the project")
    runtime.run_one()
    events = runtime.events(task_id=task_id)

    sig = significant(events)
    check(sig == [
        EventType.MODEL_REQUESTED, EventType.MODEL_COMPLETED,
        EventType.TOOL_REQUESTED, EventType.TOOL_COMPLETED,
        EventType.MODEL_REQUESTED, EventType.MODEL_COMPLETED,
        EventType.TOOL_REQUESTED, EventType.TOOL_COMPLETED,
        EventType.MODEL_REQUESTED, EventType.MODEL_COMPLETED,
        EventType.EVALUATION_COMPLETED, EventType.RUN_COMPLETED,
    ], "three model turns and two tool rounds execute in order")
    check([e.payload.get("tool") for e in events if e.event_type == EventType.TOOL_REQUESTED]
          == ["list_dir", "read_file"], "round 1 (list_dir) precedes round 2 (read_file)")
    eval_idx = sig.index(EventType.EVALUATION_COMPLETED)
    last_model = max(i for i, t in enumerate(sig) if t == EventType.MODEL_COMPLETED)
    check(eval_idx > last_model, "evaluation occurs only after the terminal model turn")
    check(any(e.event_type == EventType.RUN_COMPLETED
              and e.payload.get("answer") == "The project contains a README." for e in events),
          "the terminal answer is the run's answer")
    bus.close(); queue.close()

    # -- the bound: a model that always requests another tool stops at the budget --
    tmp2 = tempfile.mkdtemp()
    adversarial = [ModelResponse(model="fake", content="", success=True, tool_calls=[
        ToolCall(tool_name="list_dir", arguments={"path": "."}, correlation_id=f"call_{i}")])
        for i in range(10)]
    runtime2, bus2, queue2 = build_runtime(tmp2, adversarial, max_tool_rounds=3)
    task2 = runtime2.ask("loop forever")
    runtime2.run_one()
    evs2 = runtime2.events(task_id=task2)
    tool_requests = [e for e in evs2 if e.event_type == EventType.TOOL_REQUESTED]
    check(len(tool_requests) == 3, "exactly 3 tool rounds execute (the budget)")
    check(any(e.event_type == EventType.RUN_FAILED
              and "budget" in (e.payload.get("reason") or "") for e in evs2),
          "budget exhaustion fails explicitly (never masquerades as success)")
    check(not any(e.event_type == EventType.RUN_COMPLETED for e in evs2),
          "a perpetually-tool-calling model never reaches a successful run.completed")
    bus2.close(); queue2.close()

    print("\nPASS: the model->tools cycle repeats in order and is bounded; "
          "the model may request another round, it never decides iteration is unbounded.")


if __name__ == "__main__":
    main()
