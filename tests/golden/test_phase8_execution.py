"""Phase 8.2 golden task — controlled action execution (AD-041).

When the Authority returns ALLOW, Nexus executes the proposed capability through
the EXISTING Executor and records an authoritative, reconstructible completion
event. DENY and APPROVAL_REQUIRED never reach the executor.

    ActionRequest -> Authority -> (ALLOW) -> Executor -> ActionResult
                    -> action.completed -> reconstruct

Proves, in one end-to-end deterministic test:
1. ALLOW is the ONLY execution path (DENY / APPROVAL_REQUIRED never call the executor);
2. the existing Executor does the execution (no agency-specific engine);
3. ActionResult is the outcome (distinct from ActionVerdict);
4. action.completed carries provenance (who/capability/params/run/task/when/result);
5. the completion reconstructs deterministically from the event, identical to the live result.

Run:  py tests/golden/test_phase8_execution.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.contracts import (  # noqa: E402
    ActionRequest, ActionResult, AgentIdentity, Capability, ModelIdentity,
    Risk, UserIdentity, utcnow,
)
from core.events import EventBus  # noqa: E402
from core.state import reconstruct_action  # noqa: E402
from control.authority import ContinuityAuthority  # noqa: E402
from execution.action import ActionRunner  # noqa: E402
from execution.fake import FakeExecutor  # noqa: E402
from memory.continuity import ContinuityProjector  # noqa: E402
from memory.memory import MemoryStore  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


class CountingExecutor:
    """Wraps the existing FakeExecutor with a call counter — proves the agency
    layer delegates to the existing Executor (and counts how many times)."""

    def __init__(self):
        self.calls = 0
        self._inner = FakeExecutor()

    def execute_tool(self, call):
        self.calls += 1
        return self._inner.execute_tool(call)

    def run_model(self, request):
        return self._inner.run_model(request)


def make_ncs(user, agent, model, preferences=()):
    store = MemoryStore(os.path.join(tempfile.mkdtemp(), "memory.db"))
    t = utcnow()
    for key, value, scope in preferences:
        store.record("preference", key, content={"key": key, "value": value},
                     scope=scope, now=t)
    return store, ContinuityProjector().project(t, store, user=user, agent=agent, model=model)


def main():
    print("Phase 8.2 golden task: controlled action execution")
    user = UserIdentity(user_id="alice")
    agent = AgentIdentity(agent_id="researcher", role="researcher", version="1")
    model = ModelIdentity(model_id="model-a", family="fake", version="1")

    authority = ContinuityAuthority([
        Capability(name="create_book", description="publish a book", risk=Risk.WRITE),
    ])
    executor = CountingExecutor()
    bus = EventBus()
    runner = ActionRunner(authority, executor, bus)

    store_allow, ncs_allow = make_ncs(user, agent, model,
                                      [("capability.create_book", "allow", "project/xyz")])
    store_deny, ncs_deny = make_ncs(user, agent, model,
                                    [("capability.create_book", "deny", "project/xyz")])
    store_approval, ncs_approval = make_ncs(user, agent, model)

    action = ActionRequest(capability="create_book", parameters={"title": "Wild Wings"},
                           scope="project/xyz", requested_by=agent)

    # --- ALLOW: the only execution path --------------------------------------
    result = runner.run(action, ncs_allow, run_id="run/1", task_id="task/1")
    check(result is not None, "ALLOW -> the action executes (an ActionResult is produced)")
    check(executor.calls == 1, "execution happened exactly once")
    check(isinstance(result, ActionResult), "the outcome is an ActionResult (not an ActionVerdict)")
    check(result.success and result.capability == "create_book", "the result is preserved")
    check(result.output == {"echo": {"title": "Wild Wings"}}, "the output is preserved")
    check(result.requested_by == agent.key and result.scope == "project/xyz",
          "provenance (who + scope) is preserved")
    check(result.parameters == {"title": "Wild Wings"}, "authorized parameters are preserved")
    check(result.run_id == "run/1" and result.task_id == "task/1",
          "the run/task identity is preserved")
    check(result.action_id.startswith("act_"), "the action carries a Nexus-owned identity")

    completed = [e for e in bus.history if e.event_type == "action.completed"]
    check(len(completed) == 1, "exactly one action.completed event is emitted")
    check(completed[0].payload["action_id"] == result.action_id,
          "the event is bound to the action's identity")

    # --- reconstruction -------------------------------------------------------
    rebuilt = reconstruct_action(result.action_id, bus.history)
    check(rebuilt == result, "reconstructed state == original authoritative state")
    check(reconstruct_action(result.action_id, bus.history) == rebuilt,
          "repeat reconstruction is identical")

    # --- DENY / APPROVAL_REQUIRED: never execute ------------------------------
    before = executor.calls
    check(runner.run(action, ncs_deny, run_id="run/2", task_id="task/2") is None,
          "DENY -> no execution")
    check(runner.run(action, ncs_approval, run_id="run/3", task_id="task/3") is None,
          "APPROVAL_REQUIRED -> no execution")
    check(executor.calls == before, "neither the model nor the executor bypasses the verdict")
    check(len([e for e in bus.history if e.event_type == "action.completed"]) == 1,
          "denied / approval-required actions emit no completion event")

    for s in (store_allow, store_deny, store_approval):
        s.close()
    print("\nPASS: Phase 8.2 controlled action execution holds "
          "(ALLOW runs once; DENY/APPROVAL never reach the executor; completion reconstructs).")


if __name__ == "__main__":
    main()
