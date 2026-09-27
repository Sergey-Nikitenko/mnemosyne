"""The execution plane's invariant, enforced at the boundary.

Every externally observable execution step emits an event BEFORE it is
considered complete:

    ToolCall  ──► tool.requested (event) ──► execute ──► ToolResult ──► tool.completed
    ModelRequest ──► model.requested ──► run ──► ModelResponse ──► model.completed

The "requested" event is emitted first, unconditionally. If the inner executor
raises, there is a `*.requested` event but NO `*.completed` event — so the step
is NOT considered complete, and a crashed worker's event log still tells the
truth about how far it got. This is the thing that makes "kill a worker at any
point and ask what Nexus knows happened" answerable.

This wrapper is itself an Executor: it returns the same contracts, so the rest
of the system is unchanged — only the event log gains the invariant.
"""
from __future__ import annotations

from core.contracts import Event, ModelRequest, ModelResponse, ToolCall, ToolResult, new_id, utcnow
from core.events import EventType


class InstrumentedExecutor:
    """Wraps an Executor, emitting `*.requested` before and `*.completed` after."""

    def __init__(self, inner, bus, *, component="executor", task_id="", run_id=""):
        self.inner = inner
        self.bus = bus
        self.component = component
        self.task_id = task_id
        self.run_id = run_id

    def _emit(self, event_type: str, status: str, payload: dict) -> None:
        self.bus.publish(Event(
            event_id=new_id("evt"),
            event_type=event_type,
            timestamp=utcnow(),
            run_id=self.run_id,
            task_id=self.task_id,
            component=self.component,
            status=status,
            payload=payload,
        ))

    def execute_tool(self, call: ToolCall) -> ToolResult:
        # event BEFORE the step can be considered complete (and before it even runs)
        # carries BOTH the physical call_id and the model's correlation_id, so one
        # id (correlation_id, which _agency_call also uses as action_id) traverses
        # call -> action -> result in the durable log (unified action identity).
        self._emit(EventType.TOOL_REQUESTED, "running",
                   {"tool": call.tool_name, "arguments": call.arguments,
                    "call_id": call.call_id, "correlation_id": call.correlation_id})
        result = self.inner.execute_tool(call)
        self._emit(EventType.TOOL_COMPLETED, "success" if result.success else "failed",
                   {"tool": call.tool_name, "success": result.success, "error": result.error,
                    "call_id": call.call_id, "correlation_id": call.correlation_id})
        return result

    def run_model(self, request: ModelRequest) -> ModelResponse:
        # the request carries its own identity, so requested/completed are
        # unambiguously the same run/step (even when one step makes many calls).
        request_id = request.request_id or new_id("req")
        self._emit(EventType.MODEL_REQUESTED, "running",
                   {"request_id": request_id, "messages": len(request.messages),
                    "max_tokens": request.max_tokens})
        response = self.inner.run_model(request)
        # The model's declared output is an observed fact (report before observation):
        # content + tool calls + token/latency telemetry, never private chain-of-thought.
        # `model` is the display name; `model_key` is the STABLE identity key used
        # for continuity (so a provider-reported name change can't silently break
        # the model's line).
        mi = getattr(self.inner, "model_identity", None)
        model_key = getattr(mi, "key", None) or response.model
        self._emit(EventType.MODEL_COMPLETED, "success" if response.success else "failed",
                   {"request_id": request_id, "model": response.model,
                    "model_key": model_key,
                    "content": response.content,
                    "tool_calls": [tc.tool_name for tc in (response.tool_calls or [])],
                    "tokens_in": getattr(response, "tokens_in", 0),
                    "tokens_out": getattr(response, "tokens_out", 0),
                    "latency_ms": getattr(response, "latency_ms", 0),
                    "success": response.success, "error": response.error})
        return response
