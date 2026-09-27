"""Work Capsule — EXPERIMENTAL (speculative, recoverable).

Toy #1 of the toy box. A portable snapshot of the JOB's computational state,
assembled deterministically from the durable event log + workspace + capability
registry:

    "Here are 47 previous messages — figure out what happened."
                 becomes
    "Here is the computational state of the job."

This is a PURE PROJECTION. It reads authoritative state and proposes a
presentation; it never writes, never re-authorizes, never declares verification
truth (it only reports what the events already say), and it never invents a
field it has no source for — those return None/[] with an explicit note. Delete
this module and every Mnemosyne semantic is intact.

Fields and their deterministic sources (v1):

    goal                   -> latest run.manifest.task_title
    acceptance_contract    -> no source yet (None)
    frontier               -> most recent open task: current operation + status
    completed              -> task_ids with a run.completed terminal
    failed_attempts        -> run.failed / tool.completed(failed) / action.failed
    declared_decisions     -> no source yet ([]; Decision Registry is toy #5)
    changes                -> successful mutations (edit_file / write_file / patch_symbol)
    verification_debt      -> mutations not yet covered by a passing verification
    blocked_on             -> pending approvals (approval.required with no terminal)
    hot_evidence           -> recently read / mutated paths
    evidence_refs          -> no source yet ([])
    world_version          -> deterministic hash of the workspace manifest
    available_capabilities -> the tool registry (injected)
    ineligible_mechanisms  -> no source yet ([]; Capability Graph is toy #9)
"""
from __future__ import annotations

import hashlib
import os

from execution import workspace_intel as WI

MUTATION_TOOLS = frozenset({"edit_file", "write_file", "patch_symbol"})
READ_TOOLS = frozenset({"read_file"})

_MAX_LISTS = 20
_HOT_MAX = 8


def manifest(root: str) -> dict:
    """{relpath: sha256[:16]} for every file under root (ignore-dirs respected)."""
    out = {}
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in WI.IGNORE_DIRS]
            for fn in filenames:
                p = os.path.join(dirpath, fn)
                rel = os.path.relpath(p, root).replace("\\", "/")
                try:
                    with open(p, "rb") as f:
                        out[rel] = hashlib.sha256(f.read()).hexdigest()[:16]
                except OSError:
                    continue
    except OSError:
        pass
    return out


def world_version(root: str) -> dict:
    """A deterministic content fingerprint of the current world — a handle, not a
    commit. Rehashed on every call; the model never has to remember one."""
    m = manifest(root)
    h = hashlib.sha256()
    for rel in sorted(m):
        h.update(rel.encode("utf-8"))
        h.update(m[rel].encode("utf-8"))
    return {"hash": h.hexdigest()[:16], "files": len(m)}


def _task_first_seen(events) -> list:
    seen = set()
    order = []
    for e in events:
        if e.task_id and e.task_id not in seen:
            seen.add(e.task_id)
            order.append(e.task_id)
    return order


def _terminal_tasks(events) -> set:
    terminal = {"run.completed", "run.failed", "run.stopped",
                "task.completed", "task.failed"}
    return {e.task_id for e in events if e.event_type in terminal and e.task_id}


def _goal(events, override: str) -> str:
    if override:
        return override
    for e in reversed(list(events)):
        if e.event_type == "run.manifest":
            title = (e.payload or {}).get("task_title", "")
            if title:
                return title
    return ""


def _completed(events) -> list:
    out = []
    seen = set()
    for e in events:
        if e.event_type == "run.completed" and e.task_id not in seen:
            seen.add(e.task_id)
            ans = (e.payload or {}).get("answer", "")
            out.append({"task_id": e.task_id,
                        "answer": (ans[:120] + "…") if len(ans) > 120 else ans})
    return out[-_MAX_LISTS:]


def _failed_attempts(events) -> list:
    out = []
    for e in events:
        p = e.payload or {}
        if e.event_type == "run.failed":
            out.append({"kind": "run.failed", "task_id": e.task_id,
                        "reason": p.get("reason", "")})
        elif e.event_type == "tool.completed" and not p.get("success"):
            out.append({"kind": "tool.failed", "tool": p.get("tool"),
                        "error": (p.get("error") or "")[:160]})
        elif e.event_type == "action.failed":
            out.append({"kind": "action.failed",
                        "action_id": p.get("action_id", "")})
    return out[-_MAX_LISTS:]


def _tool_pairs(events):
    """Ordered (tool, success, arguments) for every completed tool call, joined
    to its requested arguments via call_id."""
    by_call = {}
    ordered = []
    for e in events:
        p = e.payload or {}
        if e.event_type == "tool.requested":
            by_call[p.get("call_id")] = {"tool": p.get("tool"),
                                         "arguments": p.get("arguments") or {}}
        elif e.event_type == "tool.completed":
            req = by_call.get(p.get("call_id"), {})
            ordered.append((p.get("tool"), bool(p.get("success")),
                            req.get("arguments", {}), e.task_id))
    return ordered


def _changes(events) -> list:
    out = []
    seen = set()
    for tool, ok, args, _task in _tool_pairs(events):
        if ok and tool in MUTATION_TOOLS:
            path = str(args.get("path", ""))
            key = (tool, path)
            if path and key not in seen:
                seen.add(key)
                out.append({"tool": tool, "path": path})
    return out[-_MAX_LISTS:]


def _verification_debt(events) -> list:
    ordered = _tool_pairs(events)
    last_verified = -1
    for i, (tool, ok, args, _task) in enumerate(ordered):
        if not ok:
            continue
        if tool == "run_verification":
            last_verified = i
        elif tool == "run_command" and "pytest" in str(args.get("command", "")):
            last_verified = i
    debt, seen = [], set()
    for i, (tool, ok, args, _task) in enumerate(ordered):
        if i <= last_verified or not ok or tool not in MUTATION_TOOLS:
            continue
        path = str(args.get("path", ""))
        if path and path not in seen:
            seen.add(path)
            debt.append({"path": path, "tool": tool,
                         "note": "mutated after the last passing verification"})
    return debt


def _blocked_on(events) -> list:
    pending = {}
    for e in events:
        p = e.payload or {}
        if e.event_type == "approval.required":
            aid = p.get("approval_id")
            if aid:
                pending[aid] = {"approval_id": aid, "task_id": e.task_id,
                                "tool": p.get("tool", "")}
        elif e.event_type in ("approval.granted", "approval.denied", "approval.consumed"):
            pending.pop(p.get("approval_id"), None)
    return list(pending.values())[-_MAX_LISTS:]


def _hot_evidence(events, injected) -> list:
    seen = set()
    out = []
    for tool, ok, args, _task in reversed(_tool_pairs(events)):
        if not ok or tool not in READ_TOOLS | MUTATION_TOOLS:
            continue
        path = str(args.get("path", ""))
        if path and path not in seen:
            seen.add(path)
            out.append({"path": path, "tool": tool})
    for p in (injected or []):
        if p not in seen:
            seen.add(p)
            out.append({"path": p, "tool": "injected"})
    return out[:_HOT_MAX]


def _frontier(events):
    order = _task_first_seen(events)
    terminal = _terminal_tasks(events)
    open_tasks = [t for t in order if t not in terminal]
    # the current task = the most recently started task that is still open;
    # if everything is settled, the frontier is the most recent task overall.
    current = (open_tasks[-1] if open_tasks
               else (order[-1] if order else None))
    current_operation = None
    status = "idle"
    for e in reversed(list(events)):
        if e.task_id != current:
            continue
        if e.event_type == "run.completed":
            status = "settled"; break
        if e.event_type == "run.failed":
            status = "failed"; break
        if e.event_type == "run.stopped":
            status = "stopped"; break
        if e.event_type == "tool.requested" and current_operation is None:
            current_operation = (e.payload or {}).get("tool")
        if e.event_type == "step.started" and current_operation is None:
            current_operation = (e.payload or {}).get("name")
    if current is not None and status == "idle":
        status = "open"
    return {
        "task_id": current,
        "status": status,
        "current_operation": current_operation,
        "current_decision": None,        # Decision Registry is toy #5
        "next_expected_transition": None,  # Capability Graph is toy #9
    }


def work_capsule(root, events, capabilities, *, goal="", acceptance=None,
                 hot=None) -> dict:
    """Assemble the Work Capsule — the computational state of the job.

    Deterministic over (root, events, capabilities). No LLM, no side effect, no
    authority. A field with no source is None/[], never guessed."""
    events = list(events or [])
    return {
        "goal": _goal(events, goal),
        "acceptance_contract": acceptance,
        "frontier": _frontier(events),
        "completed": _completed(events),
        "failed_attempts": _failed_attempts(events),
        "declared_decisions": [],         # reserved; Decision Registry is toy #5
        "changes": _changes(events),
        "verification_debt": _verification_debt(events),
        "blocked_on": _blocked_on(events),
        "hot_evidence": _hot_evidence(events, hot),
        "evidence_refs": [],              # reserved; no source yet
        "world_version": world_version(root),
        "available_capabilities": sorted(set(capabilities or [])),
        "ineligible_mechanisms": [],      # reserved; Capability Graph is toy #9
    }
