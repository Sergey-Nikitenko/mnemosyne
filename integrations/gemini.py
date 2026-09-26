"""Gemini model adapter — a second real reasoning backend behind the Executor protocol.

Google exposes Gemini through an OpenAI-compatible chat-completions endpoint
(`https://generativelanguage.googleapis.com/v1beta/openai`). The OpenAI wire
translation (model-neutral -> `id` / `function` / `tool_call_id`) lives in
`integrations/deepseek.py`; this adapter reuses it and adds ONE Gemini-specific
concern: Gemini 3.x models run in "thinking" mode and attach a `thought_signature`
to each tool call, which MUST be echoed back on the next turn for the tool-calling
conversation to continue. The signature is provider-internal correlation state, so it
is stashed here (keyed by the tool-call id) and echoed in the translation — it never
enters a Nexus contract, event, NCS, trace, or memory.

Lives in `integrations/` (the network boundary): reads the API key from a FILE and
returns only a `ModelResponse`.
"""
from __future__ import annotations

import json

from core.contracts import ModelResponse
from integrations.deepseek import DeepseekModel

_GEMINI_OPENAI_BASE = "https://generativelanguage.googleapis.com/v1beta/openai"


class GeminiModel(DeepseekModel):
    """A Gemini chat model (via its OpenAI-compatible endpoint) behind `run_model()`."""

    def __init__(self, *, api_key_file: str, model: str = "gemini-3.8-flash",
                 base_url: str = _GEMINI_OPENAI_BASE, tools=(),
                 timeout_s: float = 180.0) -> None:
        super().__init__(api_key_file=api_key_file, model=model, base_url=base_url,
                         tools=tools, timeout_s=timeout_s)
        # provider-internal correlation state: tool-call id -> thought_signature
        self._signatures: dict[str, str] = {}

    def _parse_response(self, data: dict) -> ModelResponse:
        message = ((data.get("choices") or [{}])[0].get("message")) or {}
        for tc in message.get("tool_calls") or []:
            cid = tc.get("id", "")
            sig = ((tc.get("extra_content") or {}).get("google") or {}).get("thought_signature")
            if cid and sig:
                self._signatures[cid] = sig
        return super()._parse_response(data)

    def _translate_messages(self, messages) -> list[dict]:
        out = []
        for m in messages:
            role = m.get("role")
            if role == "assistant" and "tool_calls" in m:
                tcs = []
                for tc in m["tool_calls"]:
                    cid = tc.get("correlation_id", "")
                    entry = {
                        "id": cid,
                        "type": "function",
                        "function": {"name": tc.get("name", ""),
                                     "arguments": json.dumps(tc.get("arguments", {}))},
                    }
                    sig = self._signatures.get(cid)
                    if sig:
                        entry["extra_content"] = {"google": {"thought_signature": sig}}
                    tcs.append(entry)
                out.append({"role": "assistant", "content": m.get("content"),
                            "tool_calls": tcs})
            elif role == "tool":
                cid = m.get("correlation_id", "")
                if not cid:
                    raise ValueError("tool result message missing correlation_id")
                out.append({"role": "tool", "tool_call_id": cid,
                            "content": m.get("content", "")})
            else:
                out.append(m)  # system / user pass through
        return out
