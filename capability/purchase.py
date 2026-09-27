"""Evidence Purchase — EXPERIMENTAL (speculative, recoverable).

The coherence layer's repetition fix (C1). The old RepetitionGate thinks in
TOOL names (`read_file(foo.py)` three times); but the model can buy materially
equivalent information through different doors:

    peek_symbol(X)  ->  signature(X)  ->  imports_for(X)  ->  get_symbol_source(X)

all purchase one SYMBOL_IDENTITY for X. This module keys repetition on
(subject, evidence class) instead of tool name, and FLAGS a repurchase so the
presentation can be compacted — it never refuses a read.

    EvidencePurchase
        subject:   symbol or path
        class:     SYMBOL_IDENTITY | SYMBOL_SOURCE | REFERENCES | TESTS |
                   CHANGE | STATE
        projection: the tool name
        prior_purchases: count since the last world mutation

Deterministic, in-memory per process, reset on world mutation. Delete this
module and every Mnemosyne semantic is intact (repurchase marking disappears).
"""
from __future__ import annotations

EVIDENCE_CLASS = {
    "peek_symbol": "SYMBOL_IDENTITY", "signature": "SYMBOL_IDENTITY",
    "imports_for": "SYMBOL_IDENTITY", "file_outline": "SYMBOL_IDENTITY",
    "get_symbol_source": "SYMBOL_SOURCE", "evidence_bundle": "SYMBOL_SOURCE",
    "context_need": "SYMBOL_SOURCE", "decision_context": "SYMBOL_SOURCE",
    "dependency_neighborhood": "SYMBOL_SOURCE",
    "find_references": "REFERENCES", "what_uses_this": "REFERENCES",
    "call_examples": "REFERENCES", "blast_radius": "REFERENCES",
    "related_tests": "TESTS", "test_for": "TESTS", "verification_candidates": "TESTS",
    "symbol_history": "CHANGE", "why_stale": "CHANGE", "changed_since": "CHANGE",
    "world_delta": "CHANGE", "what_changed": "CHANGE",
    "work_capsule": "STATE", "where_am_i": "STATE",
    "next_mechanical_options": "STATE",
}

SUBJECT_ARG = {
    "peek_symbol": "name", "signature": "name", "imports_for": "symbol",
    "file_outline": "path", "get_symbol_source": "symbol", "evidence_bundle": "symbol",
    "context_need": "symbol", "decision_context": "decision",
    "dependency_neighborhood": "symbol", "find_references": "symbol",
    "what_uses_this": "target", "call_examples": "name", "blast_radius": "target",
    "related_tests": "symbol", "test_for": "target",
    "verification_candidates": "path_or_symbol", "symbol_history": "name",
    "why_stale": "target", "changed_since": "", "world_delta": "",
    "what_changed": "", "work_capsule": "", "where_am_i": "",
    "next_mechanical_options": "",
}

# tools whose success changes the WORLD (and thus re-opens all evidence)
WORLD_MUTATORS = frozenset({"edit_file", "write_file", "patch_symbol",
                            "run_elevated", "run_command", "execute_intent"})


def evidence_class(tool: str) -> str:
    return EVIDENCE_CLASS.get(tool, "")


def subject_of(tool: str, args: dict) -> str:
    key = SUBJECT_ARG.get(tool)
    if not key:
        return ""
    return str((args or {}).get(key, "") or "")


class PurchaseGate:
    """Flags materially-equivalent repurchases, keyed on (class, subject).

    Non-coercive (post Progress Obligation retirement): it never REJECTS a read.
    It only MARKS a repurchase so the presentation can be compacted — the
    mechanical response to "same evidence again" is cheaper presentation, not a
    blocked door. A genuine resource ceiling (max rounds / timeout) is the only
    thing that terminates a run, and that lives in the orchestrator, not here."""

    def __init__(self) -> None:
        self._counts: dict = {}

    def verdict(self, tool: str, args: dict) -> str:
        """admit | flag (never reject)."""
        subject = subject_of(tool, args)
        cls = evidence_class(tool)
        if not subject or not cls:
            return "admit"  # no meaningful subject -> not an evidence purchase
        key = (cls, subject)
        n = self._counts.get(key, 0) + 1
        self._counts[key] = n
        return "flag" if n >= 2 else "admit"

    def prior(self, tool: str, args: dict) -> int:
        return self._counts.get((evidence_class(tool), subject_of(tool, args)), 0)

    def reset(self) -> None:
        self._counts.clear()
