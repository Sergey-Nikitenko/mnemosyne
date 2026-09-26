"""Phase 8.3 golden task — action lifecycle / idempotency (AD-042).

The core question: can Nexus execute an authorized action exactly once from
Nexus's perspective, preserve its lifecycle, and distinguish retry/recovery from a
genuinely new action?

Lifecycle (smallest authoritative): proposed -> authorized -> executing ->
completed | failed. The `action_id` is the identity of the LOGICAL action — the
idempotency key. The same id is the same logical action (retried once, never
re-executed); a new id is a new logical action and executes independently.

Proves, in one end-to-end deterministic test:
1. A executes exactly once, producing an authoritative action.completed;
2. retrying A returns the SAME authoritative result with NO second execution/event;
3. reconstruction of A is deterministic;
4. a NEW action_id executes independently (not treated as a retry of A);
5. a failed action produces an authoritative action.failed and is NOT auto-retried.

Run:  py tests/golden/test_phase8_lifecycle.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.contracts import (  # noqa: E402
    ActionRequest, ActionResult, AgentIdentity, Capability, ModelIdentity,
    ModelResponse, Risk, ToolResult, UserIdentity, utcnow,
)
from core.events import EventBus  # noqa: E402
from core.state import action_attempted, reconstruct_action  # noqa: E402
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
    """Wraps the existing FakeExecutor with a call counter (success path)."""

    def __init__(self):
        self.calls = 0
        self._inner = FakeExecutor()

    def execute_tool(self, call):
        self.calls += 1
        return self._inner.execute_tool(call)

    def run_model(self, request):
        return self._inner.run_model(request)


class FailingExecutor:
    """A deterministic failure Executor (failure path): every tool fails."""

    def __init__(self):
        self.calls = 0

    def execute_tool(self, call):
        self.calls += 1
        return ToolResult(tool_call=call, success=False, error="boom")

    def run_model(self, request):
        return ModelResponse(model="fake", content="", success=True)


class ObservingExecutor:
    """Asserts action.requested is durably observable when execute() is entered."""

    def __init__(self, bus):
        self.bus = bus
        self.saw_requested = False

    def execute_tool(self, call):
        self.saw_requested = any(e.event_type == "action.requested" for e in self.bus.history)
        return ToolResult(tool_call=call, success=True, output={"ok": True})

    def run_model(self, request):
        return ModelResponse(model="fake", content="", success=True)


class CrashingExecutor:
    """Simulates an interruption after the requested event, before the terminal."""

    def execute_tool(self, call):
        raise RuntimeError("crash mid-action")

    def run_model(self, request):
        raise RuntimeError("crash mid-action")


def make_ncs(user, agent, model, preferences=()):
    store = MemoryStore(os.path.join(tempfile.mkdtemp(), "memory.db"))
    t = utcnow()
    for key, value, scope in preferences:
        store.record("preference", key, content={"key": key, "value": value},
                     scope=scope, now=t)
    return store, ContinuityProjector().project(t, store, user=user, agent=agent, model=model)


def main():
    print("Phase 8.3 golden task: action lifecycle / idempotency")
    user = UserIdentity(user_id="alice")
    agent = AgentIdentity(agent_id="researcher", role="researcher", version="1")
    model = ModelIdentity(model_id="model-a", family="fake", version="1")

    authority = ContinuityAuthority([
        Capability(name="create_book", description="publish a book", risk=Risk.WRITE),
    ])
    store, ncs = make_ncs(user, agent, model,
                          [("capability.create_book", "allow", "project/xyz")])

    executor = CountingExecutor()
    bus = EventBus()
    runner = ActionRunner(authority, executor, bus)

    action_a = ActionRequest(action_id="action-a", capability="create_book",
                             parameters={"title": "Wild Wings"}, scope="project/xyz",
                             requested_by=agent)

    # --- 1. first execution: exactly once, authoritative completion ----------
    result_a1 = runner.run(action_a, ncs, run_id="run/1", task_id="task/1")
    check(result_a1 is not None, "ALLOW -> A executes")
    check(executor.calls == 1, "exactly one executor call for A")
    check(result_a1.success and result_a1.action_id == "action-a",
          "A completed with an authoritative result")
    completed_a = [e for e in bus.history if e.event_type == "action.completed"
                   and e.payload.get("action_id") == "action-a"]
    check(len(completed_a) == 1, "one authoritative action.completed for A")

    # --- 2. retry A: idempotent ----------------------------------------------
    result_a2 = runner.run(action_a, ncs, run_id="run/2", task_id="task/2")
    check(executor.calls == 1, "retrying A does NOT re-execute (still one call)")
    check(result_a2 == result_a1, "the retry returns the SAME authoritative result")
    check(result_a2.action_id == "action-a", "same action_id across the retry")
    check(len([e for e in bus.history if e.event_type == "action.completed"
               and e.payload.get("action_id") == "action-a"]) == 1,
          "no second action.completed for A")

    # --- 3. reconstruction deterministic -------------------------------------
    rebuilt1 = reconstruct_action("action-a", bus.history)
    rebuilt2 = reconstruct_action("action-a", bus.history)
    check(rebuilt1 == result_a1, "reconstruction == original authoritative result")
    check(rebuilt1 == rebuilt2, "reconstruction is deterministic")

    # --- 4. a new action is independent --------------------------------------
    action_b = ActionRequest(action_id="action-b", capability="create_book",
                             parameters={"title": "Birds"}, scope="project/xyz",
                             requested_by=agent)
    result_b = runner.run(action_b, ncs, run_id="run/3", task_id="task/3")
    check(executor.calls == 2, "B is a new logical action and executes independently")
    check(result_b.action_id == "action-b" and result_b.action_id != "action-a",
          "B has a distinct action identity (not a retry of A)")

    # --- 5. failure is authoritative; not auto-retried ------------------------
    fail_executor = FailingExecutor()
    fail_bus = EventBus()
    fail_runner = ActionRunner(authority, fail_executor, fail_bus)
    action_f = ActionRequest(action_id="action-f", capability="create_book",
                             scope="project/xyz", requested_by=agent)
    result_f1 = fail_runner.run(action_f, ncs, run_id="run/4", task_id="task/4")
    check(result_f1 is not None and result_f1.success is False and result_f1.error == "boom",
          "a failed execution is an authoritative failure")
    check(len([e for e in fail_bus.history if e.event_type == "action.failed"
               and e.payload.get("action_id") == "action-f"]) == 1,
          "action.failed is emitted (not action.completed)")
    result_f2 = fail_runner.run(action_f, ncs, run_id="run/5", task_id="task/5")
    check(fail_executor.calls == 1, "a failed action is NOT auto-retried")
    check(result_f2 == result_f1,
          "retrying a failed action returns the existing authoritative failure")

    # --- 8.x observable action attempt (AD-050) -------------------------------
    # 1. event-before-side-effect
    obs_bus = EventBus()
    obs_exec = ObservingExecutor(obs_bus)
    obs_runner = ActionRunner(authority, obs_exec, obs_bus)
    obs_runner.run(ActionRequest(action_id="action-obs", capability="create_book",
                                 scope="project/xyz", requested_by=agent), ncs)
    check(obs_exec.saw_requested,
          "event-before-side-effect: action.requested is observable when execute() runs")

    # 2. terminal ordering: requested precedes the terminal event
    order = [e.event_type for e in bus.history]
    check(order.index("action.requested") < order.index("action.completed"),
          "success: action.requested precedes action.completed")
    forder = [e.event_type for e in fail_bus.history]
    check(forder.index("action.requested") < forder.index("action.failed"),
          "failure: action.requested precedes action.failed")

    # 3. crash visibility: attempted, never "failed", never invisible
    crash_bus = EventBus()
    crash_runner = ActionRunner(authority, CrashingExecutor(), crash_bus)
    try:
        crash_runner.run(ActionRequest(action_id="action-crash", capability="create_book",
                                       scope="project/xyz", requested_by=agent), ncs)
    except RuntimeError:
        pass
    check(len([e for e in crash_bus.history if e.event_type == "action.requested"]) == 1,
          "crash: exactly one action.requested is retained")
    check(len([e for e in crash_bus.history
               if e.event_type in ("action.completed", "action.failed")]) == 0,
          "crash: no terminal event")
    check(reconstruct_action("action-crash", crash_bus.history) is None,
          "crash: reconstruction is unknown, NOT 'failed'")
    check(action_attempted("action-crash", crash_bus.history),
          "crash: the attempt is recorded (requested with no terminal)")

    # 4. denied means unattempted
    deny_authority = ContinuityAuthority([
        Capability(name="create_book", description="publish", risk=Risk.WRITE),
        Capability(name="delete_book", description="delete", risk=Risk.DESTRUCTIVE),
    ])
    deny_bus = EventBus()
    deny_exec = CountingExecutor()
    deny_runner = ActionRunner(deny_authority, deny_exec, deny_bus)
    deny_runner.run(ActionRequest(action_id="action-deny", capability="delete_book",
                                  scope="project/xyz", requested_by=agent), ncs)
    store_neutral, ncs_neutral = make_ncs(user, agent, model)
    deny_runner.run(ActionRequest(action_id="action-approve", capability="create_book",
                                  scope="project/xyz", requested_by=agent), ncs_neutral)
    check(deny_exec.calls == 0, "DENY + APPROVAL_REQUIRED: the executor is never called")
    check(len([e for e in deny_bus.history if e.event_type == "action.requested"]) == 0,
          "DENY + APPROVAL_REQUIRED: no action.requested (unattempted)")
    store_neutral.close()

    # 5. idempotent retry emits no new attempt
    check(len([e for e in bus.history if e.event_type == "action.requested"
               and e.payload.get("action_id") == "action-a"]) == 1,
          "retrying A does NOT emit a second action.requested")

    store.close()
    print("\nPASS: Phase 8.3 action lifecycle/idempotency holds "
          "(same action_id -> exactly one execution; new action_id -> independent; "
          "failure is authoritative).")


if __name__ == "__main__":
    main()
