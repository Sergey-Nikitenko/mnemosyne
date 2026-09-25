"""Phase 7.5 golden task — model handoff / continuity independence (AD-039).

The property Phase 7 exists to prove: a model change must not require transfer of
the previous model's private context for Nexus continuity to survive.

    Session A -> Model A -> private context --X-- (must not cross)
                         -> authoritative memory (events)
                         -> NCS -> ContextAdapter -> ContextRequest -> Model B

Model B RECONSTRUCTS continuity; Model A does not TRANSMIT it. The handoff is
nothing new: re-project the same event-sourced memory as-of with a new
ModelIdentity (7.1/7.2/7.3), then adapt (7.4). No router, no summarization, no
transcript persistence, no second continuity mechanism.

Proves, in one test:
1. identity   — user/agent byte-identical across the handoff; only ModelIdentity changes;
2. survival   — persisted procedure/semantic/preference/project state reaches B via the 7.3->7.4 pipeline;
3. isolation  — Model A's private context (and transcript/response) is absent from B's request;
4. determinism — re-project + adapt yields the identical request;
5. reconstruction — the same request is rebuilt from authoritative state alone.

Model B is a deterministic test double that RECEIVES the ContextRequest; no LLM
inference, provider, or network is involved — the proof stops at the model boundary.

Run:  py tests/golden/test_phase7_handoff.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.contracts import AgentIdentity, ModelIdentity, UserIdentity, utcnow  # noqa: E402
from core.context import OpenAIContextAdapter  # noqa: E402
from memory.continuity import ContinuityProjector  # noqa: E402
from memory.memory import MemoryStore  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


MODEL_A = ModelIdentity(model_id="fake-deterministic", family="fake", version="1")
MODEL_B = ModelIdentity(model_id="other-model", family="other", version="1")
SECRET = "SECRET_A_ONLY"


class ModelB:
    """A deterministic Model B test double: it RECEIVES the ContextRequest and
    holds it — no LLM inference, provider, or network. The test inspects exactly
    what B received, which is the boundary 7.5 proves up to."""

    def __init__(self):
        self.received = None

    def receive(self, request):
        self.received = request
        return request


def main():
    print("Phase 7.5 golden task: model handoff / continuity independence")
    store = MemoryStore(os.path.join(tempfile.mkdtemp(), "memory.db"))
    projector = ContinuityProjector()
    adapter = OpenAIContextAdapter()

    user = UserIdentity(user_id="alice")
    agent = AgentIdentity(agent_id="researcher", role="researcher", version="1")

    # --- Session A: Model A is live, holding private context -----------------
    private_thought = f"just realized {SECRET} is the right approach"
    transcript = ["user: fix the thing", f"model_a: I will use {SECRET}"]
    model_a_response = f"done, used {SECRET}"

    t = utcnow()
    # Model A persists legitimate Nexus facts — the ONLY things that may survive.
    store.record("procedure", "coloring_book.production",
                 content={"name": "coloring_book.production",
                          "steps": ["define_concept", "publish"],
                          "constraints": ["brand_rules"]},
                 scope="project/xyz", provenance={"source": "run/a"}, now=t)
    store.record("semantic", "brand.palette",
                 content={"subject": "brand", "predicate": "palette_is", "object": "crayon"},
                 scope="project/xyz", now=t)
    store.record("semantic", "project.status",
                 content={"subject": "project/xyz", "predicate": "phase", "object": "production"},
                 scope="project/xyz", now=t)
    store.record("preference", "style",
                 content={"key": "style", "value": "minimal"}, scope="user/alice", now=t)

    # --- End Session A: the handoff never touches Model A's private context ----
    ncs = projector.project(t, store, user=user, agent=agent, model=MODEL_B)
    request = adapter.adapt(ncs)

    # --- 1. identity: the model changes, Nexus identity does not --------------
    check(ncs.user == user and ncs.user.key == "user/alice",
          "UserIdentity is byte-identical across the handoff")
    check(ncs.agent == agent and ncs.agent.key == "agent/researcher@1",
          "AgentIdentity is byte-identical across the handoff")
    check(ncs.model == MODEL_B and ncs.model != MODEL_A,
          "only ModelIdentity changed (Model A -> Model B)")
    check(request.model == MODEL_B.key, "the request targets Model B's identity")

    # --- 2. survival: persisted Nexus state reaches Model B --------------------
    model_b = ModelB()
    model_b.receive(request)
    check(model_b.received is request, "Model B receives the ContextRequest (and nothing else)")
    check(len(request.body["messages"]) == 1,
          "Model B receives exactly one (system) message — no transcript smuggled in")
    system = request.body["messages"][0]["content"]
    check("coloring_book.production" in system, "procedure survives into Model B's request")
    check("define_concept" in system, "procedure steps survive")
    check("brand_rules" in system, "procedure constraints survive")
    check("palette_is" in system, "semantic memory survives")
    check("style = minimal" in system, "preference survives")
    check("project/xyz" in system and "production" in system,
          "project/continuity state survives")
    check("user/alice" in system and "agent/researcher@1" in system,
          "identity survives into Model B's request")

    # --- 3. isolation: Model A's private context does NOT cross ----------------
    check(SECRET not in system, "Model A's private context does not cross the boundary")
    check(private_thought not in system, "Model A's private thought does not cross")
    check(model_a_response not in system, "Model A's response does not cross")
    check(transcript[0] not in system, "Session A's transcript does not cross")
    check("fix the thing" not in system, "no user message is copied verbatim into B")

    # --- 4. determinism ---------------------------------------------------------
    again = adapter.adapt(projector.project(t, store, user=user, agent=agent, model=MODEL_B))
    check(again == request, "the handoff is deterministic (re-project + adapt -> same request)")

    # --- 5. reconstructive, not transmissive ------------------------------------
    rebuilt = projector.project(t, store, user=user, agent=agent, model=MODEL_B)
    check(rebuilt == ncs, "the NCS is a reconstruction, not a transmission")
    check(adapter.adapt(rebuilt) == request,
          "Model B reconstructs the identical request from authoritative state alone")

    store.close()
    print("\nPASS: Phase 7.5 handoff holds (Model B reconstructs continuity; Model A transmits nothing).")


if __name__ == "__main__":
    main()
