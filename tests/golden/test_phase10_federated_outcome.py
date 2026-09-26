"""Phase 10.3 golden task — federated outcome (AD-049).

Core question: when Nexus B executes an accepted delegation, what may Nexus A
truthfully record about the result?

    B ActionResult -> federation translation -> FederatedOutcome -> A receipt

The central distinction: A records "B reported X", never "A observed X".

Proves:
1. local execution ownership — B mints its own action identity and uses Phase 8;
2. current authority — DelegationVerdict.ALLOW is not execution permission;
3. report != observation — A's log has a receipt, never B's action.completed;
4. provenance correlation — delegation_id + peer_id + remote action reference;
5. exactly-once local receipt — duplicate delivery is idempotent;
6. conflict preservation — a conflicting report is rejected, never overwritten;
7. no authority/mutation injection — remote output cannot mutate A's state;
8. independent histories — B's action log and A's receipt log stay separate.

Run:  py tests/golden/test_phase10_federated_outcome.py
"""
import copy
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.contracts import (  # noqa: E402
    AgentIdentity, Capability, FederatedOutcome, ModelIdentity, Risk, UserIdentity, utcnow,
)
from core.events import EventBus  # noqa: E402
from core.state import reconstruct_federated_outcome  # noqa: E402
from control.authority import ContinuityAuthority  # noqa: E402
from control.policy import PolicyEngine, PolicyRules  # noqa: E402
from execution.action import ActionRunner  # noqa: E402
from execution.fake import FakeExecutor  # noqa: E402
from federation.delegation import DelegationReceiver, DelegationSender  # noqa: E402
from federation.outcome import DelegationService, FederatedOutcomeRecorder  # noqa: E402
from federation.peer import Federation  # noqa: E402
from memory.continuity import ContinuityProjector  # noqa: E402
from memory.memory import MemoryStore  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


def make_domain(name, capabilities, denylist=()):
    user = UserIdentity(user_id=f"alice-{name}")
    agent = AgentIdentity(agent_id=f"researcher-{name}", role="researcher", version="1")
    model = ModelIdentity(model_id=f"model-{name}", family="fake", version="1")
    store = MemoryStore(os.path.join(tempfile.mkdtemp(), f"mem-{name}.db"))
    projector = ContinuityProjector()
    t = utcnow()
    store.record("procedure", f"local.{name}",
                 content={"name": f"local.{name}", "steps": ["s"], "constraints": []},
                 scope=f"project/{name}", provenance={"source": "seed"}, now=t)
    ncs = projector.project(t, store, user=user, agent=agent, model=model)
    authority = ContinuityAuthority(capabilities,
                                    policy=PolicyEngine(PolicyRules(denylist=list(denylist))))
    return user, agent, model, store, ncs, authority


def main():
    print("Phase 10.3 golden task: federated outcome")
    caps = [Capability("render", "render", Risk.READ),
            Capability("cleanup", "cleanup", Risk.READ)]

    _, _, _, store_a, ncs_a, authority_a = make_domain("a", caps)
    _, _, _, store_b, ncs_b, _ = make_domain("b", caps)

    # A: represent B + outbound gate + receipt recorder
    federation = Federation(protocol_version=1)
    peer_b = federation.represent(remote_id="B", protocol_version=1,
                                  capabilities=["render", "cleanup"])
    sender = DelegationSender(authority_a)
    bus_a = EventBus()
    recorder = FederatedOutcomeRecorder(bus_a)

    # B: accept authority (allows render + cleanup) + execute authority (denies cleanup)
    authority_b_accept = ContinuityAuthority(caps)
    authority_b_execute = ContinuityAuthority(caps,
                                              policy=PolicyEngine(PolicyRules(denylist=["cleanup"])))
    receiver = DelegationReceiver(authority_b_accept, scope="project/xyz")
    bus_b = EventBus()
    action_runner_b = ActionRunner(authority_b_execute, FakeExecutor(), bus_b)
    service = DelegationService(receiver, action_runner_b)

    snapshot_ncs_a = copy.deepcopy(ncs_a)
    snapshot_ncs_b = copy.deepcopy(ncs_b)
    snapshot_mem_a = [h.version for h in store_a.history("procedure", "local.a")]
    snapshot_mem_b = [h.version for h in store_b.history("procedure", "local.b")]

    # --- 1..4. successful path: B executes, A records the receipt --------------
    d1 = sender.send(peer=peer_b, capability="render", parameters={"format": "png"},
                     scope="project/xyz", requested_by="agent/researcher-a", ncs=ncs_a)
    check(d1 is not None, "A permits sending D1")

    o1 = service.execute(d1, ncs_b, run_id="run/b", task_id="task/b")
    check(o1 is not None and o1.status == "reported_success",
          "B executes and reports a success")
    check(o1.delegation_id == d1.delegation_id and o1.peer_id == peer_b.peer_id,
          "the outcome correlates to delegation D1 and peer B")
    check(o1.remote_action_id.startswith("act_") and o1.remote_action_id != d1.delegation_id,
          "B minted its own action identity (distinct from delegation_id)")

    b_actions = [e for e in bus_b.history if e.event_type == "action.completed"]
    check(len(b_actions) == 1 and b_actions[0].payload.get("action_id") == o1.remote_action_id,
          "B's action.completed is authoritative in B (B owns the action)")
    check(len([e for e in bus_b.history if e.event_type == "action.requested"]) == 1,
          "B emits action.requested before its side effect (inherits AD-050)")
    check(len([e for e in bus_a.history if e.event_type == "action.requested"]) == 0,
          "no B action lifecycle event crosses into A")

    recorded = recorder.record(o1)
    check(recorded == o1, "A records the bounded outcome")
    a_receipts = [e for e in bus_a.history if e.event_type == "federation.outcome.received"]
    check(len(a_receipts) == 1, "A has one federation.outcome.received")
    check(reconstruct_federated_outcome(d1.delegation_id, bus_a.history) == o1,
          "the outcome reconstructs deterministically from A's events")

    # --- 3. report != observation ----------------------------------------------
    a_actions = [e for e in bus_a.history if e.event_type == "action.completed"]
    check(len(a_actions) == 0,
          "A's history does NOT contain action.completed (report, not observation)")

    # --- 2. stale authorization: acceptance is not execution permission --------
    d2 = sender.send(peer=peer_b, capability="cleanup", scope="project/xyz",
                     requested_by="agent/researcher-a", ncs=ncs_a)
    check(d2 is not None, "A permits sending cleanup")
    o2 = service.execute(d2, ncs_b, run_id="run/b2", task_id="task/b2")
    check(o2 is not None and o2.status == "reported_failure" and o2.error == "refused at execution",
          "B accepted cleanup but its execution-time authority refused (no stale bypass)")
    check(len([e for e in bus_b.history if e.event_type == "action.completed"]) == 1,
          "no new action.completed (cleanup was never executed)")

    # --- 5. duplicate receipt is idempotent -------------------------------------
    check(recorder.record(o1) == o1, "duplicate delivery returns the same outcome")
    check(len([e for e in bus_a.history if e.event_type == "federation.outcome.received"]) == 1,
          "one authoritative receipt (no duplicate)")

    # --- 6. conflicting report is rejected --------------------------------------
    o_conflict = FederatedOutcome(delegation_id=d1.delegation_id, peer_id=peer_b.peer_id,
                                  status="reported_failure", remote_action_id="act_other",
                                  output=None, error="different")
    check(recorder.record(o_conflict) is None, "a conflicting report is rejected")
    check(reconstruct_federated_outcome(d1.delegation_id, bus_a.history) == o1,
          "the original outcome remains authoritative (history is never rewritten)")
    check(len([e for e in bus_a.history if e.event_type == "federation.outcome.received"]) == 1,
          "no second authoritative receipt")

    # --- 7. remote output cannot inject authority / mutation ---------------------
    o_malicious = FederatedOutcome(delegation_id="D-malicious", peer_id=peer_b.peer_id,
                                   status="reported_success", remote_action_id="act_m",
                                   output={"trusted": True, "grant": "admin",
                                           "preference": "always_allow",
                                           "learn_this": {"procedure": "x"}})
    check(recorder.record(o_malicious) is not None, "A records the bounded remote output")
    check(ncs_a == snapshot_ncs_a, "A's NCS is unchanged")
    check([h.version for h in store_a.history("procedure", "local.a")] == snapshot_mem_a,
          "A's memory is unchanged")
    check(len([e for e in bus_a.history if e.event_type == "action.completed"]) == 0,
          "no local action was created from remote output")

    # --- 8. independent histories + state isolation ------------------------------
    check(ncs_b == snapshot_ncs_b, "B's NCS is unchanged")
    check([h.version for h in store_b.history("procedure", "local.b")] == snapshot_mem_b,
          "B's memory is unchanged")
    check(len([e for e in bus_b.history if e.event_type == "federation.outcome.received"]) == 0,
          "B's history has no A-side receipt (histories stay separate)")

    store_a.close(); store_b.close()
    print("\nPASS: Phase 10.3 federated-outcome boundary holds "
          "(A records 'B reported X', never 'A observed X'; histories stay independent).")


if __name__ == "__main__":
    main()
