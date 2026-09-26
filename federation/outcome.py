"""Federated outcome — B reports, A records (AD-049).

    B ActionResult -> federation translation -> FederatedOutcome -> A receipt

- DelegationService (B-side) executes an ACCEPTED delegation through B's own
  ActionRunner (re-authorizing at execution time) and translates the ActionResult
  into a bounded FederatedOutcome. B mints its own action identity; A never does.
- FederatedOutcomeRecorder (A-side) records the report as an authoritative
  `federation.outcome.received` event — exactly-once per delegation_id, and a
  conflicting report never overwrites history.

"B reported X" is the only proposition A may authoritatively record; it is never
promoted into "A observed X".
"""
from __future__ import annotations

from dataclasses import asdict

from core.contracts import (
    ActionRequest, Event, FederatedOutcome, PolicyVerdict, utcnow,
)
from core.events import EventType
from core.state import reconstruct_federated_outcome


class DelegationService:
    """B-side: execute an accepted delegation via B's own Phase 8 machinery."""

    def __init__(self, receiver, action_runner) -> None:
        self.receiver = receiver            # DelegationReceiver (10.2)
        self.action_runner = action_runner  # ActionRunner (8.x)

    def execute(self, delegation, ncs_b, *, run_id="", task_id="") -> FederatedOutcome | None:
        verdict = self.receiver.evaluate(delegation, ncs_b)
        if verdict.verdict != PolicyVerdict.ALLOW:
            return None  # not accepted
        action = ActionRequest(capability=delegation.capability,
                               parameters=dict(delegation.parameters),
                               scope=delegation.scope)
        result = self.action_runner.run(action, ncs_b, run_id=run_id, task_id=task_id)
        if result is None:
            # accepted, but B's current authority refused at execution time
            return FederatedOutcome(
                delegation_id=delegation.delegation_id,
                peer_id=delegation.peer_id,
                status="reported_failure",
                error="refused at execution",
            )
        return FederatedOutcome(
            delegation_id=delegation.delegation_id,
            peer_id=delegation.peer_id,
            status="reported_success" if result.success else "reported_failure",
            remote_action_id=result.action_id,
            output=result.output,
            error=result.error,
        )


class FederatedOutcomeRecorder:
    """A-side: record a remote report exactly once; reject conflicting reports."""

    def __init__(self, bus) -> None:
        self.bus = bus

    def _events(self):
        load = getattr(self.bus, "load_events", None)
        return load() if callable(load) else list(self.bus.history)

    def record(self, outcome: FederatedOutcome) -> FederatedOutcome | None:
        existing = reconstruct_federated_outcome(outcome.delegation_id, self._events())
        if existing is not None:
            return existing if existing == outcome else None  # idempotent vs conflict
        self.bus.publish(Event(
            event_id=f"federation.outcome.received:{outcome.delegation_id}",
            event_type=EventType.FEDERATION_OUTCOME_RECEIVED,
            timestamp=utcnow(), run_id="", task_id="",
            component="federation", status="received", payload=asdict(outcome),
        ))
        return outcome
