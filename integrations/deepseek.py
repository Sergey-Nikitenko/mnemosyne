"""DeepSeek model adapter — a real reasoning backend behind the Executor protocol.

DeepSeek's API is OpenAI-compatible. This adapter is the PROVIDER BOUNDARY: it
lives in `integrations/` (the layer that may touch the network), reads the API key
from a FILE (never a committed value), translates provider -> Nexus, and returns a
`ModelResponse`. The caller (apps/ composition root) consumes it behind the Executor
protocol and never sees the key, the Authorization header, or the HTTP machinery.

The key, header, and provider payload NEVER enter a Nexus contract, event, NCS,
trace, or memory — the adapter returns only a ModelResponse (model / content /
tokens / tool_calls / success / error). The model is more capable than the fake; it
is NOT more authoritative: it proposes tool calls; Nexus's policy/authority still
decides and the tool executor still executes.

Uses stdlib `urllib.request` only, so no third-party HTTP library crosses the layer.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from core.contracts import ModelRequest, ModelResponse, ToolCall


class DeepseekModel:
    """A DeepSeek chat model behind `run_model(request) -> ModelResponse`."""

    def __init__(self, *, api_key_file: str, model: str = "deepseek-chat",
                 base_url: str = "https://api.deepseek.com", tools=(),
                 timeout_s: float = 180.0) -> None:
        with open(api_key_file, encoding="utf-8") as f:
            self._api_key = f.read().strip()  # secret: never serialized, never logged
        self._model = model
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._tools = list(tools)
        self._timeout = timeout_s

    def _translate_messages(self, messages) -> list[dict]:
        """Translate model-neutral conversation messages into provider wire format.

        The orchestrator speaks `correlation_id` / `name` / `arguments`; DeepSeek
        (OpenAI-compatible) speaks `id` / `function` / `tool_call_id`. This method
        is the ONLY place that provider vocabulary appears — the correlation
        identity is preserved, the field name is translated."""
        out = []
        for m in messages:
            role = m.get("role")
            if role == "assistant" and "tool_calls" in m:
                out.append({
                    "role": "assistant",
                    "content": m.get("content"),
                    "tool_calls": [{
                        "id": tc.get("correlation_id", ""),
                        "type": "function",
                        "function": {"name": tc.get("name", ""),
                                     "arguments": json.dumps(tc.get("arguments", {}))},
                    } for tc in m["tool_calls"]],
                })
            elif role == "tool":
                cid = m.get("correlation_id", "")
                if not cid:
                    raise ValueError("tool result message missing correlation_id")
                out.append({"role": "tool", "tool_call_id": cid,
                            "content": m.get("content", "")})
            else:
                out.append(m)  # system / user pass through (role/content)
        return out

    def run_model(self, request: ModelRequest) -> ModelResponse:
        payload = {
            "model": self._model,
            "messages": self._translate_messages(request.messages),
            "max_tokens": request.max_tokens,
            "stream": False,
        }
        if self._tools:
            payload["tools"] = self._tools

        req = urllib.request.Request(
            self._url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {self._api_key}",
                     "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                body = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return ModelResponse(model=self._model, content="", success=False,
                                 error=f"provider HTTP {exc.code}")
        except urllib.error.URLError as exc:
            return ModelResponse(model=self._model, content="", success=False,
                                 error=f"provider error: {exc.reason}")

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return ModelResponse(model=self._model, content="", success=False,
                                 error="invalid provider response")

        return self._parse_response(data)

    def _parse_response(self, data: dict) -> ModelResponse:
        """Parse a parsed provider payload into a ModelResponse.

        Factored out so a provider whose tool calls carry extra provider-side
        correlation state (e.g. Gemini's `thought_signature`) can intercept the
        response without reimplementing the HTTP + error handling above.
        """
        if "error" in data:
            return ModelResponse(model=self._model, content="", success=False,
                                 error=str(data["error"])[:200])

        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        tool_calls = []
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function") or {}
            try:
                arguments = json.loads(fn.get("arguments") or "{}")
            except (json.JSONDecodeError, TypeError):
                arguments = {}
            tool_calls.append(ToolCall(tool_name=fn.get("name", ""), arguments=arguments,
                                       correlation_id=tc.get("id", "")))

        usage = data.get("usage") or {}
        return ModelResponse(
            model=self._model,
            content=message.get("content") or "",
            tokens_in=int(usage.get("prompt_tokens", 0)),
            tokens_out=int(usage.get("completion_tokens", 0)),
            success=True,
            tool_calls=tool_calls,
        )
