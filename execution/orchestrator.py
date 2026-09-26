"""The orchestrator — Nexus's central nervous system.

The orchestrator OWNS sequencing and correlation; the capabilities OWN
execution. It may call `retriever.search(...)`, `executor.run_model(...)`,
`executor.execute_tool(...)`, `policy.decide_tool(...)`, `evaluator.evaluate(...)`,
`state` and `emit(...)` — but it NEVER does capability work itself: no subprocess,
no file I/O, no network, no vector store, no model SDK, no MCP client. That
boundary is enforced by tests/conformance/test_orchestrator_purity.py.

The loop (3.5 — verification + bounded replanning):

    plan -> retrieve -> act -> verify
                            ├─ PASS ──────────────► answer
                            └─ FAIL (replan_required)
                                    └─► run.replanned ─► act again (bounded)

A model may SUGGEST a tool call (ModelResponse.tool_calls). The orchestrator
DECIDES whether and when to invoke it, and the Phase 1 policy stays
authoritative on every attempt — replanning changes the plan, never the authority.
The evaluator observes and judges; the orchestrator interprets the verdict.
"""
from __future__ import annotations

import json

from dataclasses import asdict, dataclass, field

from core.contracts import (
    ActionRequest, AgentIdentity, ApprovalRequest, Event, ModelIdentity,
    ModelRequest, PolicyVerdict, Run, RunManifest, Step, StepStatus, Task,
    TaskStatus, ToolCall, Trace, UserIdentity, new_id, utcnow,
)
from core.events import EventBus, EventType
from core.state import RunState
from execution.instrumented import InstrumentedExecutor


@dataclass
class Outcome:
    """What one run produced: the answer, the run, its trace, the LIVE state
    (updated during execution), and the event log (the durable record)."""
    answer: str
    run: Run
    trace: Trace
    live_state: RunState
    events: list[Event] = field(default_factory=list)
    waiting: bool = False
    manifest: RunManifest | None = None


def _result_content(result) -> str:
    """The model-neutral text of a tool result (bounded plain data, never a
    provider object or a credential)."""
    if result.output is None:
        return result.error or ""
    if isinstance(result.output, str):
        return result.output
    return json.dumps(result.output)


def _result_invocation(result) -> tuple:
    """(name, arguments, correlation_id) of a result — a ToolResult's tool_call,
    or an ActionResult's capability/parameters. Keeps the conversation builder
    agnostic to which execution level produced the result."""
    tc = getattr(result, "tool_call", None)
    if tc is not None:
        return (tc.tool_name, dict(tc.arguments), tc.correlation_id)
    return (getattr(result, "capability", "?"),
            dict(getattr(result, "parameters", {})),
            getattr(result, "correlation_id", ""))


def _tool_follow_up(tool_results) -> list[dict]:
    """Build the model-neutral conversation turn that follows tool execution:
    the assistant's tool invocations first, then each correlated tool result.
    `correlation_id` links a result back to the invocation that caused it; the
    provider/adapter owns translating that into its own wire field (tool_call_id).
    """
    assistant_calls = [
        {"correlation_id": cid, "name": name, "arguments": args}
        for name, args, cid in (_result_invocation(r) for r in tool_results)
    ]
    tool_msgs = [
        {"role": "tool", "correlation_id": cid, "name": name, "content": _result_content(r)}
        for r, (name, args, cid) in zip(tool_results,
                                        (_result_invocation(r) for r in tool_results))
    ]
    return [{"role": "assistant", "tool_calls": assistant_calls}] + tool_msgs


def _action_to_dict(action: ActionRequest) -> dict:
    """Serialize the logical identity of a pending action for the continuation."""
    return {"action_id": action.action_id, "capability": action.capability,
            "parameters": dict(action.parameters),
            "correlation_id": action.correlation_id}


class Orchestrator:
    """Composes injected capabilities behind one deterministic, bounded loop."""

    def __init__(self, *, retriever, executor, policy, tools, evaluator,
                 bus=None, approvals=None, run_records=None, max_replans: int = 2,
                 max_tool_rounds: int = 8, continuation=None,
                 action_runner=None, ncs_provider=None) -> None:
        self.retriever = retriever      # .search(query, filters, k) -> RetrievalResult
        self.executor = executor        # raw capability (FakeExecutor, subprocess, ...)
        self.policy = policy            # PolicyEngine (tool gating)
        self.tools = tools              # control ToolRegistry (name -> ToolSpec, for risk)
        self.evaluator = evaluator      # .evaluate(...) -> Evaluation (judges, never executes)
        self.bus = bus or EventBus()
        self.approvals = approvals      # ApprovalStore (optional; None = approvals not wired)
        self.run_records = run_records  # RunRecordStore (optional; None = not persisted)
        self.max_replans = max_replans
        # The orchestration budget: how many tool ROUNDS (model -> tools cycles)
        # may run before a terminal model turn. The model may request another
        # round; it never decides that iteration is unbounded.
        self.max_tool_rounds = max_tool_rounds
        # Durable continuation checkpoint (optional): where an approval-paused run
        # resumes from, instead of restarting from the original request.
        self.continuation = continuation
        # Agency integration (Phase 3.x/8.x): when wired, the canonical task loop
        # routes each model tool proposal through the Phase-8 ActionRunner
        # (Authority -> action.requested -> tool.* -> action.completed). When None,
        # the legacy Phase-1-policy + Phase-3-tool path runs (backward compatible).
        self.action_runner = action_runner
        self.ncs_provider = ncs_provider  # callable() -> NexusContinuityState

    def _model_identity(self) -> ModelIdentity:
        """Which logical model configuration this executor runs — a ModelIdentity
        contract, never the provider config (AD-035)."""
        mi = getattr(self.executor, "model_identity", None)
        if isinstance(mi, ModelIdentity):
            return mi
        if isinstance(mi, str) and mi:
            return ModelIdentity(model_id=mi)
        return ModelIdentity()

    def run(self, task: Task) -> Outcome:
        run = Run(run_id=new_id("run"), task_id=task.task_id)
        state = RunState(run=run)
        trace = Trace(run_id=run.run_id)
        instr = InstrumentedExecutor(
            self.executor, self.bus, run_id=run.run_id, task_id=task.task_id)

        def emit(event_type: str, status: str, payload: dict) -> None:
            self.bus.publish(Event(
                event_id=new_id("evt"), event_type=event_type, timestamp=utcnow(),
                run_id=run.run_id, task_id=task.task_id, component="orchestrator",
                status=status, payload=payload,
            ))

        def step(name: str, work):
            s = Step(step_id=new_id("step"), name=name)
            run.steps.append(s)
            state.step_status[s.step_id] = StepStatus.RUNNING
            emit(EventType.STEP_STARTED, "running", {"step_id": s.step_id, "name": name})
            try:
                result = work()
            except Exception:
                # a NORMAL exception reaches a terminal step state (step.failed);
                # a hard process death leaves step.started with NO terminal event —
                # that distinction is how an interrupted operation is detected.
                s.status = StepStatus.FAIL
                state.step_status[s.step_id] = StepStatus.FAIL
                emit(EventType.STEP_FAILED, "failed", {"step_id": s.step_id, "name": name})
                raise
            s.status = StepStatus.PASS
            state.step_status[s.step_id] = StepStatus.PASS
            emit(EventType.STEP_COMPLETED, "success", {"step_id": s.step_id, "name": name})
            return result

        def build_request(retrieved, extra_messages=None) -> ModelRequest:
            messages = [
                {"role": "system", "content": "\n".join(c.text for c in retrieved.chunks)},
                {"role": "user", "content": task.title},
            ]
            messages.extend(extra_messages or [])
            return ModelRequest(messages=messages)

        def _ncs():
            return self.ncs_provider() if self.ncs_provider else None

        def _legacy_call(call, spec):
            verdict = self.policy.decide_tool(spec)
            reason = {
                PolicyVerdict.ALLOW: f"Policy allows a {spec.risk.value} operation",
                PolicyVerdict.DENY: f"Policy rejects a {spec.risk.value} operation",
                PolicyVerdict.APPROVAL_REQUIRED:
                    f"Policy requires approval for a {spec.risk.value} operation",
            }[verdict]
            emit(EventType.POLICY_DECISION, "success", {
                "tool": call.tool_name, "verdict": verdict.value, "risk": spec.risk.value,
                "executed": verdict == PolicyVerdict.ALLOW, "reason": reason,
                "call_id": call.call_id,
            })
            if verdict == PolicyVerdict.ALLOW:
                result = instr.execute_tool(call)
                trace.nodes.append({"type": "tool", "tool": call.tool_name,
                                    "verdict": "allow", "success": result.success})
                return result, False, None
            if verdict == PolicyVerdict.APPROVAL_REQUIRED:
                if self.approvals is not None:
                    granted = self.approvals.find_approved(
                        task.task_id, call.tool_name, spec.risk)
                    if granted is not None:
                        consumed = self.approvals.consume_approved(granted.approval_id)
                        if consumed is not None:
                            result = instr.execute_tool(call)
                            trace.nodes.append({"type": "tool", "tool": call.tool_name,
                                                "verdict": "approved", "success": result.success})
                            return result, False, None
                        trace.nodes.append({"type": "tool", "tool": call.tool_name,
                                            "verdict": "consumed_elsewhere"})
                        return None, False, None
                    approval = ApprovalRequest(
                        approval_id=new_id("appr"), task_id=task.task_id,
                        run_id=run.run_id, tool_name=call.tool_name, risk=spec.risk)
                    self.approvals.create(approval)
                    trace.nodes.append({"type": "tool", "tool": call.tool_name,
                                        "verdict": "approval_required",
                                        "approval_id": approval.approval_id})
                    return None, True, {"tool_name": call.tool_name,
                                        "arguments": dict(call.arguments),
                                        "correlation_id": call.correlation_id}
                emit(EventType.APPROVAL_REQUIRED, "pending", {"tool": call.tool_name})
                trace.nodes.append({"type": "tool", "tool": call.tool_name,
                                    "verdict": "approval_required"})
                return None, False, None
            # DENY
            trace.nodes.append({"type": "tool", "tool": call.tool_name, "verdict": "deny"})
            return None, False, None

        def _agency_call(call, spec):
            # The model proposes a tool operation; it becomes a logical ActionRequest
            # (action_id = A) and passes through the Phase-8 Authority + ActionRunner.
            action = ActionRequest(
                capability=call.tool_name,
                action_id=call.correlation_id or new_id("act"),
                parameters=dict(call.arguments),
                requested_by=AgentIdentity(agent_id=task.agent, role=task.agent),
                scope="",
                correlation_id=call.correlation_id,
            )
            verdict = self.action_runner.evaluate(action, _ncs())
            emit(EventType.POLICY_DECISION, "success", {
                "tool": call.tool_name, "verdict": verdict.verdict.value,
                "risk": spec.risk.value, "executed": verdict.verdict == PolicyVerdict.ALLOW,
                "reason": "; ".join(verdict.reasons), "call_id": call.call_id,
                "action_id": action.action_id,
            })
            if verdict.verdict == PolicyVerdict.ALLOW:
                result = self.action_runner.run(action, _ncs(),
                                                run_id=run.run_id, task_id=task.task_id)
                trace.nodes.append({"type": "tool", "tool": call.tool_name,
                                    "verdict": "allow",
                                    "success": result.success if result is not None else None,
                                    "action_id": action.action_id})
                return result, False, None
            if verdict.verdict == PolicyVerdict.APPROVAL_REQUIRED:
                if self.approvals is not None:
                    granted = self.approvals.find_approved(
                        task.task_id, call.tool_name, spec.risk)
                    if granted is not None:
                        consumed = self.approvals.consume_approved(granted.approval_id)
                        if consumed is not None:
                            result = self.action_runner.run(action, _ncs(),
                                                            run_id=run.run_id, task_id=task.task_id)
                            trace.nodes.append({"type": "tool", "tool": call.tool_name,
                                                "verdict": "approved",
                                                "success": result.success if result is not None else None,
                                                "action_id": action.action_id})
                            return result, False, None
                        trace.nodes.append({"type": "tool", "tool": call.tool_name,
                                            "verdict": "consumed_elsewhere"})
                        return None, False, None
                    approval = ApprovalRequest(
                        approval_id=new_id("appr"), task_id=task.task_id,
                        run_id=run.run_id, tool_name=call.tool_name, risk=spec.risk,
                        action_id=action.action_id)
                    self.approvals.create(approval)
                    trace.nodes.append({"type": "tool", "tool": call.tool_name,
                                        "verdict": "approval_required",
                                        "approval_id": approval.approval_id,
                                        "action_id": action.action_id})
                    return None, True, _action_to_dict(action)
                emit(EventType.APPROVAL_REQUIRED, "pending", {"tool": call.tool_name})
                trace.nodes.append({"type": "tool", "tool": call.tool_name,
                                    "verdict": "approval_required"})
                return None, False, None
            # DENY
            trace.nodes.append({"type": "tool", "tool": call.tool_name, "verdict": "deny"})
            return None, False, None

        def run_tools(tool_calls):
            results = []
            waiting = False
            pending = None
            for call in tool_calls:
                # Nexus owns the physical execution identity: re-mint per ATTEMPT so
                # a replayed invocation is a distinct physical call (call_id = C).
                call.call_id = new_id("call")
                spec = self.tools.get(call.tool_name)
                if self.action_runner is not None:
                    result, waiting, pending = _agency_call(call, spec)
                else:
                    result, waiting, pending = _legacy_call(call, spec)
                if result is not None:
                    results.append(result)
                if waiting:
                    break
            return results, waiting, pending

        def verify(tool_results, attempt):
            evaluation = self.evaluator.evaluate(tool_results=tool_results)
            payload = {"passed": evaluation.passed, "reason": evaluation.reason,
                       "replan_required": evaluation.replan_required}
            emit(EventType.EVALUATION_COMPLETED, "success", {**payload, "attempt": attempt})
            state.evaluations.append(payload)
            trace.nodes.append({"type": "verify", "passed": evaluation.passed,
                                "reason": evaluation.reason, "attempt": attempt})
            return evaluation

        def do_retrieve():
            emit(EventType.RETRIEVAL_REQUESTED, "running", {"query": task.title})
            result = self.retriever.search(task.title, k=5)
            emit(EventType.RETRIEVAL_COMPLETED, "success", {"chunks": len(result.chunks)})
            trace.nodes.append({"type": "retrieve", "chunks": len(result.chunks)})
            return result

        def do_model(retrieved, extra_messages=None, final=False):
            resp = instr.run_model(build_request(retrieved, extra_messages))
            trace.nodes.append({"type": "model", "tool_calls": len(resp.tool_calls),
                                "final": final})
            return resp

        # The manifest captures the INPUTS that define this run BEFORE the first
        # capability decision (AD-029): the events below record "what happened";
        # the manifest records "what configuration defined it".
        def _snapshot(component, fallback="unknown"):
            fn = getattr(component, "snapshot", None)
            return fn() if callable(fn) else fallback

        manifest = RunManifest(
            run_id=run.run_id,
            task_id=task.task_id,
            task_title=task.title,
            knowledge=_snapshot(self.retriever),
            policy=getattr(getattr(self.policy, "rules", None), "version", "policy@1"),
            model=self._model_identity(),
            tools=_snapshot(self.tools),
            router="",
            max_replans=self.max_replans,
            user=UserIdentity(user_id=task.user),
            agent=AgentIdentity(agent_id=task.agent, role=task.agent),
            parent_run_id=task.parent_run_id,
        )
        if self.run_records is not None:
            self.run_records.record_manifest(run.run_id, task.task_id, manifest)
        emit(EventType.RUN_MANIFEST, "success", asdict(manifest))
        emit(EventType.RUN_STARTED, "success", {})
        step("plan", lambda: trace.nodes.append({
            "type": "plan", "steps": ["retrieve", "act", "verify", "replan"],
            "max_replans": self.max_replans,
        }))

        answer = ""
        attempt = 0
        # durable approval continuation: load once — an approval pause RESUMES the
        # interrupted run; a fresh task (or session/process restart with no pending
        # approval) starts from the original request.
        cont = None
        if self.continuation is not None:
            cont = self.continuation.load(task.task_id)

        while True:  # the replan loop
            attempt += 1
            retrieved = step("retrieve", do_retrieve)
            conversation: list[dict] = []  # assistant invocations + correlated results
            tool_results: list = []        # every result across rounds, for verification
            rounds = 0
            pending = None
            if cont is not None:
                conversation = cont["conversation"]
                rounds = cont["rounds"]
                pending = cont["pending_call"]
                cont = None  # the checkpoint is consumed by this resume
            if pending is not None:
                # approval resumes the EXACT pending thing (logical action or
                # physical tool call); it never authorizes whatever the model
                # proposes next.
                if self.action_runner is not None and "action_id" in pending:
                    action = ActionRequest(
                        capability=pending["capability"], action_id=pending["action_id"],
                        parameters=pending["parameters"],
                        requested_by=AgentIdentity(agent_id=task.agent, role=task.agent),
                        scope="", correlation_id=pending["correlation_id"])
                    spec = self.tools.get(action.capability)
                    result = None
                    granted = self.approvals.find_approved(
                        task.task_id, action.capability, spec.risk)
                    if granted is not None:
                        consumed = self.approvals.consume_approved(granted.approval_id)
                        if consumed is not None:
                            result = self.action_runner.run_approved(
                                action, _ncs(), run_id=run.run_id, task_id=task.task_id)
                    results = [result] if result is not None else []
                    tool_results.extend(results)
                    conversation.extend(_tool_follow_up(results))
                    rounds += 1
                    self.continuation.clear(task.task_id)
                else:
                    pending_tool = ToolCall(tool_name=pending["tool_name"],
                                            arguments=pending["arguments"],
                                            correlation_id=pending["correlation_id"])
                    results, _waiting, _pending = step("tool", lambda: run_tools([pending_tool]))
                    tool_results.extend(results)
                    conversation.extend(_tool_follow_up(results))
                    rounds += 1
                    self.continuation.clear(task.task_id)

            response = step("model", lambda: do_model(retrieved, conversation))

            # iteration, not autonomy: repeat the model -> tools cycle while the
            # model keeps proposing tool calls, bounded by max_tool_rounds. A final
            # model response (no tool calls) is NOT a tool round.
            while response.tool_calls:
                if rounds >= self.max_tool_rounds:
                    reason = (f"tool-round budget exhausted after "
                              f"{self.max_tool_rounds} round(s)")
                    state.task_status = TaskStatus.FAILED
                    emit(EventType.RUN_FAILED, "failed", {"reason": reason})
                    trace.nodes.append({"type": "answer", "answer": "", "reason": reason})
                    return Outcome(answer="", run=run, trace=trace, live_state=state,
                                   events=list(self.bus.history), manifest=manifest)
                rounds += 1
                results, waiting, pending_call = step(
                    "tool", lambda: run_tools(response.tool_calls))
                if waiting:
                    if self.continuation is not None:
                        self.continuation.save(
                            task.task_id, conversation, pending_call, rounds)
                    trace.nodes.append({"type": "waiting"})
                    return Outcome(answer="", run=run, trace=trace, live_state=state,
                                   events=list(self.bus.history), waiting=True,
                                   manifest=manifest)
                tool_results.extend(results)
                conversation.extend(_tool_follow_up(results))
                response = step("model", lambda: do_model(retrieved, conversation))

            evaluation = step("verify", lambda: verify(tool_results, attempt))

            if evaluation.passed:
                answer = response.content
                break

            if evaluation.replan_required and state.replan_count < self.max_replans:
                state.replan_count += 1
                emit(EventType.RUN_REPLANNED, "running", {"attempt": attempt + 1})
                trace.nodes.append({"type": "replan", "attempt": attempt + 1})
                continue

            # terminal failure: replan not requested, or budget exhausted
            state.task_status = TaskStatus.FAILED
            emit(EventType.RUN_FAILED, "failed", {"reason": evaluation.reason})
            trace.nodes.append({"type": "answer", "answer": ""})
            return Outcome(answer="", run=run, trace=trace, live_state=state,
                           events=list(self.bus.history), manifest=manifest)

        trace.nodes.append({"type": "answer", "answer": answer})
        state.task_status = TaskStatus.DONE
        emit(EventType.RUN_COMPLETED, "success", {"answer": answer})
        return Outcome(answer=answer, run=run, trace=trace, live_state=state,
                       events=list(self.bus.history), manifest=manifest)
