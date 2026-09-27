"""Work Graph — EXPERIMENTAL (speculative, recoverable).

Toy #2. Makes unfinished work STRUCTURALLY VISIBLE instead of living in a
transcript. A deterministic projection over the capability scratch store
(decisions + changesets) composed with the durable event log's verification debt
and blocked approvals.

    unfinished_work()
        -> open_verification, open_changesets, blocked_mutations, stale_decisions,
           nearest_closable_frontier

The model advances the graph; the framework REPRESENTS it. No authority, no
side effect. Delete this module and every Mnemosyne semantic is intact.
"""
from __future__ import annotations

from capability.work_capsule import work_capsule


def work_graph(store, root, events, capabilities) -> dict:
    decs = store.load("decisions", {})
    css = store.load("changesets", {})
    caps = work_capsule(root, events, capabilities)
    nodes = []
    for did, d in decs.items():
        nodes.append({"id": did, "kind": "decision", "status": d.get("status"),
                      "claim": d.get("claim")})
    for cid, c in css.items():
        nodes.append({"id": cid, "kind": "changeset", "status": c.get("status"),
                      "objective": c.get("objective")})
    return {"nodes": nodes, "world": caps["world_version"]["hash"],
            "frontier": caps["frontier"], "verification_debt": caps["verification_debt"],
            "blocked_on": caps["blocked_on"]}


def unfinished_work(store, root, events, capabilities) -> dict:
    g = work_graph(store, root, events, capabilities)
    stale = [n for n in g["nodes"] if n["kind"] == "decision" and n["status"] == "STALE"]
    applied_unverified = [n for n in g["nodes"]
                          if n["kind"] == "changeset" and n["status"] == "APPLIED"]
    proposed = [n for n in g["nodes"]
                if n["kind"] == "changeset" and n["status"] == "PROPOSED"]
    open_verification = len(applied_unverified) + len(g["verification_debt"])
    frontier = None
    if applied_unverified:
        frontier = {"kind": "verify", "id": applied_unverified[0]["id"]}
    elif proposed:
        frontier = {"kind": "apply", "id": proposed[0]["id"]}
    elif g["verification_debt"]:
        frontier = {"kind": "verify", "target": g["verification_debt"][0]["path"]}
    return {
        "open_verification": open_verification,
        "open_changesets": len(proposed),
        "blocked_mutations": len(g["blocked_on"]),
        "stale_decisions": len(stale),
        "nearest_closable_frontier": frontier,
    }
