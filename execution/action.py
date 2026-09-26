"""Controlled action execution — ALLOW runs; DENY/APPROVAL never execute (AD-041).

The execution half of the agency boundary. The ActionRunner does NOT decide — the
Authority already did. It connects the Authority's verdict to the existing
Executor and records one authoritative, reconstructible completion event:

    ActionRequest -> Authority -> (ALLOW) -> Executor -> ActionResult -> action.completed

It is deliberately boring: no retries, no learning, no capability discovery, no
planning, no model routing, no autonomous loops, no new persistence. It runs
exactly one allowed action through the injected Executor and emits exactly one
event. Neither the model nor the executor can bypass the verdict — this runner is
the only path from verdict to execution.
"""
from __future__ import annotations

from dataclasses import asdict

from core.contracts import (
    ActionRequest, ActionResult, Event, PolicyVerdict, ToolCall, new_id, utcnow,
)
from core.events import EventBus, EventType


class ActionRunner:
    """Composes an Authority (decides) + an Executor (executes) + a bus (records)."""

    def __init__(self, authority, executor, bus=None) -> None:
        self.authority = authority          # Authority protocol (core): evaluate(action, ncs)
        self.executor = executor            # Executor protocol (core): execute_tool(call)
        self.bus = bus or EventBus()

    def run(self, action: ActionRequest, ncs, *, run_id: str = "",
            task_id: str = "") -> ActionResult | None:
        verdict = self.authority.evaluate(action, ncs)
        if verdict.verdict != PolicyVerdict.ALLOW:
            # DENY / APPROVAL_REQUIRED: the verdict is authoritative — no execution,
            # no completion event.
            return None
        tool_result = self.executor.execute_tool(
            ToolCall(tool_name=action.capability, arguments=dict(action.parameters)))
        now = utcnow()
        result = ActionResult(
            action_id=new_id("act"),
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
        self.bus.publish(Event(
            event_id=new_id("evt"),
            event_type=EventType.ACTION_COMPLETED,
            timestamp=now,
            run_id=run_id,
            task_id=task_id,
            component="action",
            status="success" if tool_result.success else "failed",
            payload=asdict(result),
        ))
        return result
