"""RepetitionGate — a deterministic observation counter, never a cap.

HARD RULE: tool calls are never capped. This gate no longer REFUSES a repeated
read; it only FLAGS an equivalent observation so the presentation can be
compacted (the cost-reduction response, not a blocked door). The 2nd and later
equivalent observations are served with a `repetition` flag; nothing is ever
rejected. A mutation that changes the target resets the counter. No LLM, no
side effect, pure observation counting.
"""
from __future__ import annotations


class RepetitionGate:
    def __init__(self, serve_limit: int = 3):
        self._obs: dict[tuple, dict] = {}
        self._serve_limit = serve_limit

    def verdict(self, tool: str, target: str, fingerprint: str) -> str:
        """'allow' | 'flag' — NEVER 'reject' (tool calls are never capped)."""
        key = (tool, target)
        rec = self._obs.get(key)
        if rec is None or rec.get("fingerprint") != fingerprint:
            self._obs[key] = {"fingerprint": fingerprint, "count": 1}
            return "allow"
        rec["count"] += 1
        return "flag"

    def reset(self, tool: str, target: str) -> None:
        self._obs.pop((tool, target), None)
