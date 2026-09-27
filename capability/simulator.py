"""Context Simulator — EXPERIMENTAL (speculative, recoverable).

The "proprioceptive context dashboard" steal (prior-art 2026-08-11): the seat
should SEE what the house intends to put into its head BEFORE paying for
inference. This module previews the context packet — tier by tier, byte by byte
— with ZERO model calls. The context budget becomes an organ, not an anecdote
(the 427k night, made queryable).

It composes the router (`capability.pipeline.route`) with the presentation tiers
(FULL / EXCERPT / SUMMARY / REFERENCE) and a byte budget: entries are tiered, then
downgraded largest-first until the packet fits. Nothing is read for content and
nothing is decided — it is a projection of what WOULD be presented.

Delete this module and every Mnemosyne semantic is intact.
"""
from __future__ import annotations

from capability.pipeline import route

_TIER_BYTES = {"FULL": 4000, "EXCERPT": 1000, "SUMMARY": 200, "REFERENCE": 80}
_DOWNGRADE_ORDER = ["FULL", "EXCERPT", "SUMMARY", "REFERENCE"]


def _bytes(tier: str) -> int:
    return _TIER_BYTES.get(tier, 80)


def simulate_context(root, goal="", subject=None, intent="DISCOVER",
                     budget=12000) -> dict:
    """Preview the context packet the model WOULD receive, at what tier and cost.
    ZERO model calls."""
    r = route(root, goal, intent=intent, subject=subject or "")
    entries = [{"subject": c["subject"], "kind": c["kind"], "reason": c["reason"],
                "tier": c["tier"], "bytes": _bytes(c["tier"])}
               for c in r["candidates"]]

    def _total():
        return sum(e["bytes"] for e in entries)

    downgraded = []
    total = _total()
    while total > budget:
        changed = False
        for e in sorted(entries, key=lambda x: -x["bytes"]):
            i = _DOWNGRADE_ORDER.index(e["tier"])
            if i < len(_DOWNGRADE_ORDER) - 1:
                downgraded.append({"subject": e["subject"], "from": e["tier"],
                                   "to": _DOWNGRADE_ORDER[i + 1]})
                e["tier"] = _DOWNGRADE_ORDER[i + 1]
                e["bytes"] = _bytes(e["tier"])
                changed = True
                break
        if not changed:
            break
        total = _total()

    return {"goal": goal, "intent": intent, "subject": subject,
            "budget": budget, "total_bytes": total,
            "estimated_tokens": total // 4,
            "entries": entries,
            "downgraded": downgraded,
            "omitted": r.get("available_but_not_routed", []),
            "model_calls": 0,
            "note": "a projection, not authority — no content read, no tokens spent"}
