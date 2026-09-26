"""CompositeExecutor — combines a model executor (run_model) and a tool executor
(execute_tool) behind one Executor protocol.

Pure composition: it adds no provider knowledge, no authority, and no side effect
of its own. The composition root uses it to pair a real reasoning backend (e.g.
DeepSeek) with a bounded tool executor (filesystem), so the orchestrator and the
ActionRunner consume one object while the model and the tools stay separate.
"""
from __future__ import annotations

from core.contracts import ModelRequest, ModelResponse, ToolCall, ToolResult


class CompositeExecutor:
    def __init__(self, model, tools, model_identity=None) -> None:
        self._model = model
        self._tools = tools
        self.model_identity = model_identity  # the logical ModelIdentity for the manifest

    def run_model(self, request: ModelRequest) -> ModelResponse:
        return self._model.run_model(request)

    def execute_tool(self, call: ToolCall) -> ToolResult:
        return self._tools.execute_tool(call)
