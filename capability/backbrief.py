"""Backbrief — EXPERIMENTAL (speculative, recoverable).

The "backbrief-as-organ" steal (context-leverage prior-art, 2026-08-11): after a
conclusion lands, ONE raw-access re-check of a mechanical claim against the
deterministic substrate. In the house record, this guard was performed BY HAND
during the Codex correction cycle; this module makes it an organ.

The model asserts a MECHANICAL fact; the house confirms or refutes it with a
receipt (path:line / count). It never judges SEMANTIC truth — that is the
model's job. A `FACT`/`REFUTED` verdict is measured, not claimed.

    verify_claim(subject="score_event", predicate="defined_in", target="pkg/score.py")
        -> FACT, receipt "pkg/score.py:12"

Predicates (deterministic, checkable): defined_in / calls / tested_by /
referenced_by / imports. Delete this module and every Mnemosyne semantic is
intact.
"""
from __future__ import annotations

import ast
import os

from execution import workspace_intel as WI

PREDICATES = ("defined_in", "calls", "tested_by", "referenced_by", "imports")


def _verdict(ok, predicate, subject, target, verdict, receipt, detail):
    return {"ok": ok, "predicate": predicate, "subject": subject, "target": target,
            "verdict": verdict, "receipt": receipt, "detail": detail,
            "epistemic": "MEASUREMENT" if verdict != "UNKNOWN" else "UNKNOWN"}


def verify_claim(root, subject, predicate, target=None) -> dict:
    subject = (subject or "").strip()
    predicate = (predicate or "").strip()
    if not subject or not predicate:
        return {"ok": False, "error": "verify_claim requires subject and predicate"}
    if predicate not in PREDICATES:
        return {"ok": False, "error": f"unknown predicate {predicate!r}; choose one of {PREDICATES}"}

    if predicate == "defined_in":
        src = WI.get_symbol_source(root, subject)
        if src is None:
            return _verdict(True, predicate, subject, target, "REFUTED", None,
                            "symbol not in the deterministic index")
        if target and target.rstrip("/\\") != src["path"]:
            return _verdict(True, predicate, subject, target, "REFUTED",
                            f"{src['path']}:{src['lineno']}",
                            f"actually defined at {src['path']}:{src['lineno']}")
        return _verdict(True, predicate, subject, target, "FACT",
                        f"{src['path']}:{src['lineno']}",
                        f"{src['kind']} defined at {src['path']}:{src['lineno']}")

    if predicate == "calls":
        nb = WI.dependency_neighborhood(root, subject)
        if nb is None:
            return _verdict(True, predicate, subject, target, "REFUTED", None,
                            "symbol not in the deterministic index")
        calls = nb.get("calls", [])
        if target and target not in calls:
            return _verdict(True, predicate, subject, target, "REFUTED", None,
                            f"{subject} calls {calls[:20] or 'nothing'}, not {target!r}")
        return _verdict(True, predicate, subject, target, "FACT", None,
                        f"{subject} calls {target!r}" if target else f"{subject} calls {calls[:20]}")

    if predicate == "tested_by":
        tests = WI.related_tests(root, symbol=subject)
        if not tests:
            return _verdict(True, predicate, subject, target, "REFUTED", None,
                            "no test file references this symbol")
        return _verdict(True, predicate, subject, target, "FACT", tests[0],
                        f"{len(tests)} test file(s): {tests}")

    if predicate == "referenced_by":
        refs = WI.find_references(root, subject)
        if not refs:
            return _verdict(True, predicate, subject, target, "REFUTED", None,
                            "no references found")
        return _verdict(True, predicate, subject, target, "FACT", None,
                        f"{len(refs)} reference(s), e.g. {refs[:5]}")

    if predicate == "imports":
        # subject is a path; target is a module name
        full = os.path.join(root, subject)
        try:
            with open(full, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read())
        except (OSError, SyntaxError):
            return _verdict(True, predicate, subject, target, "UNKNOWN", None,
                            "path unreadable or not Python")
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and target and (
                    (node.module or "") == target or (node.module or "").startswith(target + ".")):
                return _verdict(True, predicate, subject, target, "FACT", None,
                                f"imports from {node.module}")
            if isinstance(node, ast.Import):
                for a in node.names:
                    if target and a.name == target:
                        return _verdict(True, predicate, subject, target, "FACT", None,
                                        f"imports {a.name}")
        return _verdict(True, predicate, subject, target, "REFUTED", None,
                        f"does not import {target!r}")
