"""Phase 7.4 golden task — ContextAdapter (NCS -> model-specific request).

Translate the model-neutral NexusContinuityState into a provider/model-specific
request. The adapter is a translation boundary, not an orchestration layer: it
never retrieves/mutates memory, never decides identity or selects a model, never
invokes a model, never calls MCP, never touches a provider SDK or the event store,
and never modifies the NCS.

Proves, in one test (AD-038):
1. determinism — identical NCS + identical adapter configuration -> identical request;
2. purity — adapting does not mutate the NCS (or any underlying state);
3. translation — NCS identity + memory appear correctly in the target request;
4. isolation — provider vocabulary exists only in the request/adapter, never the NCS;
5. one continuity representation, many model representations — a second, local-model
   adapter produces a different-shaped request from the SAME NCS.

Run:  py tests/golden/test_phase7_context_adapter.py
"""
import copy
import os
import sys
import tempfile
from dataclasses import dataclass, fields

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.contracts import (  # noqa: E402
    AgentIdentity, ContextRequest, ModelIdentity, UserIdentity, utcnow,
)
from core.context import OpenAIContextAdapter  # noqa: E402
from memory.continuity import ContinuityProjector  # noqa: E402
from memory.memory import MemoryStore  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


@dataclass(frozen=True)
class LocalContextAdapter:
    """A second, local-model-flavored adapter (test-local): the SAME NCS becomes a
    COMPLETELY different representation — one raw prompt, no chat messages."""
    provider: str = "local"
    max_new_tokens: int = 512

    def adapt(self, ncs):
        parts = [f"[as_of={ncs.as_of.isoformat()}]",
                 f"user={ncs.user.key}", f"agent={ncs.agent.key}", f"model={ncs.model.key}"]
        parts += [f"proc:{p.name}->{'|'.join(p.steps)}" for p in ncs.procedures]
        parts += [f"fact:{s.subject} {s.predicate} {s.object}" for s in ncs.semantic_memories]
        parts += [f"pref:{pref.key}={pref.value}" for pref in ncs.preferences]
        prompt = "\n".join(parts)
        return ContextRequest(provider=self.provider, model=ncs.model.key,
                              body={"prompt": prompt, "max_new_tokens": self.max_new_tokens})


def main():
    print("Phase 7.4 golden task: ContextAdapter (NCS -> model-specific request)")
    store = MemoryStore(os.path.join(tempfile.mkdtemp(), "memory.db"))
    projector = ContinuityProjector()

    user = UserIdentity(user_id="alice")
    agent = AgentIdentity(agent_id="researcher", role="researcher", version="1")
    model = ModelIdentity(model_id="fake-deterministic", family="fake", version="1")

    t = utcnow()
    store.record("procedure", "coloring_book.production",
                 content={"name": "coloring_book.production",
                          "steps": ["define_concept", "publish"],
                          "constraints": ["brand_rules"]},
                 scope="project/xyz", provenance={"source": "run/a"}, now=t)
    store.record("semantic", "brand.palette",
                 content={"subject": "brand", "predicate": "palette_is", "object": "crayon"},
                 scope="project/xyz", now=t)
    store.record("preference", "style",
                 content={"key": "style", "value": "minimal"}, scope="user/alice", now=t)

    ncs = projector.project(t, store, user=user, agent=agent, model=model)
    snapshot = copy.deepcopy(ncs)

    openai = OpenAIContextAdapter()
    local = LocalContextAdapter()

    # --- 1. determinism ------------------------------------------------------
    req_a1 = openai.adapt(ncs)
    req_a2 = openai.adapt(ncs)
    check(req_a1 == req_a2, "determinism: identical NCS + identical config -> identical request")
    check(isinstance(req_a1, ContextRequest), "the adapter returns the ContextRequest contract")

    # --- 2. purity -----------------------------------------------------------
    openai.adapt(ncs)
    local.adapt(ncs)
    check(ncs == snapshot, "purity: adapting does not mutate the NCS")
    check([h.version for h in store.history("procedure", "coloring_book.production")] == [1],
          "purity: adapting does not mutate the memory store")
    check({f.name for f in fields(openai)} == {"provider", "temperature", "include_as_of"},
          "purity: the adapter holds only explicit config (no store/router/executor/MCP)")

    # --- 3. translation ------------------------------------------------------
    check(req_a1.model == model.key, "translation: the model key is translated into the request")
    system = req_a1.body["messages"][0]
    check(system["role"] == "system" and "content" in system,
          "translation: OpenAI-flavored request uses the messages/role/content shape")
    check("coloring_book.production" in system["content"], "translation: procedures appear")
    check("define_concept" in system["content"], "translation: procedure steps appear")
    check("brand_rules" in system["content"], "translation: procedure constraints appear")
    check("palette_is" in system["content"], "translation: semantic memories appear")
    check("style = minimal" in system["content"], "translation: preferences appear")
    check("user/alice" in system["content"] and "agent/researcher@1" in system["content"],
          "translation: user + agent identity appear")

    # --- 4. isolation --------------------------------------------------------
    ncs_fields = {f.name for f in fields(ncs)}
    check(ncs_fields == {"as_of", "user", "agent", "model",
                         "procedures", "semantic_memories", "preferences"},
          "isolation: the NCS carries only model-neutral continuity fields")
    check(not ({"messages", "temperature", "prompt", "max_new_tokens", "tools", "system"} & ncs_fields),
          "isolation: no provider vocabulary contaminates the NCS contract")

    # --- 5. one NCS, many representations -------------------------------------
    req_b = local.adapt(ncs)
    check(req_a1 != req_b, "one continuity representation -> many model representations (A != B)")
    check(req_a1.provider != req_b.provider, "the two adapters declare different provider families")
    check("messages" in req_a1.body and "prompt" in req_b.body,
          "the two requests have different provider-specific shapes")
    check("coloring_book.production" in req_b.body["prompt"],
          "the second adapter carries the same continuity content")
    check(ncs == snapshot, "still pure after both adapters")

    store.close()
    print("\nPASS: Phase 7.4 ContextAdapter holds (deterministic, pure, faithful, isolated).")


if __name__ == "__main__":
    main()
