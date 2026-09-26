"""Phase 9.3 golden task — authorized adaptation (AD-046).

Core question: given an ALLOWed, still-current learning proposal, can Nexus create
exactly one new authoritative memory version with complete provenance, without
rewriting history or allowing authorization to go stale?

    LearningProposal + ALLOW -> AdaptationRunner -> record_if_current
                              -> AdaptationResult -> memory.updated -> new version

Proves:
1. authorized mutation only — only a currently ALLOWed proposal reaches the write path;
2. revalidation — adaptation evaluates against current state; an old ALLOW is not trusted;
3. atomic freshness — the write succeeds only if the target version still equals the
   proposal's expected version at commit time (compare-and-append in the store);
4. append-only adaptation — the prior version stays byte-identical;
5. provenance preservation — the new version traces to proposal + prior version + evidence;
6. scope preservation — scope is not broadened during mutation;
7. exactly-once proposal application — the same proposal_id cannot produce two versions;
8. continuity convergence — ordinary Phase 7 projection exposes the new version.

Run:  py tests/golden/test_phase9_adaptation.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.contracts import (  # noqa: E402
    ActionRequest, AdaptationResult, AgentIdentity, Capability, ModelIdentity,
    PolicyVerdict, Risk, UserIdentity, utcnow,
)
from core.events import EventBus  # noqa: E402
from control.authority import ContinuityAuthority  # noqa: E402
from execution.action import ActionRunner  # noqa: E402
from execution.fake import FakeExecutor  # noqa: E402
from learning.adaptation import AdaptationRunner  # noqa: E402
from learning.analyzer import EvidenceAnalyzer  # noqa: E402
from learning.authority import GroundedLearningAuthority  # noqa: E402
from memory.continuity import ContinuityProjector  # noqa: E402
from memory.memory import MemoryStore  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


def main():
    print("Phase 9.3 golden task: authorized adaptation")
    user = UserIdentity(user_id="alice")
    agent = AgentIdentity(agent_id="researcher", role="researcher", version="1")
    model = ModelIdentity(model_id="model-a", family="fake", version="1")

    store = MemoryStore(os.path.join(tempfile.mkdtemp(), "memory.db"))
    projector = ContinuityProjector()
    t = utcnow()

    # --- authoritative state: SemanticMemory M v1 + ALLOW preference ----------
    store.record("semantic", "brand.palette",
                 content={"subject": "brand", "predicate": "palette_is", "object": "crayon"},
                 scope="project/xyz", provenance={"source": "run/seed"}, now=t)
    store.record("procedure", "coloring_book.production",
                 content={"name": "coloring_book.production",
                          "steps": ["define_concept", "publish"],
                          "constraints": ["brand_rules"]},
                 scope="project/xyz", provenance={"source": "run/seed"}, now=t)
    store.record("preference", "capability.create_book",
                 content={"key": "capability.create_book", "value": "allow"},
                 scope="project/xyz", now=t)
    ncs = projector.project(t, store, user=user, agent=agent, model=model)

    # --- produce authoritative action evidence --------------------------------
    action_authority = ContinuityAuthority([
        Capability(name="create_book", description="publish", risk=Risk.WRITE),
    ])
    bus = EventBus()
    runner = ActionRunner(action_authority, FakeExecutor(), bus)
    result = runner.run(ActionRequest(action_id="action-a", capability="create_book",
                                      scope="project/xyz", requested_by=agent),
                        ncs, run_id="run/1", task_id="task/1")
    evidence = [{"action_id": result.action_id, "outcome": "completed", "run_id": "run/1"}]

    analyzer = EvidenceAnalyzer()
    authority = GroundedLearningAuthority(events=lambda: list(bus.history))
    adaptation = AdaptationRunner(authority, store)

    v1_before = store.get("semantic", "brand.palette")

    # --- 1..2. successful path (revalidated ALLOW -> mutate) ------------------
    proposal_p = analyzer.analyze(
        kind="semantic", target="brand.palette", target_version=1, evidence=evidence,
        scope="project/xyz", proposed_by="agent/researcher@1",
        candidate={"subject": "brand", "predicate": "palette_is", "object": "marker"})
    check(authority.evaluate(proposal_p, ncs).verdict == PolicyVerdict.ALLOW,
          "P is ALLOWed against the current state")
    result_p = adaptation.adapt(proposal_p, ncs)
    check(isinstance(result_p, AdaptationResult), "adaptation returns an AdaptationResult")
    check(result_p.previous_version == 1 and result_p.new_version == 2,
          "P produced exactly one new version (v1 -> v2)")

    v2 = store.get("semantic", "brand.palette")
    check(store.history("semantic", "brand.palette")[0] == v1_before,
          "M v1 is byte-identical (history is never rewritten)")
    check(v2.version == 2 and v2.object == "marker", "M v2 exists with the candidate content")
    check(v2.provenance.get("proposal_id") == proposal_p.proposal_id,
          "v2 provenance references the proposal")
    check(v2.provenance.get("previous_version") == 1, "v2 provenance references the prior version")
    check(v2.provenance.get("evidence") == evidence, "v2 provenance references the evidence")
    check(v2.scope == "project/xyz", "scope is unchanged")

    # --- 8. continuity convergence ---------------------------------------------
    ncs_after = projector.project(utcnow(), store, user=user, agent=agent, model=model)
    check(ncs_after.semantic_memories[0].version == 2
          and ncs_after.semantic_memories[0].object == "marker",
          "ordinary Phase 7 projection now exposes M v2 (no learning-specific path)")

    # --- 7. idempotent replay ---------------------------------------------------
    result_p2 = adaptation.adapt(proposal_p, ncs_after)
    check(result_p2 == result_p, "retrying P returns the SAME AdaptationResult")
    check(store.get("semantic", "brand.palette").version == 2, "no v3 (idempotent)")

    # --- 2..3. stale-authorization race (revalidation + atomic guard) -----------
    proposal_q = analyzer.analyze(
        kind="semantic", target="brand.palette", target_version=2, evidence=evidence,
        scope="project/xyz", proposed_by="agent/researcher@1",
        candidate={"subject": "brand", "predicate": "palette_is", "object": "pen"})
    ncs_v2 = projector.project(utcnow(), store, user=user, agent=agent, model=model)
    check(authority.evaluate(proposal_q, ncs_v2).verdict == PolicyVerdict.ALLOW,
          "Q is initially eligible (targets current v2)")

    t3 = utcnow()
    store.record("semantic", "brand.palette",
                 content={"subject": "brand", "predicate": "palette_is", "object": "pencil"},
                 scope="project/xyz", provenance={"source": "run/other"}, now=t3)
    ncs_v3 = projector.project(t3, store, user=user, agent=agent, model=model)

    check(adaptation.adapt(proposal_q, ncs_v3) is None,
          "revalidation against the current NCS rejects the stale proposal")
    check(adaptation.adapt(proposal_q, ncs_v2) is None,
          "the atomic compare-and-append rejects it even when revalidation passes on a stale NCS")
    check(store.get("semantic", "brand.palette").version == 3,
          "M v3 remains current (no v4 was created)")
    check(proposal_q.target_version == 2, "Q was not rebased")

    # --- non-ALLOW: APPROVAL_REQUIRED and DENY never mutate ----------------------
    proc_proposal = analyzer.analyze(
        kind="procedure", target="coloring_book.production", target_version=1,
        evidence=evidence, scope="project/xyz", proposed_by="agent/researcher@1",
        candidate={"name": "coloring_book.production",
                   "steps": ["define_concept", "preflight", "publish"],
                   "constraints": ["brand_rules"]})
    check(authority.evaluate(proc_proposal, ncs_v3).verdict == PolicyVerdict.APPROVAL_REQUIRED,
          "a Procedure proposal is APPROVAL_REQUIRED under the default policy")
    check(adaptation.adapt(proc_proposal, ncs_v3) is None, "APPROVAL_REQUIRED -> no mutation")
    check(store.get("procedure", "coloring_book.production").version == 1,
          "no new Procedure version")

    invalid = analyzer.analyze(
        kind="semantic", target="brand.palette", target_version=3,
        evidence=[{"action_id": "nonexistent-action"}], scope="project/xyz",
        proposed_by="agent/researcher@1",
        candidate={"subject": "brand", "predicate": "palette_is", "object": "x"})
    check(adaptation.adapt(invalid, ncs_v3) is None, "DENY (fabricated evidence) -> no mutation")
    check(store.get("semantic", "brand.palette").version == 3,
          "no mutation from the invalid proposal")

    store.close()
    print("\nPASS: Phase 9.3 authorized-adaptation boundary holds "
          "(compare-and-append, exactly-once, append-only, provenance-preserving).")


if __name__ == "__main__":
    main()
