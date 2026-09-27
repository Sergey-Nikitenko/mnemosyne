"""Context capability pipeline — EXPERIMENTAL (items 5-10 of the context stack).

  5. Context Router     — goal + frontier + operation -> candidate evidence set
  6. Hot Working Context — the small active reasoning neighborhood
  7. Change Context     — h1 -> h2 deltas presented preferentially
  8. Decision Context   — evidence around the unresolved decision
  9. Context Packet     — the standardized model-facing projection
 10. Context Compiler   — compose all of it under a token/evidence budget

Deterministic, speculative, recoverable. These PROPOSE a presentation; they never
decide authority, freshness, or verification truth. A wrong selection here is
recoverable — the model requests another object.
"""
from __future__ import annotations

from capability.context import Reason, Tier, evidence_bundle, tier_presentation
from execution import workspace_intel as WI


# --- 5. Context Router -------------------------------------------------------
def _cand(kind, subject, reason, *, representation="REFERENCE", **extra):
    c = {"kind": kind, "subject": subject,
         "reason": getattr(reason, "value", reason),
         "freshness": "CURRENT", "epistemic": "MEASUREMENT",
         "authority": "DERIVED_DETERMINISTIC", "provenance": "INTEL",
         "representation": representation,
         "retrieval_handle": f"{kind.lower()}({subject})"}
    c.update(extra)
    return c


def _tier(c, intent):
    kind = c.get("kind")
    if kind == "SYMBOL_SOURCE" and intent in ("MUTATE", "VERIFY", "LOCATE"):
        return "FULL"
    if kind == "FILE" and c.get("reason") == "DIRECT_TARGET" and intent in ("READ", "MUTATE"):
        return "FULL"
    if kind == "WORKSPACE_MAP":
        return "SUMMARY"
    return "REFERENCE"


def _cost(kind):
    return "CHEAP" if kind in ("WORKSPACE_MAP", "SYMBOL_SOURCE", "REFERENCES",
                               "NEIGHBORHOOD", "TEST", "FILE") else "UNKNOWN"


def _est(c):
    return 4000 if c.get("representation") == "FULL" else 200


def route(root: str, goal: str, intent: str = "DISCOVER", subject: str = "",
          debt=None, limit: int = 12) -> dict:
    """Context Router — goal + INTENDED operation -> candidate evidence set.

    Deterministic, read-only, content-free (handles only). Each candidate carries
    kind / reason / tier / cost / freshness / epistemic / provenance / retrieval
    handle. It reports an honest UNRESOLVED case and a COST estimate; it never
    reads content and never decides what the model should see. A wrong candidate
    costs tokens, never correctness.

    `intent` is the operation class the model INTENDS (DISCOVER / LOCATE / READ /
    MUTATE / VERIFY / DECLARE) — an input, never a permission.
    """
    intent = (intent or "DISCOVER").upper()
    candidates: list[dict] = []

    if subject:
        src = WI.get_symbol_source(root, subject)
        if src:
            path = src["path"]
            candidates.append(_cand("SYMBOL_SOURCE", subject, Reason.DIRECT_TARGET,
                                    representation="FULL", symbol_hash=src["symbol_hash"],
                                    handle_detail={"path": path, "lineno": src["lineno"]}))
            candidates.append(_cand("FILE", path, Reason.DIRECT_TARGET,
                                    retrieval_handle=f"read_file({path})"))
            if intent in ("MUTATE", "VERIFY", "LOCATE"):
                candidates.append(_cand("REFERENCES", subject, Reason.DEPENDENCY,
                                        count=len(WI.find_references(root, subject))))
                nb = WI.dependency_neighborhood(root, subject) or {}
                candidates.append(_cand("NEIGHBORHOOD", subject, Reason.DEPENDENCY,
                                        callers=len(nb.get("called_by", [])),
                                        calls=len(nb.get("calls", []))))
            if intent in ("MUTATE", "VERIFY"):
                for t in WI.related_tests(root, symbol=subject):
                    candidates.append(_cand("TEST", t, Reason.TEST_IMPACT, covers=subject))
        else:
            candidates.append(_cand("UNRESOLVED", subject, Reason.DIRECT_TARGET,
                                    epistemic="UNKNOWN",
                                    note="symbol not found in the deterministic index"))

    for d in (debt or []):
        if d.get("path"):
            candidates.append(_cand("FILE", d["path"], Reason.UNRESOLVED_DECISION, debt=d))

    if intent in ("DISCOVER", "LOCATE") and not subject:
        candidates.append(_cand("WORKSPACE_MAP", ".", Reason.FRONTIER,
                                retrieval_handle="workspace_map()"))

    # goal-keyword fallback (the pre-port behavior) only when nothing matched
    if not candidates and goal:
        words = set((goal or "").lower().replace(",", " ").split())
        for name, locs in WI.build_symbol_index(root).items():
            if name.lower() in words:
                candidates.append(_cand("SYMBOL_SOURCE", name, Reason.DIRECT_TARGET,
                                        handle_detail={"path": locs[0]["path"]}))

    for c in candidates:
        c["cost"] = _cost(c["kind"])
        c["tier"] = _tier(c, intent)
        c["est_bytes"] = _est(c)

    seen, deduped = set(), []
    for c in candidates:
        key = (c["kind"], c["subject"])
        if key not in seen:
            seen.add(key)
            deduped.append(c)

    routed = deduped[:limit]
    return {"intent": intent, "goal": goal,
            "frontier": {"open_debt": len(debt or [])},
            "candidates": routed,
            "available_but_not_routed": [],  # no Working Set on the serve path
            "cost": {"candidates": len(routed),
                     "estimated_bytes": sum(c.get("est_bytes", 0) for c in routed),
                     "presentation": "handles only — no content is read"},
            "note": "proposal, not authority; a wrong candidate is recoverable"}


# --- 6. Hot Working Context --------------------------------------------------
class HotContext:
    """The small active reasoning neighborhood (recently-touched paths). Per-process."""

    def __init__(self, max_items: int = 8):
        self.max = max_items
        self._order: list = []

    def touch(self, path: str) -> None:
        if path in self._order:
            self._order.remove(path)
        self._order.append(path)
        if len(self._order) > self.max:
            self._order = self._order[-self.max:]

    def get(self) -> list:
        return list(self._order)


# --- 7. Change Context -------------------------------------------------------
def change_context(root: str, prev_manifest: dict) -> dict:
    """h1 -> h2: what changed since the model last saw the world. Thin, deterministic
    wrapper over the workspace-intelligence diff — presented preferentially so a full
    re-read of unchanged artifacts is not the default."""
    return WI.changed_since(root, prev_manifest)


# --- 8. Decision Context -----------------------------------------------------
def decision_context(root: str, decision: str, prev_manifest: dict | None = None) -> dict:
    """Evidence assembled around the unresolved decision (a symbol or module)."""
    if any(sep in decision for sep in ("/", "\\", ".")):
        vc = WI.verification_candidates(root, decision)
        return {"decision": decision, "reason": Reason.UNRESOLVED_DECISION.value,
                "tests": vc.get("direct_tests", [])}
    b = evidence_bundle(root, decision)
    b["reason"] = Reason.UNRESOLVED_DECISION.value
    if prev_manifest:
        b["change"] = WI.changed_since(root, prev_manifest).get("modified", [])
    return b


# --- 9. Context Packet -------------------------------------------------------
def context_packet(root: str, goal: str, frontier: str, decision: str | None,
                   hot: list | None = None, prev_manifest: dict | None = None,
                   obligations: list | None = None) -> dict:
    """The standardized model-facing projection: goal, frontier, decision, hot evidence,
    relationships, changes, obligations, and on-demand availability in one object."""
    return {
        "goal": goal,
        "frontier": frontier,
        "decision": decision,
        "hot_evidence": [{"path": p, "reason": Reason.CONTINUITY.value} for p in (hot or [])],
        "decision_context": decision_context(root, decision, prev_manifest) if decision else None,
        "relationships": WI.dependency_neighborhood(root, decision) if decision else None,
        "changes": WI.changed_since(root, prev_manifest) if prev_manifest else None,
        "obligations": obligations or [],
        "on_demand": {"indexed_symbols": len(WI.build_symbol_index(root))},
    }


# --- 10. Context Compiler ----------------------------------------------------
def compile_packet(root: str, goal: str, frontier: str = "", decision: str | None = None,
                   hot: list | None = None, prev_manifest: dict | None = None,
                   obligations: list | None = None, budget_bytes: int = 16000) -> dict:
    """Compose the packet and reduce it to a token/evidence budget.

    Deterministic and coarse: if the packet exceeds the budget, the decision context
    (the largest field) tiers down FULL -> SUMMARY -> REFERENCE. Full evidence is still
    one `evidence_bundle` call away — nothing is destroyed, only deferred."""
    packet = context_packet(root, goal, frontier, decision, hot, prev_manifest, obligations)

    def _size(obj) -> int:
        return len(str(obj).encode("utf-8"))

    dc = packet.get("decision_context")
    if _size(packet) > budget_bytes and isinstance(dc, dict):
        packet["decision_context"] = tier_presentation(dc, Tier.SUMMARY)
    if _size(packet) > budget_bytes and isinstance(dc, dict):
        packet["decision_context"] = tier_presentation(dc, Tier.REFERENCE)
    return packet
