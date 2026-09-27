"""Little toys — EXPERIMENTAL (speculative, recoverable) orientation tools.

Small, deterministic, read-only affordances over the workspace-intelligence
primitives. Each answers one cheap question the model otherwise spends context
to answer itself. They PROPOSE orientation; they never decide authority,
freshness, or verification truth, and they never mutate a file. Delete this
module and every Mnemosyne semantic is intact.

Honest degradation: a toy whose source isn't built yet (a version ledger, the
Decision Registry) says so explicitly instead of fabricating an answer.
"""
from __future__ import annotations

import ast
import builtins as _builtins
import os
import subprocess

from execution import workspace_intel as WI
from capability.work_capsule import manifest, world_version, work_capsule

_BUILTINS = set(dir(_builtins))


# --- shared helpers ----------------------------------------------------------
def _parse(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        return src, ast.parse(src)
    except (OSError, SyntaxError):
        return None, None


def _resolve(root, rel):
    p = os.path.join(root, rel)
    return p if os.path.isfile(p) else None


def _git(root, *args):
    try:
        r = subprocess.run(["git", "-C", root, *args], capture_output=True,
                           text=True, timeout=10.0)
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


# --- 1. peek_symbol ----------------------------------------------------------
def peek_symbol(root, name):
    src = WI.get_symbol_source(root, name)
    if src is None:
        return {"name": name, "found": False}
    out = {"name": name, "found": True, "path": src["path"],
           "span": f"{src['lineno']}-{src['end_lineno']}", "kind": src["kind"],
           "doc": "", "methods": None, "hash": src["symbol_hash"]}
    try:
        tree = ast.parse(src["source"])
    except SyntaxError:
        tree = None
    if tree is not None:
        node = tree.body[0] if tree.body else None
        if node is not None:
            out["doc"] = (ast.get_docstring(node) or "")[:200]
            if isinstance(node, (ast.ClassDef,)):
                out["methods"] = [m.name for m in node.body
                                  if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
    return out


# --- 2. file_outline ---------------------------------------------------------
def file_outline(root, path):
    full = _resolve(root, path)
    if full is None:
        return {"path": path, "found": False}
    src, tree = _parse(full)
    if tree is None:
        return {"path": path, "found": True, "error": "unreadable or not Python"}
    imports, classes, funcs, consts = [], [], [], []
    for node in tree.body:
        if isinstance(node, ast.Import):
            imports += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            imports.append((node.module or "") + "." + ", ".join(a.name for a in node.names))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs.append(node.name)
        elif isinstance(node, ast.ClassDef):
            classes.append({"name": node.name,
                            "methods": [m.name for m in node.body
                                        if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]})
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            for t in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                if isinstance(t, ast.Name):
                    consts.append(t.id)
    return {"path": path, "found": True, "imports": imports, "classes": classes,
            "functions": funcs, "constants": consts}


# --- 3. symbol_history -------------------------------------------------------
def symbol_history(root, name):
    src = WI.get_symbol_source(root, name)
    if src is None:
        return {"name": name, "found": False}
    log = _git(root, "log", "--oneline", "-n", "20", "--", src["path"])
    history = [l.strip() for l in log.splitlines() if l.strip()] if log else []
    return {"name": name, "found": True, "path": src["path"],
            "current_hash": src["symbol_hash"], "history": history[:20],
            "note": ("" if history
                     else "no version ledger: workspace not under git, or git unavailable "
                          "(a durable world-version ledger is a later toy)")}


# --- 4. why_stale ------------------------------------------------------------
def why_stale(root, target, prev_manifest=None):
    # A symbol target: report mechanical staleness vs a prior manifest.
    if prev_manifest:
        changed = WI.changed_since(root, prev_manifest)
        src = WI.get_symbol_source(root, target)
        stale = src is not None and src["path"] in changed.get("modified", [])
        return {"target": target, "stale": stale,
                "because": (f"{target} modified since the acknowledged world version"
                            if stale else "no change since the acknowledged world version"),
                "changed_files": changed.get("modified", [])[:20]}
    # A decision id (D104) — no Decision Registry yet.
    return {"target": target, "stale": None,
            "because": "Decision Registry (toy #5) not built: no durable decision/"
                       "evidence records to invalidate yet"}


# --- 5. what_uses_this -------------------------------------------------------
def what_uses_this(root, target):
    refs = WI.find_references(root, target)
    nb = WI.dependency_neighborhood(root, target) or {}
    tests = WI.related_tests(root, symbol=target)
    files = sorted({r["path"] for r in refs})
    imported_by = set()
    exports = 0
    for rel, p in WI.py_files(root):
        src, tree = _parse(p)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for a in node.names:
                    if a.name == target or (a.asname == target):
                        imported_by.add(rel)
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id == "__all__":
                        if any(isinstance(e, ast.Constant) and e.value == target
                               for e in node.value.elts if isinstance(node.value, ast.List)):
                            exports += 1
    config_refs = sum(1 for f in files if any(k in f for k in ("config", "settings")))
    return {"target": target, "found": WI.get_symbol_source(root, target) is not None,
            "imported_by": len(imported_by), "called_by": len(nb.get("called_by", [])),
            "tests": len(tests), "public_exports": exports, "config_refs": config_refs,
            "files": files[:20]}


# --- 6. blast_radius ---------------------------------------------------------
def blast_radius(root, target):
    src = WI.get_symbol_source(root, target)
    idx = WI.build_symbol_index(root)
    # reference map built ONCE per symbol (memoized); the old code re-scanned the
    # whole project per direct file, making this the most expensive toy.
    ref_map = {}

    def _refs(sym):
        if sym not in ref_map:
            ref_map[sym] = {r["path"] for r in WI.find_references(root, sym)}
        return ref_map[sym]

    direct = sorted({p for p in _refs(target)
                     if src is None or p != src["path"]})
    # second-order: symbols DEFINED in the directly-affected files, then the files
    # that reference those — one hop past the change, de-duped against direct/src.
    # (Honest name: this is NOT a full transitive closure.)
    second_order = set()
    for name, locs in idx.items():
        if any(l["path"] in direct for l in locs):
            second_order |= {p for p in _refs(name)
                             if p not in direct and p != (src or {}).get("path")}
    tests = WI.related_tests(root, symbol=target)
    mods = {f.split("/")[0] for f in direct}
    if src and src["path"].endswith("__init__.py"):
        shape = "PUBLIC_API"
    elif len(mods) >= 3:
        shape = "PUBLIC_API"
    elif len(mods) == 2:
        shape = "CROSS_MODULE"
    else:
        shape = "LOCAL"
    return {"target": target, "direct_dependents": len(direct),
            "second_order_dependents": len(second_order), "tests": len(tests),
            "persisted_artifacts": 0, "design_claims": 0,
            "risk_shape": shape, "direct_files": direct[:20]}


# --- 7. imports_for ----------------------------------------------------------
def imports_for(root, symbol):
    src = WI.get_symbol_source(root, symbol)
    if src is None:
        return {"symbol": symbol, "found": False}
    parts = src["path"][:-3].split("/")
    mod = ".".join(parts)
    # nearest package root: the highest directory in the chain that is a package
    # (has __init__.py). A top-level module is importable only from the repo root.
    pkg_root = "ROOT"
    for i in range(len(parts) - 1):
        if os.path.isfile(os.path.join(root, *parts[:i + 1], "__init__.py")):
            pkg_root = parts[i]
        else:
            break
    defs = WI.build_symbol_index(root).get(symbol, [])
    return {"symbol": symbol, "found": True, "defining_module": mod,
            "canonical": f"from {mod} import {symbol}",
            "as_imported_from": pkg_root, "is_package": pkg_root != "ROOT",
            "definitions": len(defs), "collision": len(defs) > 1,
            "also_defined_in": [l["path"] for l in defs if l["path"] != src["path"]]}


# --- 8. import_health --------------------------------------------------------
def import_health(root, path):
    full = _resolve(root, path)
    if full is None:
        return {"path": path, "found": False}
    src, tree = _parse(full)
    if tree is None:
        return {"path": path, "found": True, "error": "unreadable or not Python"}
    imported, bound = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.asname or a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported |= {a.asname or a.name for a in node.names}
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bound.add(node.id)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            bound |= set(node.names)
    used = {n.id for n in ast.walk(tree)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    unused = sorted(imported - used)
    # CANDIDATE, not a guarantee: a static pass cannot be certain a name is
    # missing (dynamic imports, getattr, __all__ re-exports, etc.).
    possibly_missing = sorted(n for n in used
                              if n not in imported and n not in bound
                              and n not in _BUILTINS and not n.startswith("__"))
    return {"path": path, "possibly_missing": possibly_missing,
            "unused": unused, "circular": []}


# --- 9. signature ------------------------------------------------------------
def _sig_of(node):
    """Render one function's signature. Deterministic, no body.

    Reproduces Python's positional-only `/` and keyword-only `*` markers so the
    emitted string is pastable back (A7)."""
    args = node.args
    posonly = [a.arg for a in args.posonlyargs]
    positional = [a.arg for a in args.args]
    kwonly = [a.arg for a in args.kwonlyargs]
    all_pos = posonly + positional
    ndef = len(args.defaults)
    rendered = []
    for i, p in enumerate(all_pos):
        di = i - (len(all_pos) - ndef)
        rendered.append(p if di < 0 else p + "=" + ast.unparse(args.defaults[di]))
        if posonly and i == len(posonly) - 1:
            rendered.append("/")  # the positional-only marker sits after posonly
    if args.vararg:
        rendered.append("*" + args.vararg.arg)
    elif kwonly:
        rendered.append("*")
    for i, k in enumerate(kwonly):
        if args.kw_defaults and args.kw_defaults[i] is not None:
            rendered.append(k + "=" + ast.unparse(args.kw_defaults[i]))
        else:
            rendered.append(k)
    if args.kwarg:
        rendered.append("**" + args.kwarg.arg)
    sig = node.name + "(" + ", ".join(rendered) + ")"
    if node.returns:
        sig += f" -> {ast.unparse(node.returns)}"
    return {"name": node.name, "signature": sig,
            "parameters": posonly + positional + kwonly}


def signature(root, name):
    """A function's signature, or a class's METHOD signatures.

    For a class this reports `kind: "class"` plus every method (with the
    constructor keyed explicitly as `init`) \u2014 it never presents `__init__`
    as if it were the class's own signature."""
    src = WI.get_symbol_source(root, name)
    if src is None:
        return {"name": name, "found": False}
    try:
        tree = ast.parse(src["source"])
    except SyntaxError:
        tree = None
    node = tree.body[0] if tree and tree.body else None
    if node is None:
        return {"name": name, "found": True, "signature": name + "()"}
    if isinstance(node, ast.ClassDef):
        methods = [m for m in node.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
        init = next((m for m in methods if m.name == "__init__"), None)
        out = {"name": name, "found": True, "kind": "class",
               "init": (_sig_of(init)["signature"] if init else None),
               "methods": [_sig_of(m) for m in methods if m.name != "__init__"]}
        out["signature"] = f"class {name}" + (f"  (init: {out['init']})" if out["init"] else "")
        return out
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        out = _sig_of(node)
        return {"name": name, "found": True, "kind": src["kind"], **out}
    return {"name": name, "found": True, "kind": src["kind"],
            "signature": f"{src['kind']} {name}"}


# --- 10. call_examples -------------------------------------------------------
def call_examples(root, name, n=3):
    out = []
    for rel, p in WI.py_files(root):
        src, tree = _parse(p)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                f = node.func
                hit = (isinstance(f, ast.Name) and f.id == name) or \
                      (isinstance(f, ast.Attribute) and f.attr == name)
                if hit:
                    try:
                        snippet = ast.get_source_segment(src, node)
                    except Exception:
                        snippet = None
                    out.append({"path": rel, "line": node.lineno,
                                "call": (snippet or "").strip()[:120]})
    return {"name": name, "examples": out[:n], "total": len(out)}


# --- 11. test_for ------------------------------------------------------------
def test_for(root, target):
    files = WI.related_tests(root, symbol=target)
    direct, indirect = [], []
    for rel, p in WI.py_files(root):
        if "tests" not in rel.split("/"):
            continue
        src, tree = _parse(p)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                # direct = references the target as a NAME (the from-import case);
                # attribute access is too broad a proxy for a bare-name target (A6)
                hits = any(isinstance(n, ast.Name) and n.id == target
                           for n in ast.walk(node))
                if hits:
                    direct.append(node.name)
                elif rel in files:
                    indirect.append(node.name)
    return {"target": target, "files": files,
            "direct": sorted(set(direct)), "indirect": sorted(set(indirect)),
            "note": "direct is a textual match, not executed coverage"}


# --- 12. failure_focus -------------------------------------------------------
def failure_focus(root, events):
    events = list(events or [])
    by_call = {}
    last = None
    for e in events:
        p = e.payload or {}
        if e.event_type == "tool.requested":
            by_call[p.get("call_id")] = p
        elif e.event_type == "tool.completed":
            req = by_call.get(p.get("call_id"), {})
            if (req.get("tool") == "run_verification"
                    or (req.get("tool") == "run_command"
                        and "pytest" in str((req.get("arguments") or {}).get("command", "")))):
                last = {"tool": req.get("tool"), "success": bool(p.get("success")),
                        "error": p.get("error", "")}
    if last is None:
        return {"failures": None, "note": "no verification run on record"}
    if last["success"]:
        return {"failures": 0, "note": "last verification passed"}
    # honest: the structured failure list isn't durable; point at the last mutations
    muts = []
    for e in reversed(events):
        p = e.payload or {}
        if e.event_type == "tool.requested" and p.get("tool") in ("edit_file", "write_file", "patch_symbol"):
            muts.append((p.get("arguments") or {}).get("path"))
            if len(muts) >= 3:
                break
    return {"failures": ">=1", "reported": last["error"],
            "suggested_evidence": [m for m in muts if m],
            "note": "re-run run_verification for exact failed test locations"}


# --- 13. show_contract -------------------------------------------------------
_CONTRACTS = {
    "patch_symbol": {"requires": ["current target hash"], "causes": ["mutation", "world_version increment", "verification debt"],
                     "may_fail": ["STALE_TARGET", "SYMBOL_NOT_FOUND", "INVALID_PATCH"], "does_not": ["verify correctness"]},
    "edit_file": {"requires": ["unique old_string"], "causes": ["mutation", "verification debt"],
                  "may_fail": ["old_string not found", "old_string not unique"], "does_not": ["verify correctness"]},
    "write_file": {"requires": ["full content"], "causes": ["mutation", "verification debt"],
                   "may_fail": [], "does_not": ["verify correctness"]},
    "read_file": {"requires": ["relative path"], "causes": [], "may_fail": ["path outside project", "not a file"],
                  "does_not": ["mutate", "decide"]},
    "run_verification": {"requires": ["scope or symbol"], "causes": ["structured pass/fail"],
                         "may_fail": ["tests fail"], "does_not": ["decide what to fix"]},
    "run_command": {"requires": ["one executable + argv"], "causes": ["a subprocess"],
                    "may_fail": ["nonzero exit", "timeout"], "does_not": ["interpret shell operators"]},
    "run_elevated": {"requires": ["operator approval"], "causes": ["OS-elevated subprocess (UAC)"],
                     "may_fail": ["elevation denied"], "does_not": ["capture full elevated output"]},
}


def show_contract(root, tool, specs=None):
    specs = specs or {}
    spec = specs.get(tool)
    risk = getattr(spec, "risk", None)
    contract = dict(_CONTRACTS.get(tool, {}))
    contract.setdefault("requires", []); contract.setdefault("causes", [])
    contract.setdefault("may_fail", []); contract.setdefault("does_not", [])
    return {"tool": tool,
            "risk": getattr(risk, "value", None),
            "description": getattr(spec, "description", "") if spec else "",
            **contract}


# --- 14. (removed: explain_rejection now lives in capability/coherence.py,
#      rendered from the single GateView, so it cannot drift from the governor) ---


# --- 15. (removed: next_mechanical_options now lives in capability/coherence.py) ---
def _phase(caps):
    if caps["verification_debt"]:
        return "REPAIR"
    if caps["blocked_on"]:
        return "BLOCKED"
    if caps["frontier"]["status"] == "open":
        return "ACTIVE_PROGRESS"
    return "IDLE"


def next_mechanical_options(root, events, capabilities):
    """(delegated to capability/coherence.py — single GateView source)"""
    from capability import coherence as _coh
    from capability.gate_view import GateView as _GV
    return _coh.next_mechanical_options(root, events, capabilities, _GV([]))


# --- 16. where_am_i ----------------------------------------------------------
def where_am_i(root, events, capabilities):
    caps = work_capsule(root, events, capabilities)
    return {"goal": caps["goal"], "phase": _phase(caps),
            "frontier": caps["frontier"], "world": caps["world_version"]["hash"],
            "dirty": bool(caps["changes"] or caps["verification_debt"]),
            "verification_debt": [d["path"] for d in caps["verification_debt"]]}


# --- 17. what_changed --------------------------------------------------------
def what_changed(root, prev_manifest):
    if prev_manifest is None:
        return {"note": "no acknowledged world version yet — here is your baseline",
                "world_version": world_version(root), "changed": []}
    return {"changed": WI.changed_since(root, prev_manifest),
            "world_version": world_version(root)}


# --- 18. why_is_this_here ----------------------------------------------------
def why_is_this_here(root, obj_id):
    src = WI.get_symbol_source(root, obj_id) if obj_id else None
    if src is not None:
        return {"id": obj_id, "reason": "DIRECT_TARGET", "tier": "FULL",
                "freshness": f"CURRENT @ {src['symbol_hash']}",
                "supports": "direct target of the current goal/decision"}
    return {"id": obj_id, "reason": None, "tier": None, "freshness": None,
            "note": "no evidence-admission ledger yet — only symbols/paths resolve; "
                    "per-object admission records are a later toy"}
