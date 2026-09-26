"""Phase 9.1 golden task — learning proposals (AD-044).

Core question: can Nexus derive a proposed adaptation from authoritative
experience WITHOUT changing authoritative memory, identity, policy, or history?

    authoritative experience -> observe -> LearningProposal -> X (NO MUTATION)

Proves, in one deterministic procedure-adaptation scenario:
1. proposal, not mutation — a LearningProposal is produced; nothing mutates;
2. evidence-grounded      — the proposal cites Nexus-owned authoritative artifacts;
3. target-versioned       — it names the exact Procedure version observed;
4. scope-preserving       — project scope is carried, never silently global;
5. model non-authority    — the proposer is an interpreter, never authority;
6. continuity isolation   — the candidate never enters the NCS or memory retrieval;
7. temporal stability     — a later legitimate v2 does not retarget the proposal.

Run:  py tests/golden/test_phase9_learning_proposal.py
"""
import copy
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.contracts import (  # noqa: E402
    ActionRequest, AgentIdentity, Capability, LearningProposal, ModelIdentity,
    ModelResponse, Risk, ToolResult, UserIdentity, utcnow,
)
from core.events import EventBus  # noqa: E402
from control.authority import ContinuityAuthority  # noqa: E402
from execution.action import ActionRunner  # noqa: E402
from execution.fake import FakeExecutor  # noqa: E402
from learning.analyzer import EvidenceAnalyzer  # noqa: E402
from memory.continuity import ContinuityProjector  # noqa: E402
from memory.memory import MemoryStore  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


class SuccessExecutor:
    def __init__(self):
        self.calls = 0
        self._inner = FakeExecutor()

    def execute_tool(self, call):
        self.calls += 1
        return self._inner.execute_tool(call)

    def run_model(self, request):
        return self._inner.run_model(request)


class FailingExecutor:
    def __init__(self):
        self.calls = 0

    def execute_tool(self, call):
        self.calls += 1
        return ToolResult(tool_call=call, success=False, error="boom")

    def run_model(self, request):
        return ModelResponse(model="fake", content="", success=True)


def main():
    print("Phase 9.1 golden task: learning proposals")
    user = UserIdentity(user_id="alice")
    agent = AgentIdentity(agent_id="researcher", role="researcher", version="1")
    model = ModelIdentity(model_id="model-a", family="fake", version="1")

    store = MemoryStore(os.path.join(tempfile.mkdtemp(), "memory.db"))
    projector = ContinuityProjector()
    t = utcnow()

    # --- establish Procedure P v1 + the ALLOW preference, in one store ---------
    store.record("procedure", "coloring_book.production",
                 content={"name": "coloring_book.production",
                          "steps": ["define_concept", "publish"],
                          "constraints": ["brand_rules"]},
                 scope="project/xyz", provenance={"source": "run/seed"}, now=t)
    store.record("preference", "capability.create_book",
                 content={"key": "capability.create_book", "value": "allow"},
                 scope="project/xyz", now=t)
    ncs = projector.project(t, store, user=user, agent=agent, model=model)

    # --- produce authoritative experience: Action A completed, Action B failed --
    authority = ContinuityAuthority([
        Capability(name="create_book", description="publish", risk=Risk.WRITE),
    ])
    ok_exec = SuccessExecutor()
    bus = EventBus()
    runner = ActionRunner(authority, ok_exec, bus)
    result_a = runner.run(
        ActionRequest(action_id="action-a", capability="create_book",
                      scope="project/xyz", requested_by=agent),
        ncs, run_id="run/1", task_id="task/1")

    fail_exec = FailingExecutor()
    fail_bus = EventBus()
    fail_runner = ActionRunner(authority, fail_exec, fail_bus)
    result_b = fail_runner.run(
        ActionRequest(action_id="action-b", capability="create_book",
                      scope="project/xyz", requested_by=agent),
        ncs, run_id="run/1", task_id="task/1")

    evidence = [
        {"action_id": result_a.action_id, "outcome": "completed", "run_id": "run/1"},
        {"action_id": result_b.action_id, "outcome": "failed", "run_id": "run/1"},
        {"run_id": "run/1", "evaluation": "poor"},
    ]

    # --- snapshot BEFORE proposal generation -----------------------------------
    ncs_before = projector.project(t, store, user=user, agent=agent, model=model)
    snapshot_ncs = copy.deepcopy(ncs_before)
    snapshot_mem = [h.version for h in store.history("procedure", "coloring_book.production")]
    snapshot_actions = list(bus.history) + list(fail_bus.history)

    # --- observe -> proposal ----------------------------------------------------
    analyzer = EvidenceAnalyzer()
    lp = analyzer.analyze(
        kind="procedure", target="coloring_book.production", target_version=1,
        evidence=evidence, scope="project/xyz", proposed_by="agent/researcher@1",
    )

    # --- 1. proposal, not mutation ----------------------------------------------
    check(isinstance(lp, LearningProposal), "the analyzer returns a LearningProposal")
    check(lp.proposal_id.startswith("lrn_"), "the proposal has a Nexus-owned identity")
    check(ncs_before == snapshot_ncs, "the NCS is unchanged")
    check([h.version for h in store.history("procedure", "coloring_book.production")] == snapshot_mem,
          "memory history is unchanged (still only v1)")
    check(list(bus.history) + list(fail_bus.history) == snapshot_actions,
          "action history is unchanged")

    # --- 2. evidence-grounded ----------------------------------------------------
    check(lp.evidence == evidence, "the proposal cites the authoritative evidence")
    check(any(e.get("action_id") == result_b.action_id for e in lp.evidence),
          "the evidence references a Nexus-owned action id")

    # --- 3. target-versioned ------------------------------------------------------
    check(lp.kind == "procedure" and lp.target == "coloring_book.production",
          "the proposal targets the procedure")
    check(lp.target_version == 1, "the proposal names the EXACT observed version (v1)")

    # --- 4. scope-preserving --------------------------------------------------------
    check(lp.scope == "project/xyz", "the proposal carries the project scope")

    # --- 5. model non-authority ------------------------------------------------------
    check(lp.proposed_by == "agent/researcher@1", "the proposer is the interpreter")
    check(bool(lp.proposed_change), "a candidate change is proposed (data, never authority)")

    # --- 6. continuity isolation -------------------------------------------------------
    ncs_after = projector.project(t, store, user=user, agent=agent, model=model)
    check(ncs_after == ncs_before, "the proposal does not enter the NCS")
    check(len(ncs_after.procedures) == 1 and ncs_after.procedures[0].version == 1,
          "Procedure P is still v1 — the candidate is not accepted knowledge")
    candidate_text = lp.proposed_change.get("candidate", "")
    check(candidate_text not in " ".join(ncs_after.procedures[0].steps),
          "the candidate change is NOT present in continuity")

    # --- 7. temporal stability --------------------------------------------------------
    t2 = utcnow()
    store.record("procedure", "coloring_book.production",
                 content={"name": "coloring_book.production",
                          "steps": ["define_concept", "preflight", "publish"],
                          "constraints": ["brand_rules"]},
                 scope="project/xyz", provenance={"source": "run/other"}, now=t2)
    check(store.get("procedure", "coloring_book.production").version == 2,
          "a legitimate v2 now exists (through the normal write path)")
    check(lp.target_version == 1,
          "the proposal still targets v1 — a later version does not retarget it")

    store.close()
    print("\nPASS: Phase 9.1 learning-proposal boundary holds "
          "(observation produces a proposal; nothing mutates).")


if __name__ == "__main__":
    main()
