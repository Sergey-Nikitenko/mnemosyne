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

    # --- 7.x memory retirement (AD-051) ---------------------------------------
    store.record("semantic", "brand.palette",
                 content={"subject": "brand", "predicate": "palette_is", "object": "crayon"},
                 scope="project/xyz", now=t2)
    store.record("preference", "style",
                 content={"key": "style", "value": "minimal"}, scope="user/alice", now=t2)
    semantic_before = store.get("semantic", "brand.palette")
    preference_before = store.get("preference", "style")
    v1_before = store.history("procedure", "coloring_book.production")[0]
    v2_before = store.get("procedure", "coloring_book.production")

    t3 = t2 + timedelta(seconds=10)
    retired = store.retire_if_current("procedure", "coloring_book.production",
                                      expected_version=2, provenance={"source": "ret/1"}, now=t3)
    check(retired is not None and retired.status == "retired", "retire -> status retired")
    check(retired.version == 2, "retirement does not increment the content version")

    # 1. append-only retirement
    check(store.history("procedure", "coloring_book.production") == [v1_before, v2_before],
          "append-only: v1 + v2 byte-identical; no content rewrite, no v3")
    check(len(store.retirement_history("procedure", "coloring_book.production")) == 1,
          "exactly one memory.retired appended")

    # 2. current continuity exclusion
    ncs_now = projector.project(t3, store, user=user, agent=agent, model=model)
    check(len(ncs_now.procedures) == 0, "NCS(now) excludes the retired P")

    # 3. historical continuity preservation (project t2 AFTER retirement happened)
    ncs_t2 = projector.project(t2, store, user=user, agent=agent, model=model)
    check(len(ncs_t2.procedures) == 1 and ncs_t2.procedures[0].version == 2,
          "NCS(t2) still contains P v2 (temporal, not retroactive)")

    # 4. lifecycle reconstruction
    check(store.get("procedure", "coloring_book.production").status == "retired",
          "get() reports retired")
    check([p.status for p in store.list_as_of("procedure", t2)] == ["active"],
          "list_as_of(t2) reports active")
    check([p.status for p in store.list_as_of("procedure", t3)] == ["retired"],
          "list_as_of(t3) reports retired")

    # 5. fresh retirement (stale reject)
    store.record("procedure", "q.proc",
                 content={"name": "q.proc", "steps": ["a"], "constraints": []},
                 scope="project/xyz", now=t3 + timedelta(seconds=1))
    store.record("procedure", "q.proc",
                 content={"name": "q.proc", "steps": ["a", "b"], "constraints": []},
                 scope="project/xyz", now=t3 + timedelta(seconds=2))
    stale = store.retire_if_current("procedure", "q.proc", expected_version=1,
                                    now=t3 + timedelta(seconds=3))
    check(stale is None, "a stale retirement is rejected atomically")
    check(store.get("procedure", "q.proc").status == "active"
          and store.get("procedure", "q.proc").version == 2, "Q v2 stays ACTIVE")

    # 6. idempotent retirement
    retired2 = store.retire_if_current("procedure", "coloring_book.production",
                                       expected_version=2, now=t3 + timedelta(seconds=4))
    check(retired2.status == "retired", "idempotent retirement returns the retired state")
    check(len(store.retirement_history("procedure", "coloring_book.production")) == 1,
          "one retirement event (no duplicate lifecycle history)")

    # 7. no implicit resurrection
    resurrect = store.record("procedure", "coloring_book.production",
                             content={"name": "coloring_book.production",
                                      "steps": ["x"], "constraints": []},
                             scope="project/xyz", now=t3 + timedelta(seconds=5))
    check(resurrect is None, "record() on a retired memory is rejected")
    ncs_r = projector.project(t3 + timedelta(seconds=5), store, user=user, agent=agent, model=model)
    check(all(p.memory_id != "coloring_book.production" for p in ncs_r.procedures),
          "no new active Procedure appears in the NCS (P stays retired)")

    # 8. other memory untouched
    check(store.get("semantic", "brand.palette") == semantic_before, "SemanticMemory untouched")
    check(store.get("preference", "style") == preference_before, "Preference untouched")
    check(store.get("procedure", "q.proc").status == "active", "other Procedure (Q) untouched")

    store.close()
    print("\nPASS: Phase 7.3 ContinuityProjector holds (deterministic, read-only, model-independent).")


if __name__ == "__main__":
    main()
