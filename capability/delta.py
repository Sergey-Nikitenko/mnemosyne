"""World Delta — EXPERIMENTAL (speculative, recoverable).

Toy #3: a richer `diff_since`. Where `changed_since` answers "which FILES moved",
`world_delta` answers "what moved, at which granularity" in one object:

    files              added / modified / deleted            (exact)
    symbols            changed / added / removed / unchanged (hash-precise)
    tests              test files that changed
    decisions_invalidated   (Decision Registry is toy #5 — honest [] today)
    world_version      the current content fingerprint

Pure, deterministic, read-only. It compares the CURRENT world against the
model's last acknowledged manifest (files + symbols), so the model doesn't have
to re-read the world to discover what changed. Delete this module and every
Mnemosyne semantic is intact.
"""
from __future__ import annotations

import hashlib

from execution import workspace_intel as WI
from capability.work_capsule import manifest, world_version


def _lines(root, rel):
    try:
        with open(root.rstrip("/\\") + "/" + rel, "r", encoding="utf-8") as f:
            return f.read().splitlines()
    except OSError:
        return None


def symbol_manifest(root) -> dict:
    """{f"{path}::{name}": symbol_hash[:16]} for EVERY indexed definition —
    keyed by path+name so a name defined in two files cannot silently collapse
    (the A8 collision defect)."""
    idx = WI.build_symbol_index(root)
    cache = {}
    out = {}
    for name, locs in idx.items():
        for loc in locs:
            if loc["path"] not in cache:
                cache[loc["path"]] = _lines(root, loc["path"])
            lines = cache[loc["path"]]
            if lines is None:
                continue
            source = "\n".join(lines[loc["lineno"] - 1:loc["end_lineno"]])
            out[f"{loc['path']}::{name}"] = hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]
    return out


def world_delta(root, since_files=None, since_symbols=None) -> dict:
    """The world's movement since the acknowledged version, at three granularities."""
    files = WI.changed_since(root, since_files) if since_files is not None \
        else {"added": [], "modified": [], "deleted": []}
    current_symbols = symbol_manifest(root)
    symbols = {"changed": [], "added": [], "removed": [], "unchanged": 0,
               "collisions": []}
    if since_symbols is not None:
        for key, h in current_symbols.items():
            name = key.rsplit("::", 1)[-1]
            if key not in since_symbols:
                symbols["added"].append(name)
            elif since_symbols[key] != h:
                symbols["changed"].append(name)
        for key in since_symbols:
            if key not in current_symbols:
                symbols["removed"].append(key.rsplit("::", 1)[-1])
        symbols["unchanged"] = (len(current_symbols) - len(symbols["added"])
                                - len(symbols["changed"]))
    counts = {}
    for key in current_symbols:
        n = key.rsplit("::", 1)[-1]
        counts[n] = counts.get(n, 0) + 1
    symbols["collisions"] = sorted(n for n, k in counts.items() if k > 1)
    changed_tests = [f for f in (files["added"] + files["modified"])
                     if "tests" in f.replace("\\", "/").split("/")]
    return {
        "files": files,
        "symbols": symbols,
        "tests": sorted(set(changed_tests)),
        "decisions_invalidated": [],
        "note_decisions": "Decision Registry (toy #5) not built — no decisions to invalidate yet",
        "world_version": world_version(root),
    }
