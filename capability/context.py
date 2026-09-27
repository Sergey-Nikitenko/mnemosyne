"""Context capability mechanisms — EXPERIMENTAL (speculative, recoverable).

The first three items of the context stack, per the design order:

  2. Context Reasons  — every presented object gets a reason code (why it costs money).
  3. Evidence Bundles — symbol + source + references + dependents + tests + change in
                        ONE deterministic retrieval.
  4. Context Tiers    — FULL / EXCERPT / DELTA / SUMMARY / REFERENCE.

All deterministic (stdlib + workspace_intel only), no LLM, no side effect. They
PROPOSE a presentation; they never decide authority, freshness, or verification truth.
A wrong selection here is recoverable — the model asks for another object.
"""
from __future__ import annotations

from enum import Enum

from execution import workspace_intel as WI


class Reason(str, Enum):
    """Why an object is presented to the model — the "reason-coded byte" contract."""
    GOAL = "GOAL"
    FRONTIER = "FRONTIER"
    DIRECT_TARGET = "DIRECT_TARGET"
    DEPENDENCY = "DEPENDENCY"
    TEST_IMPACT = "TEST_IMPACT"
    RECENT_CHANGE = "RECENT_CHANGE"
    CONTINUITY = "CONTINUITY"
    MODEL_REQUESTED = "MODEL_REQUESTED"
    POLICY_REQUIRED = "POLICY_REQUIRED"
    UNRESOLVED_DECISION = "UNRESOLVED_DECISION"


class Tier(str, Enum):
    FULL = "FULL"
    EXCERPT = "EXCERPT"
    DELTA = "DELTA"
    SUMMARY = "SUMMARY"
    REFERENCE = "REFERENCE"


def evidence_bundle(root: str, symbol: str) -> dict:
    """One deterministic retrieval: everything Nexus mechanically knows about a symbol.

    Returns source + references + callers + calls + related tests + change state under
    a DIRECT_TARGET reason. This collapses the six-call find_symbol → read_file →
    find_references → read_file → related_tests → read_file chain into one call.

    EXPERIMENTAL — speculative selection, recoverable."""
    src = WI.get_symbol_source(root, symbol)
    refs = WI.find_references(root, symbol)
    nb = WI.dependency_neighborhood(root, symbol) or {}
    return {
        "symbol": symbol,
        "reason": Reason.DIRECT_TARGET.value,
        "source": src,
        "references": refs,
        "callers": nb.get("called_by", []),
        "calls": nb.get("calls", []),
        "tests": WI.related_tests(root, symbol=symbol),
        "verification_candidates": WI.verification_candidates(root, symbol),
    }


def context_need(root: str, symbol: str) -> dict:
    """A bounded, explicit query: the model commits to ONE target it needs evidence for.

    Same deterministic retrieval as evidence_bundle, but under a MODEL_REQUESTED
    reason. A bounded explicit query — the model says WHAT it needs; no budget is
    reset and nothing is forced. EXPERIMENTAL — recoverable."""
    b = evidence_bundle(root, symbol)
    b["reason"] = Reason.MODEL_REQUESTED.value
    return b


def tier_presentation(obj: dict, tier: Tier, max_bytes: int = 4000) -> dict:
    """Reduce a presentation to a tier. Deterministic, lossy only by design.

    FULL      -> the object as-is
    EXCERPT   -> first `max_bytes` of the source body
    DELTA     -> only the change list (absent -> empty delta)
    SUMMARY   -> identity + counts, no body
    REFERENCE -> a handle to re-request the object, no body
    """
    t = Tier(tier)
    if t is Tier.FULL:
        return obj
    src = (obj.get("source") or {}).get("source", "") if isinstance(obj, dict) else ""
    if t is Tier.REFERENCE:
        return {"symbol": obj.get("symbol"), "form": "REFERENCE"}
    if t is Tier.EXCERPT:
        return {"symbol": obj.get("symbol"), "form": "EXCERPT", "excerpt": (src or "")[:max_bytes]}
    if t is Tier.DELTA:
        return {"symbol": obj.get("symbol"), "form": "DELTA", "delta": obj.get("change", [])}
    if t is Tier.SUMMARY:
        return {"symbol": obj.get("symbol"), "form": "SUMMARY",
                "references": len(obj.get("references", [])),
                "callers": len(obj.get("callers", [])),
                "calls": len(obj.get("calls", [])),
                "tests": len(obj.get("tests", []))}
    return obj
