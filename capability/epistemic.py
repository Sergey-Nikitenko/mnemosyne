"""Epistemic Output Contract — EXPERIMENTAL (speculative, recoverable).

Formalizes what the coherence report called the A1–A9 smell: several
deterministic tools return something STRONGER than what they actually know.
This module gives every capability field a declared epistemic strength, and the
law that governs it:

    A weaker epistemic class may INFORM a stronger decision, but may never
    MASQUERADE as one. In particular HEURISTIC may never directly become
    AUTHORITY or VERIFICATION TRUTH.

Levels:
    FACT          measured by a tool / pinned by a test — reproachable
    MEASUREMENT   a deterministic computation over observed state (a count/hash)
    HEURISTIC     a deterministic but lossy/simplifying rule
    INFERENCE     a conclusion drawn from facts; overridable
    CANDIDATE     a proposal, not yet established
    UNKNOWN       no source yet — never guessed

Delete this module and every Mnemosyne semantic is intact; the annotation table
is documentation that `capability_explain` and `house_consistency_check` render.
"""
from __future__ import annotations

from enum import Enum


class Epistemic(str, Enum):
    FACT = "FACT"
    MEASUREMENT = "MEASUREMENT"
    HEURISTIC = "HEURISTIC"
    INFERENCE = "INFERENCE"
    CANDIDATE = "CANDIDATE"
    UNKNOWN = "UNKNOWN"


# tool -> field -> epistemic strength. Fields absent here default to MEASUREMENT
# (a deterministic computation) unless a caller says otherwise.
FIELD_EPISTEMICS: dict[str, dict[str, str]] = {
    "verify_fabric": {
        "status": Epistemic.FACT, "level": Epistemic.FACT,
        "passed": Epistemic.FACT, "failed": Epistemic.FACT,
        "failures": Epistemic.FACT, "escalated": Epistemic.FACT,
        "implicated": Epistemic.HEURISTIC,
    },
    "blast_radius": {
        "direct_dependents": Epistemic.MEASUREMENT,
        "second_order_dependents": Epistemic.MEASUREMENT,
        "risk_shape": Epistemic.HEURISTIC,
    },
    "import_health": {
        "possibly_missing": Epistemic.CANDIDATE, "unused": Epistemic.CANDIDATE,
    },
    "test_for": {"direct": Epistemic.HEURISTIC, "indirect": Epistemic.HEURISTIC},
    "imports_for": {"canonical": Epistemic.HEURISTIC, "collision": Epistemic.MEASUREMENT},
    "world_delta": {
        "files": Epistemic.MEASUREMENT, "symbols": Epistemic.MEASUREMENT,
        "decisions_invalidated": Epistemic.UNKNOWN,
    },
    "work_capsule": {
        "goal": Epistemic.MEASUREMENT, "frontier": Epistemic.MEASUREMENT,
        "verification_debt": Epistemic.MEASUREMENT, "declared_decisions": Epistemic.UNKNOWN,
    },
    "route": {"candidates": Epistemic.CANDIDATE},
}


def epistemic(tool: str, field: str) -> str:
    """The declared strength of a field; MEASUREMENT when not annotated."""
    return FIELD_EPISTEMICS.get(tool, {}).get(field, Epistemic.MEASUREMENT)


def annotate(tool: str, output: dict) -> dict:
    """Attach each present field's epistemic class to a tool output."""
    if not isinstance(output, dict):
        return {"_value": output, "_epistemic": Epistemic.MEASUREMENT}
    return {k: {"value": v, "epistemic": epistemic(tool, k)} for k, v in output.items()}
