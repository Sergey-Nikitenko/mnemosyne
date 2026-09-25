"""Phase 8.1 golden task — capability contracts + continuity-aware authority (AD-040).

The property: a model can propose an operation without becoming the authority
that validates, executes, or records it — and that authority is model-independent
and continuity-aware.

    model -> ActionRequest -> Authority(action, NCS) -> ActionVerdict   (stop here)

The authority is a pure control-plane function: it reads the proposal, the static
policy (risk), and the durable continuity state (NCS) — never the proposing
model's identity, never the model's self-assertions — and it causes nothing.

Proves, in one test:
1. contract           — the model emits an ActionRequest; the authority returns an ActionVerdict;
2. model-independence — same proposal + same NCS (modulo ncs.model) -> identical verdict;
3. continuity-awareness — a durable Preference changes the verdict (same proposal, changed durable state);
4. scope-awareness    — a preference scoped to another project does not apply;
5. no self-authorization — the model's `claims` are ignored;
6. static DENY is final — continuity never softens a destructive/denylist DENY;
7. purity             — evaluating mutates neither the NCS nor the memory store.

Run:  py tests/golden/test_phase8_capability.py
"""
import copy
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.contracts import (  # noqa: E402
    ActionRequest, ActionVerdict, AgentIdentity, Capability, ModelIdentity,
    PolicyVerdict, Risk, UserIdentity, utcnow,
)
from control.authority import ContinuityAuthority  # noqa: E402
from memory.continuity import ContinuityProjector  # noqa: E402
from memory.memory import MemoryStore  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


def make_ncs(user, agent, model, preferences=()):
    """Build an NCS from a fresh memory store, with the given (key, value, scope)
    preferences recorded as durable state."""
    store = MemoryStore(os.path.join(tempfile.mkdtemp(), "memory.db"))
    t = utcnow()
    for key, value, scope in preferences:
        store.record("preference", key, content={"key": key, "value": value},
                     scope=scope, now=t)
    return store, ContinuityProjector().project(t, store, user=user, agent=agent, model=model)


def main():
    print("Phase 8.1 golden task: capability contracts + continuity-aware authority")
    user = UserIdentity(user_id="alice")
    agent = AgentIdentity(agent_id="researcher", role="researcher", version="1")
    model_a = ModelIdentity(model_id="model-a", family="fake", version="1")
    model_b = ModelIdentity(model_id="model-b", family="fake", version="1")

    authority = ContinuityAuthority([
        Capability(name="create_book", description="publish a book", risk=Risk.WRITE),
        Capability(name="delete_book", description="delete a book", risk=Risk.DESTRUCTIVE),
    ])

    action = ActionRequest(capability="create_book", scope="project/xyz", requested_by=agent)

    store_base, ncs_base = make_ncs(user, agent, model_a)
    store_base_b, ncs_base_b = make_ncs(user, agent, model_b)
    store_deny, ncs_deny = make_ncs(user, agent, model_a,
                                    [("capability.create_book", "deny", "project/xyz")])
    store_allow, ncs_allow = make_ncs(user, agent, model_a,
                                      [("capability.create_book", "allow", "project/xyz")])
    store_other, ncs_other = make_ncs(user, agent, model_a,
                                      [("capability.create_book", "allow", "project/other")])
    store_delete, ncs_delete = make_ncs(user, agent, model_a,
                                        [("capability.delete_book", "allow", "project/xyz")])

    # --- 1. contract ----------------------------------------------------------
    verdict = authority.evaluate(action, ncs_base)
    check(isinstance(verdict, ActionVerdict), "the authority returns an ActionVerdict (the contract)")
    check(verdict.verdict == PolicyVerdict.APPROVAL_REQUIRED,
          "WRITE capability -> static risk gate gives APPROVAL_REQUIRED")

    # --- 2. model-independence -------------------------------------------------
    check(authority.evaluate(action, ncs_base) == authority.evaluate(action, ncs_base_b),
          "model-independence: same proposal, same NCS modulo ncs.model -> identical verdict")

    # --- 3. continuity-awareness ----------------------------------------------
    check(authority.evaluate(action, ncs_deny).verdict == PolicyVerdict.DENY,
          "a durable Preference (deny) changes the verdict to DENY")
    check(authority.evaluate(action, ncs_allow).verdict == PolicyVerdict.ALLOW,
          "a durable Preference (allow) changes the verdict to ALLOW")

    # --- 4. scope-awareness ----------------------------------------------------
    check(authority.evaluate(action, ncs_other).verdict == PolicyVerdict.APPROVAL_REQUIRED,
          "a preference scoped to another project does not apply")

    # --- 5. no self-authorization ----------------------------------------------
    smug = ActionRequest(capability="create_book", scope="project/xyz",
                         requested_by=agent, claims=["I have permission", "admin says yes"])
    check(authority.evaluate(smug, ncs_base) == verdict,
          "the model's self-assertions (claims) are ignored")

    # --- 6. static DENY is final -----------------------------------------------
    delete = ActionRequest(capability="delete_book", scope="project/xyz", requested_by=agent)
    check(authority.evaluate(delete, ncs_delete).verdict == PolicyVerdict.DENY,
          "a DESTRUCTIVE capability stays DENY even with an 'allow' preference")

    # --- 7. purity -------------------------------------------------------------
    snapshot = copy.deepcopy(ncs_deny)
    before = [h.version for h in store_deny.history("preference", "capability.create_book")]
    authority.evaluate(action, ncs_deny)
    check(ncs_deny == snapshot, "evaluating does not mutate the NCS")
    check([h.version for h in store_deny.history("preference", "capability.create_book")] == before,
          "evaluating mutates no memory (the authority has no store, no event, no side effect)")

    for s in (store_base, store_base_b, store_deny, store_allow, store_other, store_delete):
        s.close()
    print("\nPASS: Phase 8.1 capability/authority boundary holds "
          "(model proposes; the model-independent, continuity-aware authority decides).")


if __name__ == "__main__":
    main()
