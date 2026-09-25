"""ContextAdapter reference implementation (AD-038) — NCS -> provider-specific request.

The reference adapter is OpenAI-flavored: it understands OpenAI's request
VOCABULARY (a "messages" list with "role"/"content", a "temperature") but NOT
OpenAI's SDK or execution — a pure, stdlib-only translation, the context twin of
the tool/model adapters' boundary discipline.

    NCS (model-neutral) -> OpenAIContextAdapter -> ContextRequest (OpenAI-flavored)

A second adapter (e.g. a local-model adapter) produces a COMPLETELY different
ContextRequest from the SAME NCS: one continuity representation, many model
representations. Neither adapter changes the NCS.
"""
from __future__ import annotations

from dataclasses import dataclass

from .contracts import ContextRequest, NexusContinuityState


@dataclass(frozen=True)
class OpenAIContextAdapter:
    """Translate an NCS into an OpenAI-flavored ContextRequest.

    Configuration is explicit and frozen (deterministic per config). Identity,
    procedures, semantic memories, preferences, and the as-of stamp are assembled
    into a structured system prompt, emitted as the first "messages" entry.
    """

    provider: str = "openai"
    temperature: float = 0.0
    include_as_of: bool = True

    def adapt(self, ncs: NexusContinuityState) -> ContextRequest:
        body = {
            "messages": [{"role": "system", "content": self._system_prompt(ncs)}],
            "temperature": self.temperature,
        }
        return ContextRequest(provider=self.provider, model=ncs.model.key, body=body)

    def _system_prompt(self, ncs: NexusContinuityState) -> str:
        lines: list[str] = []
        if self.include_as_of:
            lines.append(f"World as of {ncs.as_of.isoformat()}")
        lines.append(f"user: {ncs.user.key}")
        lines.append(f"agent: {ncs.agent.key}")
        lines.append(f"model: {ncs.model.key}")
        if ncs.procedures:
            lines.append("")
            lines.append("Procedures:")
            for p in ncs.procedures:
                steps = "; ".join(p.steps)
                constraints = f" (constraints: {', '.join(p.constraints)})" if p.constraints else ""
                lines.append(f"- {p.name}: {steps}{constraints}")
        if ncs.semantic_memories:
            lines.append("")
            lines.append("Facts:")
            for s in ncs.semantic_memories:
                lines.append(f"- {s.subject} {s.predicate} {s.object}")
        if ncs.preferences:
            lines.append("")
            lines.append("Preferences:")
            for pref in ncs.preferences:
                lines.append(f"- {pref.key} = {pref.value}")
        return "\n".join(lines)
