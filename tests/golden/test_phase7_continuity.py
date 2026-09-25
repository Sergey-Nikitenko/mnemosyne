"""Phase 7.3 golden task — ContinuityProjector / NCS.

Reconstruct the world as of a point in time from authoritative state. The
projector is a read model: no new store, no model, no mutation — deterministic.

Proves:
1. as_of reconstruction — v1 at t1, v2 at t2, so the NCS answers "what was in
   force then", not just "what is current now";
2. determinism — the same as_of yields the same NCS;
3. read-only — projecting never mutates the memory store;
4. "new session != new identity" — a model swap changes only ModelIdentity; the
   reconstructed memory and the user/agent identity are unchanged.

Run:  py tests/golden/test_phase7_continuity.py
"""
import os
import sys
import tempfile
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.contracts import (  # noqa: E402
    AgentIdentity, ModelIdentity, Procedure, UserIdentity, utcnow,
)
from memory.continuity import ContinuityProjector  # noqa: E402
from memory.memory import MemoryStore  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


def main():
    print("Phase 7.3 golden task: ContinuityProjector / NCS")
    store = MemoryStore(os.path.join(tempfile.mkdtemp(), "memory.db"))
    projector = ContinuityProjector()

    user = UserIdentity(user_id="alice")
    agent = AgentIdentity(agent_id="researcher", role="researcher", version="1")
    model = ModelIdentity(model_id="fake-deterministic", family="fake", version="1")

    # two versions of the same procedure, at two points in time -------------
    t1 = utcnow()
    store.record("procedure", "coloring_book.production",
                 content={"name": "coloring_book.production",
                          "steps": ["define_concept", "publish"],
                          "constraints": ["brand_rules"]},
                 scope="project/xyz", provenance={"source": "run/a"}, now=t1)
    t2 = t1 + timedelta(seconds=10)
    store.record("procedure", "coloring_book.production",
                 content={"name": "coloring_book.production",
                          "steps": ["define_concept", "preflight", "publish"],
                          "constraints": ["brand_rules"]},
                 scope="project/xyz", provenance={"source": "run/b"}, now=t2)

    # 1. as_of reconstruction ------------------------------------------------
    ncs_t1 = projector.project(t1, store, user=user, agent=agent, model=model)
    ncs_t2 = projector.project(t2, store, user=user, agent=agent, model=model)
    check(ncs_t1.procedures[0].version == 1 and len(ncs_t1.procedures[0].steps) == 2,
          "as-of t1 reconstructs procedure v1 (2 steps)")
    check(ncs_t2.procedures[0].version == 2 and len(ncs_t2.procedures[0].steps) == 3,
          "as-of t2 reconstructs procedure v2 (3 steps)")
    check(isinstance(ncs_t2.procedures[0], Procedure),
          "the NCS carries typed memory contracts")

    # 2. determinism ---------------------------------------------------------
    check(projector.project(t2, store, user=user, agent=agent, model=model) == ncs_t2,
          "the same as_of yields the same NCS (deterministic)")

    # 3. read-only — projection never mutates the store ----------------------
    before = [h.version for h in store.history("procedure", "coloring_book.production")]
    projector.project(t2, store, user=user, agent=agent, model=model)
    after = [h.version for h in store.history("procedure", "coloring_book.production")]
    check(before == after == [1, 2], "projecting does not mutate the memory store")

    # 4. new session != new identity -----------------------------------------
    model_b = ModelIdentity(model_id="other-model", family="other", version="1")
    ncs_b = projector.project(t2, store, user=user, agent=agent, model=model_b)
    check(ncs_b.user == ncs_t2.user, "UserIdentity is unchanged across a model swap")
    check(ncs_b.agent == ncs_t2.agent, "AgentIdentity is unchanged across a model swap")
    check(ncs_b.model != ncs_t2.model, "only ModelIdentity changed")
    check(ncs_b.procedures == ncs_t2.procedures,
          "the reconstructed memory as-of is model-independent")

    store.close()
    print("\nPASS: Phase 7.3 ContinuityProjector holds (deterministic, read-only, model-independent).")


if __name__ == "__main__":
    main()
