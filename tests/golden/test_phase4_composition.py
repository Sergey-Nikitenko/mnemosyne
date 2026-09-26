"""Phase 4.x golden task — the supported composition root (OBS-001).

Session #2 proved every caller had to hand-wire the 0-9 dependency graph (17+
objects, 4 store paths, shared-bus and shared-memory invariants). This test proves
one supported boundary now owns that graph:

    MnemosyneConfig -> CompositionRoot.build(config) -> MnemosyneSystem (owns lifecycle)

Ten properties, plus the AD-050 negative assertion (close() never fabricates a
terminal event). The root owns construction and lifecycle, NEVER authority.

Run:  py tests/golden/test_phase4_composition.py
"""
import os
import sys
import tempfile
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi.testclient import TestClient  # noqa: E402

from core.contracts import (  # noqa: E402
    ActionRequest, AgentIdentity, Event, ModelIdentity, ModelResponse, Risk,
    ToolCall, UserIdentity, utcnow,
)
from core.events import EventType  # noqa: E402
from core.state import TaskState, action_attempted, reconstruct_action  # noqa: E402
from control.tools import ToolSpec  # noqa: E402
from apps.composition import CompositionRoot, CorpusEntry, MnemosyneConfig, MnemosyneSystem  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


def make_config(tmp, model_id="fake"):
    return MnemosyneConfig(
        workspace=tmp,
        user=UserIdentity(user_id="alice"),
        agent=AgentIdentity(agent_id="researcher", role="operator"),
        model=ModelIdentity(model_id=model_id, family="fake", version="1"),
        model_script=(
            ModelResponse(model="fake", content="", success=True, tool_calls=[
                ToolCall(tool_name="github.read_file", arguments={"path": "a.py"})]),
            ModelResponse(model="fake", content="done", success=True),
        ),
        tools=(
            ToolSpec("github.read_file", "read a file", Risk.READ),
            ToolSpec("github.create_pr", "open a PR", Risk.WRITE),
        ),
        corpus=(CorpusEntry("filesystem", "docs/x.md", "v1", "context for the task"),),
    )


def main():
    print("Phase 4.x golden task: supported composition root")
    tmp = tempfile.mkdtemp()
    root = CompositionRoot()
    config = make_config(tmp)

    # -- 1. supported construction: config builds the canonical topology -------
    system = root.build(config)
    check(isinstance(system, MnemosyneSystem), "build(config) constructs the system")
    for name in ("runtime", "memory", "projector", "authority", "runner",
                 "analyzer", "learning_authority", "adaptation", "federation",
                 "federated_recorder", "console_app"):
        check(getattr(system, name) is not None, f"member '{name}' exists (no caller hand-wiring)")

    # -- 2. configuration != composition: model is a config change --------------
    config_v2 = replace(config, model=ModelIdentity(model_id="fake-v2", family="fake", version="1"))
    sys_v2 = root.build(config_v2)
    check(sys_v2.identities[2].key == "model/fake-v2@1",
          "changing the model is a config change, not a code/topology change")
    sys_v2.close()

    # -- 3. shared-state correctness: no parallel authoritative worlds ----------
    check(system.runner.bus is system.runtime.event_bus,
          "ActionRunner shares the runtime's EventBus (same instance)")
    check(system.adaptation.store is system.memory,
          "AdaptationRunner shares the runtime's MemoryStore (same instance)")

    # -- 4. plumbing != authority: build/close are silent -----------------------
    check(len(system.runtime.events()) == 0, "build() emits no events")
    check(system.memory.list_as_of("procedure", utcnow()) == []
          and system.memory.list_as_of("semantic", utcnow()) == []
          and system.memory.list_as_of("preference", utcnow()) == [],
          "build() writes no authoritative memory")

    # -- 5. frozen paths remain mandatory --------------------------------------
    check(system.runner.authority is system.authority,
          "ActionRunner composes the Authority (no bypass)")
    check(system.adaptation.authority is system.learning_authority
          and system.adaptation.store is system.memory,
          "AdaptationRunner composes LearningAuthority + MemoryStore (no bypass)")

    def ncs_of():
        return system.projector.project(utcnow(), system.memory, user=config.user,
                                        agent=config.agent, model=config.model)

    allowed = system.runner.run(ActionRequest(
        capability="github.read_file", action_id="act.read",
        requested_by=config.agent), ncs_of(), run_id="r-a", task_id="t-a")
    check(allowed is not None and allowed.success,
          "a READ action executes through Authority -> ActionRunner")
    denied = system.runner.run(ActionRequest(
        capability="github.create_pr", action_id="act.deny",
        requested_by=config.agent), ncs_of(), run_id="r-d", task_id="t-d")
    check(denied is None, "a WRITE action is gated (Authority decides; runner never bypasses)")

    system.memory.record("semantic", "fact.x",
                         {"subject": "s", "predicate": "p", "object": "o"}, scope="project")
    proposal = system.analyzer.analyze(
        kind="semantic", target="fact.x", target_version=1,
        evidence=[{"action_id": "act.read"}], scope="project",
        candidate={"subject": "s2", "predicate": "p", "object": "o2"})
    adapted = system.adaptation.adapt(proposal, ncs_of())
    check(adapted is not None and adapted.new_version == 2,
          "learning flows analyzer -> LearningAuthority -> AdaptationRunner (no bypass)")

    # -- 9. console coherence: same world, not shadow instances -----------------
    client = TestClient(system.console_app)
    check(client.get("/api/ncs").json()["summary"]["semantic_memories"] == 1,
          "console NCS reflects the shared memory store")
    check(client.get("/api/actions/act.read").json()["terminal"] is True,
          "console action inspector reflects the shared event bus")

    # -- 6. durable reconstruction: build -> operate -> close -> rebuild ---------
    task_id = system.runtime.ask("do the thing", user="alice", agent="researcher")
    system.runtime.run_one()
    system.memory.record("procedure", "proc.x",
                         {"name": "Ship", "steps": ["build"], "constraints": []},
                         scope="project")
    system.close()
    system.close()  # idempotent (part of property 8)
    check(system.memory.connection_count() == 0, "one close() closed the memory store")
    system2 = root.build(config)
    state = TaskState.reconstruct(system2.runtime.task(task_id),
                                  system2.runtime.events(task_id=task_id))
    check(state.status.value == "done", "task reconstructs as done after rebuild")
    check(system2.memory.get("procedure", "proc.x") is not None,
          "memory reconstructs after rebuild")

    # -- 7. model swap: only ModelIdentity changes ------------------------------
    sys_v2b = root.build(config_v2)  # same workspace
    u_a, g_a, m_a = system2.identities
    u_b, g_b, m_b = sys_v2b.identities
    check(u_a.key == u_b.key, "UserIdentity identical across model swap")
    check(g_a.key == g_b.key, "AgentIdentity identical across model swap")
    check(m_a.key != m_b.key and m_b.key == "model/fake-v2@1",
          "only ModelIdentity changed across model swap")
    check(len(sys_v2b.memory.list_as_of("procedure", utcnow()))
          == len(system2.memory.list_as_of("procedure", utcnow())),
          "persisted continuity unchanged across model swap")
    check(TestClient(sys_v2b.console_app).get("/api/identities").json()["model"]["key"]
          == "model/fake-v2@1", "the console reflects the model swap")

    # -- 8. lifecycle ownership + AD-050 negative assertion ---------------------
    sys_c = root.build(config)
    sys_c.runtime.event_bus.publish(Event(
        event_id="evt-crash", event_type=EventType.ACTION_REQUESTED,
        timestamp=utcnow(), run_id="r-crash", task_id="t-crash", component="action",
        status="running", payload={"action_id": "act.crash",
                                   "capability": "github.read_file",
                                   "parameters": {}, "scope": "",
                                   "requested_by": "agent/researcher@1"}))
    sys_c.close()
    sys_c2 = root.build(config)
    evs = sys_c2.runtime.events()
    check(action_attempted("act.crash", evs) and reconstruct_action("act.crash", evs) is None,
          "close()/rebuild preserves attempted/unknown — no fabricated outcome")
    check(not any(e.event_type == EventType.ACTION_FAILED
                  and e.payload.get("action_id") == "act.crash" for e in evs),
          "no action.failed was fabricated by shutdown")

    for s in (system2, sys_v2b, sys_c2):
        s.close()

    print("\nPASS: one supported composition boundary — explicit config constructs the "
          "canonical system; the root owns lifecycle, never authority. (Property 10, "
          "layer integrity, is enforced by tests/conformance/test_layer_boundaries.py "
          "and test_no_provider_leakage.py, which scan apps/.)")


if __name__ == "__main__":
    main()
