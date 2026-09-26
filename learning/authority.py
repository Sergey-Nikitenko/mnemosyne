"""Learning authority (reference) — LearningProposal -> LearningVerdict (AD-045).

The authority decides whether a proposal may be authorized — it never applies it.
It evaluates a proposal's evidence against authoritative history, its target
against the current NCS (freshness + scope), and the kind against policy. It is
pure: an ALLOW verdict is permission, never mutation.

    LearningProposal -> GroundedLearningAuthority -> LearningVerdict -> X (no mutation)

Hard invariants:
- evidence authenticity — a cited action_id that does not reference a real,
  authoritative action event is DENY (a proposal cannot self-authorize);
- target freshness — a proposal whose target version is no longer current is DENY
  (stale_target), never silently rebased;
- scope — a proposal must be scoped to the target's exact scope (never broadened);
- policy — ALLOW/DENY/APPROVAL_REQUIRED come from the kind-based policy, never the
  proposer's identity or the proposal's self-claims.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from core.contracts import (
    LearningProposal, LearningVerdict, NexusContinuityState, PolicyVerdict,
)
from core.events import EventType

_TERMINAL = {EventType.ACTION_COMPLETED, EventType.ACTION_FAILED}


@dataclass
class LearningPolicyRules:
    """The authority's kind-based policy (conservative default, configurable)."""
    version: str = "learning-policy@1"
    verdict_by_kind: dict[str, PolicyVerdict] = field(default_factory=lambda: {
        "semantic": PolicyVerdict.ALLOW,
        "procedure": PolicyVerdict.APPROVAL_REQUIRED,
        "preference": PolicyVerdict.APPROVAL_REQUIRED,
    })


class GroundedLearningAuthority:
    """A pure authority grounded in authoritative history + the current NCS."""

    def __init__(self, rules=None, events=None) -> None:
        self._rules = rules or LearningPolicyRules()
        self._events = events  # list[Event] or callable -> list[Event]

    def _event_list(self):
        if self._events is None:
            return []
        return self._events() if callable(self._events) else self._events

    def evaluate(self, proposal: LearningProposal,
                 ncs: NexusContinuityState) -> LearningVerdict:
        # 1. evidence authenticity (against authoritative history)
        if not self._authentic(proposal.evidence):
            return LearningVerdict(PolicyVerdict.DENY, ["fabricated or unverifiable evidence"])
        # 2. target freshness (against the current NCS)
        target = self._find_target(proposal, ncs)
        if target is None:
            return LearningVerdict(PolicyVerdict.DENY, ["unknown target"])
        if target.version != proposal.target_version:
            return LearningVerdict(PolicyVerdict.DENY,
                                   [f"stale_target: current v{target.version}"])
        # 3. scope (never broaden)
        if proposal.scope != target.scope:
            return LearningVerdict(PolicyVerdict.DENY,
                                   ["scope_mismatch", f"target scope '{target.scope}'"])
        # 4. kind-based policy
        verdict = self._rules.verdict_by_kind.get(proposal.kind, PolicyVerdict.DENY)
        return LearningVerdict(verdict, [f"kind={proposal.kind}"])

    def _authentic(self, evidence) -> bool:
        if not evidence:
            return False
        real_ids = {ev.payload.get("action_id") for ev in self._event_list()
                    if ev.event_type in _TERMINAL and ev.payload.get("action_id")}
        for item in evidence:
            if "action_id" in item and item["action_id"] not in real_ids:
                return False
        return True

    @staticmethod
    def _find_target(proposal, ncs):
        records = {
            "procedure": ncs.procedures,
            "semantic": ncs.semantic_memories,
            "preference": ncs.preferences,
        }.get(proposal.kind, [])
        for rec in records:
            if rec.memory_id == proposal.target:
                return rec
        return None
