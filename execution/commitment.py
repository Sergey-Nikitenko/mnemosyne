"""Cost telemetry — deterministic OBSERVATION, never coercion.

Retired: the Progress Obligation / CommitmentGate. That experiment turned
exploration into pressure to mutate ("25 discovery ops -> commit or be blocked"),
which could distort the model's trajectory and reward economically-but-not-
semantically meaningful mutations. The lesson is preserved in OBSERVATIONS.md.

What remains is OBSERVATION, not governance: classify each tool call and count
it, so cost can be DIAGNOSED. The durable event log already carries the raw
telemetry (`tool.requested` / `tool.completed`); `CostTelemetry` only classifies.
It never rejects a tool, never resets a budget, and never manufactures urgency.
Deleting this module's coercion (already removed) left every semantic intact;
what is left is a pure counter.
"""
from __future__ import annotations

DISCOVERY_TOOLS = frozenset({
    "read_file", "list_dir", "workspace_map", "find_symbol", "find_references",
    "related_tests", "get_symbol_source", "dependency_neighborhood",
    "verification_candidates", "changed_since",
    "evidence_bundle", "route", "change_context", "decision_context",
    "context_packet", "compile_packet",
    "peek_symbol", "file_outline", "symbol_history", "why_stale",
    "what_uses_this", "blast_radius", "imports_for", "import_health",
    "signature", "call_examples", "test_for", "failure_focus", "show_contract",
    "explain_rejection", "next_mechanical_options", "where_am_i", "what_changed",
    "why_is_this_here", "resolve_intent", "world_delta", "decision_status",
    "capability_explain", "house_consistency_check", "work_capsule",
    "verify_claim", "simulate_context",
})
QUERY_TOOLS = frozenset({"context_need"})
MUTATION_TOOLS = frozenset({"edit_file", "write_file", "patch_symbol",
                            "execute_intent", "run_elevated", "record_decision",
                            "reaffirm_decision", "retire_decision"})
VERIFY_TOOLS = frozenset({"run_verification", "verify_fabric"})


class CostTelemetry:
    """A pure counter over tool calls — the telemetry half that survived the
    Progress Obligation retirement. Observe, never govern."""

    def __init__(self) -> None:
        self.evidence_ops = 0
        self.mutations = 0
        self.verifications = 0
        self.failed = 0

    def observe(self, tool: str, success: bool) -> None:
        if tool in MUTATION_TOOLS:
            self.mutations += 1
        elif tool in VERIFY_TOOLS:
            self.verifications += 1
        elif tool in DISCOVERY_TOOLS or tool in QUERY_TOOLS:
            self.evidence_ops += 1
        if not success:
            self.failed += 1

    def snapshot(self) -> dict:
        return {"evidence_ops": self.evidence_ops, "mutations": self.mutations,
                "verifications": self.verifications, "failed": self.failed}
