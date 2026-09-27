"""Workspace Intelligence — deterministic, stdlib-only facts about a project.

The read-side "knowledge plane": symbols, references, tests, neighborhoods, diffs,
and structured verification. Everything here is derived from the authoritative
filesystem via `ast`/`os`/`hashlib` — zero model cost, zero side effects. It narrows
the SEARCH space; it never decides what the code means or what should change.
"""
from __future__ import annotations

import ast
import hashlib
import os
import re
import subprocess
import time

IGNORE_DIRS = {".git", "__pycache__", ".pytest_cache", "node_modules", ".venv", "venv", "dist", "build"}


def py_files(root):
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]
        for fn in filenames:
            if fn.endswith(".py"):
                p = os.path.join(dirpath, fn)
                out.append((os.path.relpath(p, root).replace("\\", "/"), p))
    return sorted(out)


def _parse(p):
    try:
        with open(p, "r", encoding="utf-8") as f:
            src = f.read()
        return src, ast.parse(src)
    except (OSError, SyntaxError):
        return None, None


def workspace_map(root):
    dirs = {}
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORE_DIRS)
        rel = os.path.relpath(dirpath, root).replace("\\", "/")
        dirs[rel] = dirnames
        for fn in sorted(filenames):
            files.append((rel, fn))
    return dirs, files


def build_symbol_index(root):
    index = {}
    for rel, p in py_files(root):
        src, tree = _parse(p)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                index.setdefault(node.name, []).append({
                    "path": rel, "lineno": node.lineno,
                    "end_lineno": getattr(node, "end_lineno", None) or node.lineno,
                    "kind": "class" if isinstance(node, ast.ClassDef) else "def",
                })
    return index


def find_symbol(index, query):
    q = (query or "").lower()
    out = []
    for name, locs in index.items():
        if q in name.lower():
            for loc in locs:
                out.append({"name": name, **loc})
    return out


def find_references(root, symbol):
    refs = []
    for rel, p in py_files(root):
        src, tree = _parse(p)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == symbol:
                refs.append({"path": rel, "lineno": node.lineno})
            elif isinstance(node, ast.Attribute) and node.attr == symbol:
                refs.append({"path": rel, "lineno": node.lineno})
    return refs


def related_tests(root, symbol=None, module=None):
    tests = []
    for rel, p in py_files(root):
        if "tests" not in rel.replace("\\", "/").split("/"):
            continue
        src, tree = _parse(p)
        if tree is None:
            continue
        hit = False
        if symbol:
            for node in ast.walk(tree):
                if (isinstance(node, ast.Name) and node.id == symbol) or \
                   (isinstance(node, ast.Attribute) and node.attr == symbol):
                    hit = True
        if module and (module.replace("/", ".") in src or module.split(".")[-1] in src):
            hit = True
        if hit:
            tests.append(rel)
    return tests


def get_symbol_source(root, symbol):
    idx = build_symbol_index(root)
    mod, name = (symbol.rsplit(".", 1) if "." in symbol else (None, symbol))
    candidates = idx.get(name, [])
    loc = None
    for c in candidates:
        if mod is None or c["path"].replace("/", ".").startswith(mod) or c["path"].endswith(mod + ".py"):
            loc = c
            break
    if loc is None and candidates:
        loc = candidates[0]
    if loc is None:
        return None
    full = os.path.join(root, loc["path"].replace("/", os.sep))
    src, tree = _parse(full)
    if tree is None:
        return None
    lines = src.splitlines()
    source = "\n".join(lines[loc["lineno"] - 1:loc["end_lineno"]])
    return {"path": loc["path"], "lineno": loc["lineno"], "end_lineno": loc["end_lineno"],
            "kind": loc["kind"], "source": source,
            "file_hash": hashlib.sha256(src.encode("utf-8")).hexdigest()[:16],
            "symbol_hash": hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]}


def dependency_neighborhood(root, symbol):
    src = get_symbol_source(root, symbol)
    if src is None:
        return None
    refs = find_references(root, symbol)
    called_by = [r for r in refs if not (r["path"] == src["path"] and r["lineno"] == src["lineno"])]
    idx = build_symbol_index(root)
    calls = []
    try:
        tree = ast.parse(src["source"])
    except SyntaxError:
        tree = None
    if tree is not None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in idx and node.id != symbol:
                calls.append(node.id)
            elif isinstance(node, ast.Attribute) and node.attr in idx:
                calls.append(node.attr)
    return {"defined": f"{src['path']}:{src['lineno']}", "called_by": called_by[:40],
            "calls": sorted(set(calls))[:40], "tests": related_tests(root, symbol=symbol)}


def verification_candidates(root, path_or_symbol):
    if "." in path_or_symbol or "/" in path_or_symbol:
        mod = path_or_symbol.replace(".py", "").replace("/", ".")
    else:
        locs = build_symbol_index(root).get(path_or_symbol, [])
        mod = locs[0]["path"][:-3].replace("/", ".") if locs else path_or_symbol
    direct = related_tests(root, module=mod)
    return {"direct_tests": sorted(set(direct)),
            "full": "py -m pytest -q tests"}


def patch_symbol(root, path, symbol, expected_hash, replacement):
    src = get_symbol_source(root, symbol)
    if src is None:
        return False, f"symbol {symbol!r} not found"
    if src["symbol_hash"] != expected_hash:
        return False, f"stale: expected {expected_hash}, current {src['symbol_hash']}"
    full = os.path.join(root, src["path"].replace("/", os.sep))
    _, tree = _parse(full)
    if tree is None:
        return False, "parse failed"
    target = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == symbol:
            target = node
            break
    if target is None:
        return False, f"symbol {symbol!r} not found in {src['path']}"
    lines = open(full, "r", encoding="utf-8").read().splitlines(keepends=True)
    start = target.lineno - 1
    end = getattr(target, "end_lineno", None) or target.lineno
    indent = lines[start][:len(lines[start]) - len(lines[start].lstrip())]
    new_lines = indent + replacement.rstrip("\n").replace("\n", "\n" + indent) + "\n"
    new_content = "".join(lines[:start]) + new_lines + "".join(lines[end:])
    with open(full, "w", encoding="utf-8") as f:
        f.write(new_content)
    return True, f"replaced {symbol} at {src['path']}:{target.lineno}-{end}"


def changed_since(root, prev_manifest):
    cur = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]
        for fn in filenames:
            p = os.path.join(dirpath, fn)
            rel = os.path.relpath(p, root).replace("\\", "/")
            try:
                with open(p, "rb") as f:
                    cur[rel] = hashlib.sha256(f.read()).hexdigest()[:16]
            except OSError:
                continue
    added = sorted(p for p in cur if p not in prev_manifest)
    deleted = sorted(p for p in prev_manifest if p not in cur)
    modified = sorted(p for p in cur if p in prev_manifest and cur[p] != prev_manifest[p])
    return {"added": added, "modified": modified, "deleted": deleted}


def run_pytest(root, targets, timeout=300):
    """Structured verification: run pytest and reduce the output to a shape."""
    t0 = time.time()
    cmd = ["py", "-m", "pytest", "-q", "--tb=no"] + list(targets)
    try:
        r = subprocess.run(cmd, cwd=root, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"passed": 0, "failed": 0, "counts": {}, "failed_locations": [],
                "duration": round(time.time() - t0, 2), "summary": "TIMEOUT", "returncode": -1}
    out = r.stdout or ""
    lines = [l for l in out.strip().splitlines() if l.strip()]
    summary = lines[-1] if lines else ""
    counts = {}
    for m in re.finditer(r"(\d+)\s+(passed|failed|error|errors|skipped|deselected)", summary):
        counts[m.group(2)] = counts.get(m.group(2), 0) + int(m.group(1))
    passed = counts.get("passed", 0)
    failed = counts.get("failed", 0) + counts.get("error", 0) + counts.get("errors", 0)
    return {"passed": passed, "failed": failed, "counts": counts,
            "failed_locations": re.findall(r"^FAILED\s+(\S+)", out, re.M)[:40],
            "duration": round(time.time() - t0, 2),
            "bounded_output": (out + (r.stderr or ""))[-4000:],
            "summary": summary, "returncode": r.returncode, "targets": list(targets)}
