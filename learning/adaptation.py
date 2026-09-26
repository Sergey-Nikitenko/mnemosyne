"""Authorized adaptation — the mutation boundary (AD-046).

The AdaptationRunner composes a LearningAuthority (revalidates) + a MemoryStore
(compare-and-append). It applies a currently-ALLOWed proposal as exactly one new
authoritative memory version, with provenance, and never trusts a previously
computed ALLOW.

    LearningProposal -> AdaptationRunner -> (revalidate) -> record_if_current
                     -> AdaptationResult

Idempotency is proven by memory provenance — no second store, no adaptation event:
a proposal_id already present in the target's history returns the existing result.
"""
from __future__ import annotations

from core.contracts import AdaptationResult, LearningProposal, PolicyVerdict


class AdaptationRunner:
    """Composes authority + store; it adds no intelligence (9.1 already analyzed)."""

    def __init__(self, authority, store) -> None:
        self.authority = authority  # LearningAuthority protocol: evaluate(proposal, ncs)
        self.store = store          # MemoryStore: record_if_current(...) + history(...)

    def adapt(self, proposal: LearningProposal, ncs) -> AdaptationResult | None:
        existing = self._find_existing(proposal)
        if existing is not None:
            return existing
        verdict = self.authority.evaluate(proposal, ncs)
        if verdict.verdict != PolicyVerdict.ALLOW:
            # DENY / APPROVAL_REQUIRED: the verdict is authoritative — no mutation.
            return None
        record = self.store.record_if_current(
            proposal.kind, proposal.target, dict(proposal.proposed_change),
            expected_version=proposal.target_version,
            scope=proposal.scope,
            provenance={"proposal_id": proposal.proposal_id,
                        "previous_version": proposal.target_version,
                        "evidence": [dict(e) for e in proposal.evidence]},
        )
        if record is None:
            return None  # stale: the target advanced past the proposal's version
        return self._result(proposal, record)

    def _find_existing(self, proposal):
        for rec in self.store.history(proposal.kind, proposal.target):
            if rec.provenance.get("proposal_id") == proposal.proposal_id:
                return self._result(proposal, rec)
        return None

    @staticmethod
    def _result(proposal, record):
        return AdaptationResult(
            proposal_id=proposal.proposal_id,
            memory_id=proposal.target,
            previous_version=proposal.target_version,
            new_version=record.version,
            kind=proposal.kind,
            scope=record.scope,
            provenance={"proposal_id": proposal.proposal_id,
                        "evidence": [dict(e) for e in proposal.evidence]},
        )
