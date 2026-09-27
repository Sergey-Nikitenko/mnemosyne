"""Coherence tools — EXPERIMENTAL (speculative, recoverable).

The coherence layer's read-side: the menu (`next_mechanical_options`), the
rejection explanation (`explain_rejection`), the single-capability inspector
(`capability_explain`), and the mechanical consistency checker
(`house_consistency_check`). All render from ONE GateView, never a parallel
hardcoded interpretation of the governors.

Deterministic, no LLM, no side effect. After the Progress Obligation retirement,
these report MECHANICAL FACTS (debt, blocked, cost telemetry) — never pressure
to mutate. Delete this module and every Mnemosyne semantic is intact.
"""
from __future__ import annotations

from core.contracts import Risk
from capability.epistemic import FIELD_EPISTEMICS
from capability.cost import EconomicClass, cost_profile
from capability.gate_view import GateView
from capability.purchase import EVIDENCE_CLASS
from capability.work_capsule import work_capsule
from execution.commitment import (DISCOVERY_TOOLS, MUTATION_TOOLS, QUERY_TOOLS,
                                  VERIFY_TOOLS)


def _invert(cls_map: dict) -> dict:
    out: dict = {}
    for tool, cls in cls_map.items():
        out.setdefault(cls, []).append(tool)
    return out


# evidence classes that overlap: DERIVED from the single purchase.EVIDENCE_CLASS
# source (inverted), so the C027 report can never drift from the repurchase gate.
EVIDENCE_CLASSES = _invert(EVIDENCE_CLASS)


def _cost_telemetry(events) -> dict:
    """Cost telemetry DERIVED from the durable log — the observation half that
    survived the Progress Obligation retirement. Facts, never pressure."""
    t = {"evidence_ops": 0, "mutations": 0, "verifications": 0, "failed": 0}
    for e in events or []:
        p = e.payload or {}
        if e.event_type != "tool.completed":
            continue
        tool = p.get("tool", "")
        ok = bool(p.get("success"))
        if tool in MUTATION_TOOLS:
            t["mutations"] += 1
        elif tool in VERIFY_TOOLS:
            t["verifications"] += 1
        elif tool in DISCOVERY_TOOLS or tool in QUERY_TOOLS:
            t["evidence_ops"] += 1
        if not ok:
            t["failed"] += 1
    return t


def next_mechanical_options(root, events, capabilities, gate_view) -> dict:
    caps = work_capsule(root, events, capabilities)
    debt = bool(caps["verification_debt"])
    blocked = bool(caps["blocked_on"])
    phase = ("REPAIR" if debt else
             ("BLOCKED" if blocked else
              ("ACTIVE_PROGRESS" if caps["frontier"]["status"] == "open" else "IDLE")))
    menu = gate_view.menu(debt=debt, blocked=blocked) if isinstance(gate_view, GateView) \
        else {"available": ["INSPECT", "DECIDE", "MUTATE", "QUERY", "STOP"],
              "unavailable": []}
    return {"world": caps["world_version"]["hash"], "phase": phase,
            "debt": [d["path"] for d in caps["verification_debt"]],
            "cost": _cost_telemetry(events),
            "available": menu["available"], "unavailable": menu["unavailable"]}


def explain_rejection(gate_view, events, call_id) -> dict:
    """Explain a rejection from its durable record. Never makes the model
    reverse-engineer the governor. (The retired commitment_required verdict is
    no longer emitted; this explains the verdicts that still occur.)"""
    for e in events or []:
        p = e.payload or {}
        if e.event_type == "policy.decision" and p.get("call_id") == call_id:
            verdict = p.get("verdict", "")
            if verdict == "approval_required":
                return {"call_id": call_id, "verdict": verdict,
                        "reason": p.get("reason", ""),
                        "allowed_operation_classes": ["wait for operator approval"]}
            if verdict == "unknown":
                return {"call_id": call_id, "verdict": verdict,
                        "reason": p.get("reason", ""),
                        "allowed_operation_classes": ["use an advertised tool name"]}
            return {"call_id": call_id, "verdict": verdict,
                    "reason": p.get("reason", ""), "allowed_operation_classes": []}
    return {"call_id": call_id, "verdict": "unknown",
            "reason": "no rejection on record for this call_id",
            "allowed_operation_classes": []}


def capability_explain(gate_view, name) -> dict:
    c = gate_view.classify(name)
    if not c["registered"]:
        return {"tool": name, "registered": False}
    admit = gate_view.admit(name)
    return {"tool": name, "registered": True, "risk": c["risk"],
            "operation_class": c["operation_class"],
            "cost": cost_profile(name),
            "policy_verdict": admit["verdict"], "reasons": admit["reasons"],
            "output_epistemics": FIELD_EPISTEMICS.get(name, {}),
            "description": c.get("description", "")}


def house_consistency_check(gate_view) -> dict:
    findings = []
    # C002: classification sets reference tools that aren't registered
    for cls, tools in (("DISCOVERY", DISCOVERY_TOOLS), ("QUERY", QUERY_TOOLS),
                       ("MUTATION", MUTATION_TOOLS), ("VERIFY", VERIFY_TOOLS)):
        for t in sorted(tools):
            if not gate_view.classify(t)["registered"]:
                findings.append({"id": "C002", "severity": "FAIL", "tool": t,
                                 "message": f"in {cls}_TOOLS but not registered"})
    # C027: overlapping evidence classes (informs the repurchase flag, not a block)
    for cls, tools in EVIDENCE_CLASSES.items():
        if len(tools) > 1:
            findings.append({"id": "C027", "severity": "WARN", "class": cls,
                             "tools": tools,
                             "message": "overlapping evidence — flagged, never blocked"})
    # C028: a WORLD_MUTATING capability must be registered as WRITE risk
    for name, spec in gate_view._specs.items():
        if cost_profile(name)["economic_class"] == EconomicClass.WORLD_MUTATING:
            if getattr(getattr(spec, "risk", None), "value", None) != Risk.WRITE.value:
                findings.append({"id": "C028", "severity": "FAIL", "tool": name,
                                 "message": "WORLD_MUTATING but not registered WRITE risk"})
    return {"findings": findings, "count": len(findings),
            "consistent": not any(f["severity"] == "FAIL" for f in findings)}
