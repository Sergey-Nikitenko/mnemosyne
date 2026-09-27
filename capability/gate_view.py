"""GateView — ONE authoritative read-only projection of "what is admissible now".

The coherence layer's anchor. Several governors — ToolRegistry (risk),
PolicyEngine (verdicts), and the derived debt/blocked state — previously had
multiple parallel re-encodings. GateView is a SINGLE projection that reads the
same sources the orchestrator enforces with, so the menu, the explanation, and
`capability_explain` cannot drift from what actually happens.

It decides nothing and writes nothing: it only renders the trusted-core truth.
It imports the classification sets directly from `execution.commitment` (the
source), never a copy. The classification is TELEMETRY, not coercion — no
commitment budget, no "force mutation" escape routes. Delete it and every
Mnemosyne semantic is intact.
"""
from __future__ import annotations

from core.contracts import PolicyVerdict
from execution.commitment import (DISCOVERY_TOOLS, MUTATION_TOOLS, QUERY_TOOLS,
                                  VERIFY_TOOLS)


def operation_class(name: str) -> str:
    if name in MUTATION_TOOLS:
        return "MUTATE"
    if name in VERIFY_TOOLS:
        return "VERIFY"
    if name in QUERY_TOOLS:
        return "QUERY"
    if name in DISCOVERY_TOOLS:
        return "DISCOVERY"
    return "OTHER"


class GateView:
    def __init__(self, specs, policy=None) -> None:
        self._specs = {getattr(s, "name", None): s for s in (specs or [])}
        self._policy = policy

    def classify(self, name: str) -> dict:
        spec = self._specs.get(name)
        if spec is None:
            return {"registered": False, "risk": None, "operation_class": "UNKNOWN"}
        return {"registered": True, "risk": getattr(spec.risk, "value", None),
                "operation_class": operation_class(name),
                "description": getattr(spec, "description", "")}

    def admit(self, name: str) -> dict:
        c = self.classify(name)
        if not c["registered"]:
            return {**c, "verdict": "UNKNOWN", "reasons": ["NOT_REGISTERED"]}
        verdict = PolicyVerdict.ALLOW
        reasons = []
        spec = self._specs.get(name)
        if self._policy is not None:
            verdict = self._policy.decide_tool(spec)
            if verdict == PolicyVerdict.APPROVAL_REQUIRED:
                reasons.append("POLICY_APPROVAL_REQUIRED")
            elif verdict == PolicyVerdict.DENY:
                reasons.append("POLICY_DENY")
        return {**c, "verdict": verdict.value, "reasons": reasons}

    def menu(self, *, debt=False, blocked=False) -> dict:
        """The doors that currently exist, derived from one place. Mechanical
        facts only (open verification debt, pending approvals) — never a pressure
        to mutate. It never names a specific action to take."""
        available = ["INSPECT", "DECIDE", "MUTATE", "QUERY", "STOP"]
        unavailable = []
        if debt:
            available += ["REVERIFY", "REVERT"]
            unavailable.append("COMPLETE — OPEN_VERIFICATION_DEBT")
        if blocked:
            unavailable.append("MUTATE — approval pending")
        return {"available": sorted(set(available)), "unavailable": unavailable}
