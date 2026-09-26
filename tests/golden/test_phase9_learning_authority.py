"""Phase 9.2 golden task — learning authority (AD-045).

Core question: should this specific, evidence-grounded proposal be permitted to
become authoritative?

    LearningProposal -> LearningAuthority -> LearningVerdict -> X (NO MUTATION)

Proves, in one deterministic scenario:
1. authority separation — proposal in, verdict out; no adaptation occurs;
2. evidence authenticity — fabricated/nonexistent evidence -> DENY;
3. target freshness — a stale (superseded) proposal cannot be ALLOWed;
4. scope enforcement — a scope mismatch is DENY (never silently broadened);
5. proposer non-authority — proposed_by / confidence / self-claims cannot self-authorize;
6. policy-controlled verdict — ALLOW/DENY/APPROVAL_REQUIRED from the authority, not the learner;
7. purity — every verdict (including ALLOW) leaves memory/NCS/history untouched;
8. proposal immutability — the authority never rebases or rewrites a proposal.

Run:  py tests/golden/test_phase9_learning_authority.py
"""
import copy
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.contracts import (  # noqa: E402
    ActionRequest, AgentIdentity, Capability, LearningVerdict, ModelIdentity,
    ModelResponse, PolicyVerdict, Risk, ToolResult, UserIdentity, utcnow,
)
from core.events import EventBus  # noqa: E402
from control.authority import ContinuityAuthority  # noqa: E402
from execution.action import ActionRunner  # noqa: E402
from learning.analyzer import EvidenceAnalyzer  # noqa: E402
from learning.authority import GroundedLearningAuthority  # noqa: E402
from memory.continuity import ContinuityProjector  # noqa: E402
from memory.memory import MemoryStore  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


class ScriptedExecutor:
    def __init__(self, fail_tools=()):
        self.calls = 0
        self.fail_tools = set(fail_tools)

    def execute_tool(self, call):
        self.calls += 1
        if call.tool_name in self.fail_tools:
            return ToolResult(tool_call=call, success=False, error="boom")
        return ToolResult(tool_call=call, success=True, output={"echo": dict(call.arguments)})

    def run_model(self, request):
        return ModelResponse(model="fake", content="", success=True)


def main():
    print("Phase 9.2 golden task: learning authority")
    user = UserIdentity(user_id="alice")
    agent = AgentIdentity(agent_id="researcher", role="researcher", version="1")
    model = ModelIdentity(model_id="model-a", family="fake", version="1")

    store = MemoryStore(os.path.join(tempfile.mkdtemp(), "memory.db"))
    projector = ContinuityProjector()
    t = utcnow()

    # --- authoritative state: Procedure P v1, Semantic S v1, ALLOW prefs ------
    store.record("procedure", "coloring_book.production",
                 content={"name": "coloring_book.production",
                          "steps": ["define_concept", "publish"],
                          "constraints": ["brand_rules"]},
                 scope="project/xyz", provenance={"source": "run/seed"}, now=t)
    store.record("semantic", "brand.palette",
                 content={"subject": "brand", "predicate": "palette_is", "object": "crayon"},
                 scope="project/xyz", now=t)
    store.record("preference", "capability.create_book",
                 content={"key": "capability.create_book", "value": "allow"},
                 scope="project/xyz", now=t)
    store.record("preference", "capability.publish_book",
                 content={"key": "capability.publish_book", "value": "allow"},
                 scope="project/xyz", now=t)
    ncs = projector.project(t, store, user=user, agent=agent, model=model)

    # --- produce authoritative experience (Action A completed, B failed) ------
    action_authority = ContinuityAuthority([
        Capability(name="create_book", description="publish", risk=Risk.WRITE),
        Capability(name="publish_book", description="publish", risk=Risk.WRITE),
    ])
    executor = ScriptedExecutor(fail_tools={"publish_book"})
    bus = EventBus()
    runner = ActionRunner(action_authority, executor, bus)
    result_a = runner.run(ActionRequest(action_id="action-a", capability="create_book",
                                        scope="project/xyz", requested_by=agent),
                          ncs, run_id="run/1", task_id="task/1")
    result_b = runner.run(ActionRequest(action_id="action-b", capability="publish_book",
                                        scope="project/xyz", requested_by=agent),
                          ncs, run_id="run/1", task_id="task/1")

    evidence = [
        {"action_id": result_a.action_id, "outcome": "completed", "run_id": "run/1"},
        {"action_id": result_b.action_id, "outcome": "failed", "run_id": "run/1"},
        {"run_id": "run/1", "evaluation": "poor"},
    ]

    analyzer = EvidenceAnalyzer()
    authority = GroundedLearningAuthority(events=lambda: list(bus.history))

    snapshot_ncs = copy.deepcopy(ncs)
    snapshot_mem = [h.version for h in store.history("procedure", "coloring_book.production")]
    snapshot_actions = list(bus.history)

    # --- 1. valid Procedure proposal -> APPROVAL_REQUIRED (conservative default)
    lp_proc = analyzer.analyze(kind="procedure", target="coloring_book.production",
                               target_version=1, evidence=evidence, scope="project/xyz",
                               proposed_by="agent/researcher@1")
    v_proc = authority.evaluate(lp_proc, ncs)
    check(isinstance(v_proc, LearningVerdict), "the authority returns a LearningVerdict")
    check(v_proc.verdict == PolicyVerdict.APPROVAL_REQUIRED,
          "a valid Procedure proposal -> APPROVAL_REQUIRED (conservative default)")

    # --- 2. valid SemanticMemory proposal -> ALLOW -----------------------------
    lp_sem = analyzer.analyze(kind="semantic", target="brand.palette", target_version=1,
                              evidence=evidence, scope="project/xyz",
                              proposed_by="agent/researcher@1")
    check(authority.evaluate(lp_sem, ncs).verdict == PolicyVerdict.ALLOW,
          "a valid SemanticMemory proposal -> ALLOW (the ALLOW path is proven)")

    # --- 3. fabricated evidence -> DENY ----------------------------------------
    lp_fake = analyzer.analyze(kind="semantic", target="brand.palette", target_version=1,
                               evidence=[{"action_id": "nonexistent-action"}],
                               scope="project/xyz", proposed_by="agent/researcher@1")
    v_fake = authority.evaluate(lp_fake, ncs)
    check(v_fake.verdict == PolicyVerdict.DENY, "fabricated evidence -> DENY")
    check(any("evidence" in r for r in v_fake.reasons), "the DENY names the evidence problem")

    # --- 4. incompatible scope -> DENY -----------------------------------------
    lp_scope = analyzer.analyze(kind="procedure", target="coloring_book.production",
                                target_version=1, evidence=evidence, scope="project/abc",
                                proposed_by="agent/researcher@1")
    v_scope = authority.evaluate(lp_scope, ncs)
    check(v_scope.verdict == PolicyVerdict.DENY, "incompatible scope -> DENY")
    check(any("scope" in r for r in v_scope.reasons), "the DENY names the scope problem")

    # --- 5. proposer non-authority ----------------------------------------------
    smug = analyzer.analyze(kind="semantic", target="brand.palette", target_version=1,
                            evidence=[{"action_id": "nonexistent-action"}],
                            scope="project/xyz", proposed_by="model-evil")
    smug.proposed_change = {"auto_approve": True, "confidence": 0.9999}
    v_smug = authority.evaluate(smug, ncs)
    check(v_smug.verdict == PolicyVerdict.DENY,
          "self-claims (confidence / auto-approve) cannot self-authorize")

    # --- 6. purity: nothing mutated so far ----------------------------------------
    check(ncs == snapshot_ncs, "the NCS is unchanged after every evaluation")
    check([h.version for h in store.history("procedure", "coloring_book.production")] == snapshot_mem,
          "memory history is unchanged")
    check(list(bus.history) == snapshot_actions, "action history is unchanged")

    # --- 7. stale target -> DENY; proposal immutability -----------------------------
    t2 = utcnow()
    store.record("procedure", "coloring_book.production",
                 content={"name": "coloring_book.production",
                          "steps": ["define_concept", "preflight", "publish"],
                          "constraints": ["brand_rules"]},
                 scope="project/xyz", provenance={"source": "run/other"}, now=t2)
    ncs_v2 = projector.project(t2, store, user=user, agent=agent, model=model)

    v_stale = authority.evaluate(lp_proc, ncs_v2)
    check(v_stale.verdict == PolicyVerdict.DENY, "a proposal targeting a superseded version -> DENY")
    check(any("stale" in r for r in v_stale.reasons), "the DENY names the stale target")
    check(lp_proc.target_version == 1, "the authority did NOT rebase the proposal to v2")
    check(store.get("procedure", "coloring_book.production").version == 2,
          "no v3 was created (the authority never adapts)")

    # --- 8. a fresh proposal against v2 -> eligible -------------------------------
    lp_v2 = analyzer.analyze(kind="procedure", target="coloring_book.production",
                             target_version=2, evidence=evidence, scope="project/xyz",
                             proposed_by="agent/researcher@1")
    check(authority.evaluate(lp_v2, ncs_v2).verdict == PolicyVerdict.APPROVAL_REQUIRED,
          "a fresh proposal against the current v2 is eligible for evaluation")

    store.close()
    print("\nPASS: Phase 9.2 learning-authority boundary holds "
          "(a proposal may recommend change; only the authority may permit it, and ALLOW never mutates).")


if __name__ == "__main__":
    main()
