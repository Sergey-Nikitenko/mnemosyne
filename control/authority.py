"""Continuity-aware authority — the model proposes, Nexus decides (AD-040).

The reference Authority validates a model's ActionRequest against exactly two
inputs and nothing else: the static policy gate (risk + allow/denylist, via the
existing PolicyEngine) and the durable continuity state (the NCS). It is a pure
control-plane component — it returns an ActionVerdict and causes nothing.

    model -> ActionRequest -> ContinuityAuthority(action, NCS) -> ActionVerdict

Properties (proven by tests/golden/test_phase8_capability.py):
- model-independent — never reads ncs.model, so a model swap never changes a verdict;
- continuity-aware — a durable Preference (key `capability.<name>`, scoped to the
  action's `scope`) overrides the static risk verdict;
- Decision != Action — returns a verdict, never executes or emits (control-plane purity);
- no self-authorization — the model's `claims` are never read;
- static DENY is final — continuity refines; it never softens a destructive/denylist DENY.
"""
from __future__ import annotations

from core.contracts import (
    ActionRequest, ActionVerdict, Capability, NexusContinuityState, PolicyVerdict,
)
from .policy import PolicyEngine
from .tools import ToolSpec


_VERDICT_BY_VALUE = {
    "allow": PolicyVerdict.ALLOW,
    "deny": PolicyVerdict.DENY,
    "approval_required": PolicyVerdict.APPROVAL_REQUIRED,
}


class ContinuityAuthority:
    """Pure authority: static risk gate + durable continuity refinement."""

    def __init__(self, capabilities, policy: PolicyEngine | None = None) -> None:
        self._capabilities = {c.name: c for c in capabilities}
        self._policy = policy or PolicyEngine()

    def evaluate(self, action: ActionRequest, ncs: NexusContinuityState) -> ActionVerdict:
        cap = self._capabilities[action.capability]
        base = self._policy.decide_tool(
            ToolSpec(name=cap.name, description=cap.description,
                     risk=cap.risk, parameters=cap.parameters))
        reasons = [f"risk={cap.risk.value}"]
        if base == PolicyVerdict.DENY:
            reasons.append("static policy DENY is final")
            return ActionVerdict(PolicyVerdict.DENY, reasons)
        override = self._preference_override(action, ncs)
        if override is not None:
            reasons.append(f"continuity preference 'capability.{action.capability}' "
                           f"scope '{action.scope}' -> {override}")
            return ActionVerdict(_VERDICT_BY_VALUE[override], reasons)
        reasons.append(f"static policy {base.value}")
        return ActionVerdict(base, reasons)

    def _preference_override(self, action: ActionRequest, ncs: NexusContinuityState):
        key = f"capability.{action.capability}"
        for pref in ncs.preferences:
            if pref.key != key:
                continue
            if pref.scope and pref.scope != action.scope:
                continue
            if pref.value not in _VERDICT_BY_VALUE:
                continue
            return pref.value
        return None
