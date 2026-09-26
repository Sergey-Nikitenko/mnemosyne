"""Phase 3.x golden task — tool-call conversation correlation (WORK-001-OBS-02).

A real OpenAI-compatible model needs each tool result correlated with the assistant
tool invocation that caused it. This regression proves the orchestrator preserves
correlation identity (model-neutral `correlation_id`) through execution and into
the follow-up turn, that the assistant invocation precedes the results, and that
two calls cannot cross.

Run:  py tests/golden/test_phase3_tool_correlation.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.contracts import ModelResponse, Risk, ToolCall  # noqa: E402
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


class RecordingExecutor:
    """Plays a scripted model but records every ModelRequest it receives, so the
    test can inspect exactly what the follow-up turn presented to the model."""

    def __init__(self, script):
        self._inner = FakeExecutor(model_script=script)
        self.requests = []

    def run_model(self, request):
        self.requests.append(request)
        return self._inner.run_model(request)

    def execute_tool(self, call):
        return self._inner.execute_tool(call)


# A provider-shaped double: the assistant proposes TWO tool invocations with
# distinct correlation ids (list_dir = call_alpha, read_file = call_beta).
SCRIPT = [
    ModelResponse(model="fake", content="", success=True, tool_calls=[
        ToolCall(tool_name="list_dir", arguments={"path": "."}, correlation_id="call_alpha"),
        ToolCall(tool_name="read_file", arguments={"path": "a.txt"}, correlation_id="call_beta"),
    ]),
    ModelResponse(model="fake", content="done", success=True),
]


def build_runtime(tmp):
    bus = DurableEventBus(os.path.join(tmp, "events.db"))
    queue = TaskQueue(os.path.join(tmp, "queue.db"), bus=bus)
    retriever = ComposedRetriever()
    retriever.ingest("filesystem", "docs/x.md", "v1", "context")
    tools = ToolRegistry()
    tools.register(ToolSpec("list_dir", "list files", Risk.READ))
    tools.register(ToolSpec("read_file", "read a file", Risk.READ))
    executor = RecordingExecutor(SCRIPT)
    runtime = NexusRuntime(retriever=retriever, executor=executor, queue=queue,
                           event_bus=bus, tools=tools)
    return runtime, bus, queue, executor


def main():
    print("Phase 3.x golden task: tool-call conversation correlation")
    tmp = tempfile.mkdtemp()
    runtime, bus, queue, executor = build_runtime(tmp)
    runtime.ask("inspect the project")
    runtime.run_one()

    check(len(executor.requests) == 2, "the model was called twice (turn 1 + follow-up)")
    msgs = executor.requests[1].messages

    roles = [m.get("role") for m in msgs]
    assistant_idx = roles.index("assistant")
    tool_idxs = [i for i, r in enumerate(roles) if r == "tool"]
    check(assistant_idx < min(tool_idxs),
          "the assistant tool invocation precedes the tool results")

    assistant = msgs[assistant_idx]
    id_to_name = {tc["correlation_id"]: tc["name"] for tc in assistant["tool_calls"]}
    check(id_to_name == {"call_alpha": "list_dir", "call_beta": "read_file"},
          "both invocation identities survive translation, and stay distinct")

    tool_msgs = [msgs[i] for i in tool_idxs]
    check(len(tool_msgs) == 2, "one tool result per invocation")
    seen = {t["correlation_id"]: t["name"] for t in tool_msgs}
    check(seen == id_to_name, "each result is correlated with its own invocation (no cross)")
    check(all("echo" in t.get("content", "") for t in tool_msgs),
          "the follow-up carries the actual tool output, not a mere count")

    print("\nPASS: correlation identity survives translation; multiple calls cannot cross.")


if __name__ == "__main__":
    main()
