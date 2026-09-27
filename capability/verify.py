"""Verification Fabric — EXPERIMENTAL (speculative, recoverable).

Toy #4: cheapest-sufficient verification with escalation, returning FACTS rather
than an 8,000-character terminal dump.

    VERIFY(symbol="score_event")
      -> level RELATED (direct tests)
      -> if they pass AND the change is cross-module/public, escalate to FULL
      -> VerificationResult { status, level, passed, failed, duration,
                              failures, implicated, escalated }

The actual test run is DELEGATED to the trusted `run_verification` tool (the
execution plane), so verification truth stays in the trusted core — this module
only CHOOSES the cheapest sufficient level and REDUCES the result to facts.
Deterministic, no authority, no side effect of its own. Delete it and every
Mnemosyne semantic is intact.
"""
from __future__ import annotations

from capability.toys import blast_radius


def _summarize(tool_result) -> dict:
    out = (tool_result.output or {}) if tool_result is not None else {}
    if not isinstance(out, dict):
        return {"passed": 0, "failed": 0, "duration": 0, "failures": [],
                "summary": str(out)}
    return {"passed": out.get("passed", 0), "failed": out.get("failed", 0),
            "duration": out.get("duration", 0),
            "failures": out.get("failed_locations", []) or [],
            "summary": out.get("summary", "")}


def verify_fabric(root, symbol=None, run=None) -> dict:
    """run(scope, symbol) -> ToolResult is the trusted run_verification seam."""
    def _run(scope, sym):
        return run(scope, sym) if run is not None else None

    if symbol:
        first = _run("related", symbol)
        if first is None:
            return {"status": "NOT_RUN", "level": "RELATED",
                    "note": "run_verification not wired"}
        passed, level, escalated, summary = first.success, "RELATED", False, _summarize(first)
        if passed:
            # cheapest-sufficient escalation, keyed on FACTS not a display label:
            # related tests suffice only when nothing else references the symbol.
            if blast_radius(root, symbol).get("direct_dependents", 0) >= 1:
                second = _run("full", None)
                if second is not None:
                    passed, level, escalated, summary = second.success, "FULL", True, _summarize(second)
        # attribution is honest: only a RELATED-level failure implicates the
        # named symbol; a full-suite failure is not attributable to it.
        implicated = [symbol] if (not passed and level == "RELATED") else None
        note = None if (not passed or level == "RELATED") else \
            f"failure is in the full suite, not attributable to {symbol}"
        return {"status": "PASS" if passed else "FAIL", "level": level,
                "passed": summary["passed"], "failed": summary["failed"],
                "duration": summary["duration"], "failures": summary["failures"],
                "implicated": implicated, "escalated": escalated, "note": note,
                "summary": summary["summary"]}
    first = _run("full", None)
    if first is None:
        return {"status": "NOT_RUN", "level": "FULL", "note": "run_verification not wired"}
    summary = _summarize(first)
    return {"status": "PASS" if first.success else "FAIL", "level": "FULL",
            "passed": summary["passed"], "failed": summary["failed"],
            "duration": summary["duration"], "failures": summary["failures"],
            "implicated": [], "escalated": False, "summary": summary["summary"]}
