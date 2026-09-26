"""Federated delegation — a bounded request, never authority (AD-048).

Delegation has TWO independent authority decisions:

    A authority  ->  DelegationRequest  ->  B authority  ->  DelegationVerdict

- DelegationSender (A-side) authorizes SENDING: if A's own authority does not
  permit the capability, the request never crosses (federation is not a bypass).
- DelegationReceiver (B-side) independently authorizes ACCEPTING: it maps the
  bounded request into B-local action semantics, applies B's own authority, checks
  scope, and returns a DelegationVerdict — never an execution.

ALLOW at this boundary means "B accepts the delegation as eligible"; it does not
mean anything executed. Neither A's permission, nor B's peer claims, nor the
request's self-claims confer authority inside the other domain.
"""
from __future__ import annotations

from core.contracts import (
    ActionRequest, DelegationRequest, DelegationVerdict, PolicyVerdict, new_id,
)


class DelegationSender:
    """A's outbound gate: authorize sending, then mint a bounded request."""

    def __init__(self, authority) -> None:
        self.authority = authority  # A's Authority protocol: evaluate(action, ncs)

    def send(self, *, peer, capability, parameters=None, scope="",
             requested_by="", ncs) -> DelegationRequest | None:
        verdict = self.authority.evaluate(
            ActionRequest(capability=capability, parameters=dict(parameters or {}),
                          scope=scope), ncs)
        if verdict.verdict != PolicyVerdict.ALLOW:
            return None  # A's authority does not permit this delegation — never crosses
        return DelegationRequest(
            delegation_id=new_id("dlg"),
            peer_id=peer.peer_id,
            capability=capability,
            parameters=dict(parameters or {}),
            scope=scope,
            requested_by=requested_by,
        )


class DelegationReceiver:
    """B's inbound gate: apply B's own authority, never the request's claims."""

    def __init__(self, authority, scope: str = "") -> None:
        self.authority = authority  # B's Authority protocol: evaluate(action, ncs)
        self.scope = scope          # B's accepted local scope (never broadened)

    def evaluate(self, request: DelegationRequest, ncs) -> DelegationVerdict:
        if request.scope != self.scope:
            return DelegationVerdict(PolicyVerdict.DENY,
                                     ["scope_mismatch", f"receiver scope '{self.scope}'"])
        # map the bounded request into B-local action semantics and apply B's
        # authority. request.provenance / requested_by are deliberately NOT read.
        verdict = self.authority.evaluate(
            ActionRequest(capability=request.capability,
                          parameters=dict(request.parameters),
                          scope=request.scope), ncs)
        return DelegationVerdict(verdict.verdict, list(verdict.reasons))
