"""Controlled action execution + lifecycle (AD-041 / AD-042).

The execution half of the agency boundary. The ActionRunner does NOT decide — the
Authority already did. It connects the Authority's verdict to the existing
Executor and records one authoritative terminal event:

    ActionRequest -> Authority -> (ALLOW) -> Executor -> ActionResult
                    -> action.completed | action.failed

Lifecycle (the smallest authoritative one Nexus needs):

    proposed -> authorized -> executing -> completed | failed

Idempotency (AD-042): the ActionRequest's `action_id` is the identity of the
logical action. If an authoritative terminal event already exists for that id, the
runner returns the reconstructed result WITHOUT re-executing — a retry never
becomes a second execution, and a failed action is never auto-retried. A new
action_id is a new logical action and executes independently.

It is deliberately boring: no automatic retries, no recovery planner, no
compensation planner, no learning, no capability discovery, no planning, no model
routing, no new persistence. Execution does not silently become learning.
"""
from __future__ import annotations

from dataclasses import asdict

from core.contracts import (
    ActionRequest, ActionResult, Event, PolicyVerdict, ToolCall, new_id, utcnow,
)
from core.events import EventBus, EventType
from core.state import reconstruct_action

_TERMINAL = {EventType.ACTION_COMPLETED, EventType.ACTION_FAILED}


class ActionRunner:
    """Composes an Authority (decides) + an Executor (executes) + a bus (records)."""

    def __init__(self, authority, executor, bus=None) -> None:
        self.authority = authority          # Authority protocol: evaluate(action, ncs)
        self.executor = executor            # Executor protocol: execute_tool(call)
        self.bus = bus or EventBus()

    def _events(self) -> list[Event]:
        load = getattr(self.bus, "load_events", None)
        return load() if callable(load) else list(self.bus.history)

    def _find_terminal(self, action_id: str) -> Event | None:
        found = None
        for ev in self._events():
            if ev.event_type in _TERMINAL and ev.payload.get("action_id") == action_id:
                found = ev
        return found

    def _emit_requested(self, action: ActionRequest, action_id: str,
                        run_id: str, task_id: str) -> None:
        """Record the attempt durably BEFORE the Executor may cause a side effect."""
        self.bus.publish(Event(
            event_id=new_id("evt"),
            event_type=EventType.ACTION_REQUESTED,
            timestamp=utcnow(),
            run_id=run_id,
            task_id=task_id,
            component="action",
            status="running",
            payload={
                "action_id": action_id,
                "capability": action.capability,
                "parameters": dict(action.parameters),
                "scope": action.scope,
                "requested_by": action.requested_by.key,
            },
        ))

    def run(self, action: ActionRequest, ncs, *, run_id: str = "",
            task_id: str = "") -> ActionResult | None:
        action_id = action.action_id or new_id("act")
        if self._find_terminal(action_id) is not None:
            # Idempotent: this logical action already has an authoritative terminal
            # outcome. Return it — never a second execution, never an auto-retry.
            return reconstruct_action(action_id, self._events())
        verdict = self.authority.evaluate(action, ncs)
        if verdict.verdict != PolicyVerdict.ALLOW:
            # DENY / APPROVAL_REQUIRED: the verdict is authoritative — no execution,
            # no terminal event, and no action.requested.
            return None
        # the attempt is durably recorded BEFORE the side effect can occur (AD-050)
        self._emit_requested(action, action_id, run_id, task_id)
        tool_result = self.executor.execute_tool(
            ToolCall(tool_name=action.capability, arguments=dict(action.parameters)))
        now = utcnow()
        result = ActionResult(
            action_id=action_id,
            capability=action.capability,
            success=tool_result.success,
            output=tool_result.output,
            error=tool_result.error,
            parameters=dict(action.parameters),
            requested_by=action.requested_by.key,
            scope=action.scope,
            run_id=run_id,
            task_id=task_id,
            completed_at=now.isoformat(),
        )
        event_type = (EventType.ACTION_COMPLETED if tool_result.success
                      else EventType.ACTION_FAILED)
        self.bus.publish(Event(
            event_id=new_id("evt"),
            event_type=event_type,
            timestamp=now,
            run_id=run_id,
            task_id=task_id,
            component="action",
            status="success" if tool_result.success else "failed",
            payload=asdict(result),
        ))
        return result
