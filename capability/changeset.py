"""ChangeSet — EXPERIMENTAL (speculative, recoverable).

Toy #3 (+ the record that #9 Semantic Undo will replay). One logical change as a
first-class object, instead of five unrelated tool calls.

    propose_changeset(objective, operations, verification)
        -> CS-1 (PROPOSED) with per-file freshness preconditions
    apply_changeset(CS-1)
        -> checks preconditions AT THE WRITE BOUNDARY, then applies each op
           through the bounded filesystem executor; records before/after hashes.
    verify_changeset(CS-1)
        -> runs affected (or full) verification; status VERIFIED | FAILED.

Deterministic, stored in the capability scratch store (deletable, never
authoritative). The authority gate still applies to each delegated mutation
through the executor; this module only bundles and sequences. Delete it and
every Mnemosyne semantic is intact.
"""
from __future__ import annotations

import hashlib
import os

from core.contracts import ToolCall
from capability.work_capsule import world_version
from execution import workspace_intel as WI

VALID_OPS = ("CREATE", "EDIT")


def _hash(root, rel):
    p = os.path.join(root, rel)
    if not os.path.isfile(p):
        return None
    try:
        with open(p, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()[:16]
    except OSError:
        return None


def _read(root, rel):
    try:
        with open(os.path.join(root, rel), "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def _next_id(css):
    return f"CS-{len(css) + 1}"


def _public(cs):
    out = dict(cs)
    out["operations"] = [{k: v for k, v in op.items() if not k.startswith("_")}
                         for op in cs.get("operations", [])]
    return out


def _validate(op):
    if op.get("op") not in VALID_OPS:
        return f"bad op {op.get('op')!r} (use {', '.join(VALID_OPS)})"
    if not op.get("path"):
        return "operation requires a path"
    if op["op"] == "CREATE" and "content" not in op:
        return "CREATE requires content"
    if op["op"] == "EDIT" and ("old_string" not in op or "new_string" not in op):
        return "EDIT requires old_string and new_string"
    return None


def propose_changeset(store, root, objective, operations, verification="affected") -> dict:
    objective = (objective or "").strip()
    if not objective:
        return {"ok": False, "error": "propose_changeset requires an objective"}
    ops = operations or []
    if not ops:
        return {"ok": False, "error": "at least one operation required"}
    for op in ops:
        err = _validate(op)
        if err:
            return {"ok": False, "error": err}
    css = store.load("changesets", {})
    cid = _next_id(css)
    normalized, preconditions = [], {}
    for op in ops:
        rel = op["path"]
        normalized.append({**op, "path": rel, "_before": _read(root, rel)})
        preconditions[rel] = _hash(root, rel)
    cs = {"id": cid, "objective": objective, "operations": normalized,
          "preconditions": preconditions, "verification": verification,
          "status": "PROPOSED", "world_before": world_version(root)["hash"],
          "mutations": []}
    css[cid] = cs
    store.save("changesets", css)
    return {"ok": True, "changeset": _public(cs)}


def apply_changeset(store, root, inner, changeset_id) -> dict:
    css = store.load("changesets", {})
    cs = css.get(changeset_id)
    if cs is None:
        return {"ok": False, "error": f"changeset {changeset_id!r} not found"}
    if cs["status"] != "PROPOSED":
        return {"ok": False, "error": f"changeset is {cs['status']}, not PROPOSED"}
    # freshness at the write boundary: every precondition must still hold
    for rel, expected in cs["preconditions"].items():
        cur = _hash(root, rel)
        if cur != expected:
            return {"ok": False,
                    "error": f"stale precondition on {rel}: expected {expected}, now {cur}"}
    mutations = []
    for op in cs["operations"]:
        if op["op"] == "CREATE":
            res = inner.execute_tool(ToolCall(tool_name="write_file",
                                              arguments={"path": op["path"], "content": op["content"]}))
        else:
            res = inner.execute_tool(ToolCall(tool_name="edit_file",
                                              arguments={"path": op["path"], "old_string": op["old_string"],
                                                         "new_string": op["new_string"]}))
        if not res.success:
            cs["mutations"] = mutations
            cs["status"] = "FAILED"
            cs["error"] = f"{op['op']} {op['path']} failed: {res.error}"
            css[changeset_id] = cs
            store.save("changesets", css)
            return {"ok": False, "changeset": _public(cs)}
        mutations.append({"op": op["op"], "path": op["path"],
                          "before": op.get("_before"), "after": _read(root, op["path"])})
    cs["mutations"] = mutations
    cs["status"] = "APPLIED"
    cs["world_after"] = world_version(root)["hash"]
    css[changeset_id] = cs
    store.save("changesets", css)
    return {"ok": True, "changeset": _public(cs)}


def verify_changeset(store, root, inner, changeset_id) -> dict:
    css = store.load("changesets", {})
    cs = css.get(changeset_id)
    if cs is None:
        return {"ok": False, "error": f"changeset {changeset_id!r} not found"}
    if cs["status"] not in ("APPLIED", "FAILED"):
        return {"ok": False, "error": f"cannot verify a {cs['status']} changeset"}
    symbol = None
    idx = WI.build_symbol_index(root)
    for m in cs.get("mutations", []):
        if m["path"].endswith(".py"):
            for name, locs in idx.items():
                if any(l["path"] == m["path"] for l in locs):
                    symbol = name
                    break
        if symbol:
            break
    args = {"scope": "related", "symbol": symbol} if symbol else {"scope": "full"}
    res = inner.execute_tool(ToolCall(tool_name="run_verification", arguments=args))
    cs["status"] = "VERIFIED" if res.success else "FAILED"
    cs["verification_result"] = {"passed": res.success, "output": res.output or {},
                                 "error": res.error}
    css[changeset_id] = cs
    store.save("changesets", css)
    return {"ok": True, "changeset": _public(cs)}


def changeset_status(store, changeset_id) -> dict:
    cs = store.get("changesets", changeset_id)
    if cs is None:
        return {"id": changeset_id, "found": False}
    return _public(cs)
