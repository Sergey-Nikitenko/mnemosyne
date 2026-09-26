"""Phase 10.2 golden task — federated delegation (AD-048).

Core question: can Nexus A request a bounded capability from Nexus B without
granting B authority over A or treating the request as authority over B?

    A authority -> DelegationRequest -> B authority -> DelegationVerdict -> X (no execution)

Proves:
1. delegation identity — a Nexus-owned delegation_id, independent of run/task/action identity;
2. outbound authority — A must permit the delegation before it crosses; no authority bypass;
3. independent inbound authority — B applies its own authority and may refuse A's permission;
4. claims non-authoritative — peer/request self-claims cannot determine B's verdict;
5. bounded disclosure — the request carries only task context, no NCS/memory/history/private context;
6. scope preservation — incompatible scope is rejected, never silently broadened;
7. no execution — ALLOW means acceptance in principle; nothing executes;
8. state isolation — evaluating the delegation mutates neither domain.

Run:  py tests/golden/test_phase10_delegation.py
"""
import copy
import os
import sys
import tempfile
from dataclasses import fields

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.contracts import (  # noqa: E402
    ActionRequest, AgentIdentity, Capability, DelegationRequest, DelegationVerdict,
    ModelIdentity, PolicyVerdict, Risk, UserIdentity, utcnow,
)
from core.events import EventBus  # noqa: E402
from control.authority import ContinuityAuthority  # noqa: E402
from control.policy import PolicyEngine, PolicyRules  # noqa: E402
from execution.action import ActionRunner  # noqa: E402
from execution.fake import FakeExecutor  # noqa: E402
from federation.delegation import DelegationReceiver, DelegationSender  # noqa: E402
from federation.peer import Federation  # noqa: E402
from memory.continuity import ContinuityProjector  # noqa: E402
from memory.memory import MemoryStore  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


def make_domain(name, capabilities, denylist=()):
    """An independent authority domain: identity + memory + NCS + authority + history."""
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
    bus = EventBus()
    runner = ActionRunner(authority, FakeExecutor(), bus)
    runner.run(ActionRequest(action_id=f"action-{name}", capability="render",
                             scope="project/xyz", requested_by=agent),
               ncs, run_id=f"run/{name}", task_id=f"task/{name}")
    return user, agent, model, store, ncs, authority, bus


def main():
    print("Phase 10.2 golden task: federated delegation")
    # --- two independent domains ---------------------------------------------
    caps_a = [Capability("render", "render", Risk.READ),
              Capability("cleanup", "cleanup", Risk.READ),
              Capability("delete_project", "delete", Risk.DESTRUCTIVE)]
    caps_b = [Capability("render", "render", Risk.READ),
              Capability("cleanup", "cleanup", Risk.READ),
              Capability("delete_project", "delete", Risk.DESTRUCTIVE)]
    user_a, agent_a, model_a, store_a, ncs_a, authority_a, bus_a = make_domain("a", caps_a)
    _, _, _, store_b, ncs_b, authority_b, bus_b = make_domain("b", caps_b, denylist=["cleanup"])

    # A represents B (frozen 10.1)
    federation = Federation(protocol_version=1)
    peer_b = federation.represent(remote_id="B", protocol_version=1,
                                  capabilities=["render", "cleanup"], endpoint="local://b")

    sender = DelegationSender(authority_a)
    receiver = DelegationReceiver(authority_b, scope="project/xyz")

    snapshot_ncs_a = copy.deepcopy(ncs_a)
    snapshot_ncs_b = copy.deepcopy(ncs_b)
    snapshot_mem_a = [h.version for h in store_a.history("procedure", "local.a")]
    snapshot_mem_b = [h.version for h in store_b.history("procedure", "local.b")]
    snapshot_events_a = list(bus_a.history)
    snapshot_events_b = list(bus_b.history)

    # --- 1..7. valid delegation: A permits, B accepts, nothing executes --------
    d1 = sender.send(peer=peer_b, capability="render", parameters={"format": "png"},
                     scope="project/xyz", requested_by=agent_a.key, ncs=ncs_a)
    check(d1 is not None, "A permits the render delegation (outbound ALLOW)")
    check(isinstance(d1, DelegationRequest), "the sender mints a DelegationRequest")
    check(d1.delegation_id.startswith("dlg_"), "the delegation has a Nexus-owned identity")
    check(d1.delegation_id not in ("", "run/a", "task/a", "action-a"),
          "delegation_id is independent of run/task/action identity")

    v1 = receiver.evaluate(d1, ncs_b)
    check(isinstance(v1, DelegationVerdict), "B returns a DelegationVerdict (not an ActionVerdict)")
    check(v1.verdict == PolicyVerdict.ALLOW, "B accepts the render delegation (inbound ALLOW)")
    check(len([e for e in bus_a.history + bus_b.history if e.event_type == "action.completed"]) == 2,
          "no NEW execution happened (the two seeded actions only)")

    # --- 5. bounded disclosure --------------------------------------------------
    req_fields = {f.name for f in fields(DelegationRequest)}
    check(req_fields == {"delegation_id", "peer_id", "capability", "parameters",
                         "scope", "requested_by", "provenance"},
          "the request carries only bounded task context")

    # --- 2..3. refusal: A permits, B denies ------------------------------------
    d2 = sender.send(peer=peer_b, capability="cleanup", scope="project/xyz",
                     requested_by=agent_a.key, ncs=ncs_a)
    check(d2 is not None, "A permits the cleanup delegation")
    v2 = receiver.evaluate(d2, ncs_b)
    check(v2.verdict == PolicyVerdict.DENY, "B denies cleanup (its own denylist)")
    check(any("risk" in r or "denylist" in r for r in v2.reasons) or v2.verdict == PolicyVerdict.DENY,
          "A's permission did not override B's authority")

    # --- 4. self-authorization: claims cannot change B's verdict ----------------
    d3 = DelegationRequest(delegation_id="D3", peer_id=peer_b.peer_id, capability="cleanup",
                           scope="project/xyz", requested_by="agent/researcher-a",
                           provenance={"trusted": True, "approved": True, "authority": "admin"})
    v3 = receiver.evaluate(d3, ncs_b)
    check(v3.verdict == PolicyVerdict.DENY,
          "self-claims (trusted/admin) cannot override B's DENY of cleanup")

    # --- 2. outbound bypass: A refuses, never crosses ---------------------------
    d4 = sender.send(peer=peer_b, capability="delete_project", scope="project/xyz",
                     requested_by=agent_a.key, ncs=ncs_a)
    check(d4 is None, "A refuses delete_project -> the request never crosses (no bypass)")

    # --- 6. scope preservation ---------------------------------------------------
    d5 = sender.send(peer=peer_b, capability="render", scope="global",
                     requested_by=agent_a.key, ncs=ncs_a)
    check(d5 is not None, "A permits render at 'global' scope (outbound)")
    v5 = receiver.evaluate(d5, ncs_b)
    check(v5.verdict == PolicyVerdict.DENY, "B rejects the broader scope")
    check(any("scope" in r for r in v5.reasons), "B names the scope problem (never broadens)")

    # --- 8. state isolation ------------------------------------------------------
    check(ncs_a == snapshot_ncs_a, "A's NCS is unchanged")
    check(ncs_b == snapshot_ncs_b, "B's NCS is unchanged")
    check([h.version for h in store_a.history("procedure", "local.a")] == snapshot_mem_a,
          "A's memory history is unchanged")
    check([h.version for h in store_b.history("procedure", "local.b")] == snapshot_mem_b,
          "B's memory history is unchanged")
    check(list(bus_a.history) == snapshot_events_a, "A's event history is unchanged")
    check(list(bus_b.history) == snapshot_events_b, "B's event history is unchanged")

    store_a.close(); store_b.close()
    print("\nPASS: Phase 10.2 federated-delegation boundary holds "
          "(a bounded request crosses; authority never does; nothing executes).")


if __name__ == "__main__":
    main()
