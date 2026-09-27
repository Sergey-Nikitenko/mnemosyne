"""Decision Registry + Decision Invalidation — EXPERIMENTAL (speculative, recoverable).

Toy #5 (frozen order), the "challengeable intelligence" core. The model authors
a SEMANTIC conclusion (a claim + what supports it); Nexus keeps only its
MECHANICAL validity envelope — the freshness of each supporting evidence anchor.
No reasoning, no chain-of-thought is stored.

    record_decision(claim=..., supported_by=[E17, E21], scope="ARG-17")  -> D7
    decision_status(D7)
        -> STALE: supporting evidence changed (h1 -> h2), or CURRENT/RETIRED

A decision is a PROPOSAL with provenance, never authority: Nexus never agrees or
disagrees, it only reports whether the evidence that supported it has moved.
Deterministic, stored in the capability scratch store (deletable, not
authoritative state). Delete this module and every Mnemosyne semantic is intact.
"""
from __future__ import annotations

import hashlib
import os

from execution import workspace_intel as WI
from capability.work_capsule import world_version


def _file_anchor(root, ref):
    p = os.path.join(root, ref)
    if not os.path.isfile(p):
        return {"ref": ref, "kind": "file", "hash": None, "path": ref}
    try:
        with open(p, "rb") as f:
            h = hashlib.sha256(f.read()).hexdigest()[:16]
        return {"ref": ref, "kind": "file", "hash": h, "path": ref}
    except OSError:
        return {"ref": ref, "kind": "file", "hash": None, "path": ref}


def _anchor(root, ref):
    """Resolve an evidence ref to a freshness anchor. A ref that looks like a
    path (has a slash, or ends in .py) is treated as a file; otherwise it is a
    symbol looked up in the index."""
    ref = (ref or "").strip()
    if not ref:
        return {"ref": ref, "kind": "none", "hash": None, "path": None}
    if "/" in ref or "\\" in ref or ref.endswith(".py"):
        return _file_anchor(root, ref)
    src = WI.get_symbol_source(root, ref)
    if src is None:
        return {"ref": ref, "kind": "unknown", "hash": None, "path": None}
    return {"ref": ref, "kind": "symbol", "hash": src["symbol_hash"], "path": src["path"]}


def _next_id(decs: dict) -> str:
    return f"D-{len(decs) + 1}"


def record_decision(root, store, claim, supported_by=None, scope="",
                    decision_id=None) -> dict:
    """Author a decision: claim + provenance (evidence anchors), no reasoning."""
    claim = (claim or "").strip()
    if not claim:
        return {"ok": False, "error": "record_decision requires a claim"}
    decs = store.load("decisions", {})
    did = decision_id or _next_id(decs)
    d = {"id": did, "claim": claim, "scope": scope,
         "supported_by": [_anchor(root, r) for r in (supported_by or [])],
         "status": "CURRENT", "world": world_version(root)["hash"]}
    decs[did] = d
    store.save("decisions", decs)
    return {"ok": True, "decision": d}


def decision_status(root, store, decision_id) -> dict:
    """Report the mechanical validity of a decision: does its evidence still hold?"""
    d = store.get("decisions", decision_id)
    if d is None:
        return {"id": decision_id, "found": False}
    changed, still_valid = [], []
    for a in d.get("supported_by", []):
        cur = _anchor(root, a["ref"])
        if a.get("hash") is None or cur["hash"] is None or a["hash"] != cur["hash"]:
            changed.append({"ref": a["ref"], "from": a.get("hash"), "to": cur["hash"]})
        else:
            still_valid.append(a["ref"])
    stale = bool(changed)
    status = ("RETIRED" if d.get("status") == "RETIRED"
              else ("STALE" if stale else "CURRENT"))
    return {"id": decision_id, "found": True, "claim": d.get("claim"),
            "scope": d.get("scope"), "status": status,
            "supporting_evidence_changed": changed, "still_valid": still_valid,
            "requires": ["RECONSIDER", "REAFFIRM", "RETIRE"] if stale else []}


def reaffirm_decision(root, store, decision_id) -> dict:
    """Re-anchor a stale decision's evidence to the current world -> CURRENT."""
    decs = store.load("decisions", {})
    d = decs.get(decision_id)
    if d is None:
        return {"ok": False, "error": f"decision {decision_id!r} not found"}
    d["supported_by"] = [_anchor(root, a["ref"]) for a in d.get("supported_by", [])]
    d["status"] = "CURRENT"
    d["world"] = world_version(root)["hash"]
    decs[decision_id] = d
    store.save("decisions", decs)
    return {"ok": True, "decision": d}


def retire_decision(store, decision_id) -> dict:
    """Retire a decision without deleting its record (history is never rewritten)."""
    decs = store.load("decisions", {})
    d = decs.get(decision_id)
    if d is None:
        return {"ok": False, "error": f"decision {decision_id!r} not found"}
    d["status"] = "RETIRED"
    decs[decision_id] = d
    store.save("decisions", decs)
    return {"ok": True, "decision": d}
