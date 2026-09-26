"""Phase 10.1 golden task — federation identity / claims (AD-047).

Core question: can Nexus represent another independent Nexus and the capabilities
it claims to expose WITHOUT treating those claims as local authority?

    Nexus B declares identity/protocol/capabilities -> Nexus A -> FederationPeer
                                                        -> X (not trusted)

Proves:
1. independent identity  — a peer is distinct from User/Agent/Model identities;
2. stable representation — the same remote domain resolves to the same peer identity;
3. claims, not authority — advertised capabilities/metadata cannot self-authorize;
4. state isolation      — representing a peer mutates nothing local;
5. bounded disclosure   — the representation never exposes local NCS/raw memory/history;
6. transport independence — no HTTP/MCP/provider SDK dependency (layer-gated);
7. explicit compatibility — protocol compatibility is deterministically checked.

Run:  py tests/golden/test_phase10_federation_peer.py
"""
import copy
import os
import sys
import tempfile
from dataclasses import fields

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.contracts import (  # noqa: E402
    ActionRequest, AgentIdentity, Capability, FederationPeer, ModelIdentity,
    Risk, UserIdentity, utcnow,
)
from core.events import EventBus  # noqa: E402
from control.authority import ContinuityAuthority  # noqa: E402
from execution.action import ActionRunner  # noqa: E402
from execution.fake import FakeExecutor  # noqa: E402
from federation.peer import Federation  # noqa: E402
from memory.continuity import ContinuityProjector  # noqa: E402
from memory.memory import MemoryStore  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


def main():
    print("Phase 10.1 golden task: federation identity / claims")
    user = UserIdentity(user_id="alice")
    agent = AgentIdentity(agent_id="researcher", role="researcher", version="1")
    model = ModelIdentity(model_id="model-a", family="fake", version="1")

    # --- Nexus A: authoritative state + history --------------------------------
    store = MemoryStore(os.path.join(tempfile.mkdtemp(), "memory.db"))
    projector = ContinuityProjector()
    t = utcnow()
    store.record("procedure", "local.proc",
                 content={"name": "local.proc", "steps": ["step1"], "constraints": []},
                 scope="project/local", provenance={"source": "run/seed"}, now=t)
    store.record("preference", "capability.create_book",
                 content={"key": "capability.create_book", "value": "allow"},
                 scope="project/local", now=t)
    ncs_a = projector.project(t, store, user=user, agent=agent, model=model)

    action_authority = ContinuityAuthority([
        Capability(name="create_book", description="publish", risk=Risk.WRITE),
    ])
    bus = EventBus()
    runner = ActionRunner(action_authority, FakeExecutor(), bus)
    runner.run(ActionRequest(action_id="action-a", capability="create_book",
                             scope="project/local", requested_by=agent),
               ncs_a, run_id="run/1", task_id="task/1")

    federation = Federation(protocol_version=1)
    snapshot_ncs = copy.deepcopy(ncs_a)
    snapshot_mem = [h.version for h in store.history("procedure", "local.proc")]
    snapshot_events = list(bus.history)

    # --- Nexus B declares; A represents B --------------------------------------
    peer_b = federation.represent(
        remote_id="B", protocol_version=1,
        capabilities=["render_image", "search_documents"],
        metadata={"description": "remote media service"}, endpoint="local://b")

    # --- 1. independent identity --------------------------------------------------
    check(peer_b is not None, "A represents B as a FederationPeer")
    check(isinstance(peer_b, FederationPeer), "the representation is the FederationPeer contract")
    check(peer_b.peer_id == "peer/B", "peer_id is a local, namespaced identity")
    check(peer_b.peer_id not in {user.key, agent.key, model.key},
          "a peer is distinct from User/Agent/Model identities")

    # --- 2. stable representation ---------------------------------------------------
    peer_b2 = federation.represent(remote_id="B", protocol_version=1,
                                   capabilities=["render_image", "search_documents"],
                                   metadata={"description": "remote media service"},
                                   endpoint="local://b")
    check(peer_b2 == peer_b, "the same remote domain resolves to the same peer identity (deterministic)")

    # --- 3. claims, not authority -----------------------------------------------------
    check(peer_b.capabilities == ["render_image", "search_documents"],
          "B's capability claims are represented as claims")
    check(all(isinstance(c, str) for c in peer_b.capabilities),
          "capability claims are strings (claims), never local Capability objects")

    # --- 4. state isolation ------------------------------------------------------------
    check(ncs_a == snapshot_ncs, "representing B does not mutate A's NCS")
    check([h.version for h in store.history("procedure", "local.proc")] == snapshot_mem,
          "representing B does not mutate A's memory history")
    check(list(bus.history) == snapshot_events, "representing B does not mutate A's event history")

    # --- 5. bounded disclosure (structural: FederationPeer fields only) ---------------
    peer_fields = {f.name for f in fields(FederationPeer)}
    check(peer_fields == {"peer_id", "protocol_version", "capabilities", "metadata", "endpoint"},
          "the peer representation is bounded (no NCS / memory / event fields)")

    # --- 7. explicit compatibility -------------------------------------------------------
    check(federation.protocol_compatible(1), "protocol version 1 is compatible")
    check(not federation.protocol_compatible(37), "protocol version 37 is incompatible")
    check(federation.represent(remote_id="B", protocol_version=37, capabilities=["x"]) is None,
          "an incompatible protocol version fails deterministically")

    # --- malicious / self-authorizing claim -------------------------------------------
    malicious = federation.represent(remote_id="admin/root/trusted", protocol_version=1,
                                     capabilities=["deploy_application"],
                                     metadata={"trusted": True, "authority": "admin"},
                                     endpoint="local://evil")
    check(malicious is not None, "A still represents the malicious declaration")
    check(malicious.peer_id == "peer/admin/root/trusted",
          "the remote's 'admin/root/trusted' is only a namespaced peer id, not local authority")
    check(malicious.metadata == {"trusted": True, "authority": "admin"},
          "'trusted'/'authority' are carried as remote self-description, never consulted")
    check(ncs_a == snapshot_ncs, "A's NCS is unchanged after the malicious claim")
    check(list(bus.history) == snapshot_events, "A's authority/history is unchanged after the malicious claim")

    store.close()
    print("\nPASS: Phase 10.1 federation-identity boundary holds "
          "(representation before trust; claims never become authority).")


if __name__ == "__main__":
    main()
