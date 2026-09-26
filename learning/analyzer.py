"""Learning analyzer (reference) — authoritative experience -> LearningProposal (AD-044).

The learner OBSERVES authoritative experience and produces a bounded candidate
adaptation. It is deterministic, non-LLM, and side-effect-free: it never reads or
writes a MemoryStore, never mutates the NCS, never emits an event, never touches
identity or history. Its output is a value object — a PROPOSAL, not knowledge.

    authoritative evidence -> EvidenceAnalyzer -> LearningProposal -> X (no mutation)
"""
from __future__ import annotations

from core.contracts import LearningProposal


class EvidenceAnalyzer:
    """The reference, deterministic learner (a test double stands in for any model).

    It packages authoritative evidence into a LearningProposal without reasoning
    or mutation. `proposed_change` is a candidate revision derived from the
    observed evidence — data, never authoritative state."""

    def analyze(self, *, kind: str, target: str, target_version: int,
                evidence: list[dict], scope: str = "",
                proposed_by: str = "") -> LearningProposal:
        return LearningProposal(
            kind=kind,
            target=target,
            target_version=target_version,
            proposed_change={
                "candidate": f"revise {kind} '{target}' observed at v{target_version}",
                "evidence_count": len(evidence),
            },
            evidence=[dict(e) for e in evidence],
            scope=scope,
            proposed_by=proposed_by,
        )
