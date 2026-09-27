"""OperationIntent — EXPERIMENTAL (speculative, recoverable).

Toy #2: the model names the NEED; Nexus resolves the mechanism.

    MODIFY_SYMBOL(symbol="score_event", objective="reject malformed scores")
        ↓ capability resolver
    patch_symbol(path, symbol, expected_hash=<current>, replacement=...)
        ↓ freshness precondition  (the current symbol hash — the model never computes it by hand)
        ↓ authority              (the orchestrator gates the WRITE tool)
        ↓ execution              (the bounded filesystem executor)
        ↓ verification debt      (the evaluator / cost telemetry)

`resolve_intent` is a pure, deterministic READ: it returns the concrete tool +
prepared arguments + freshness precondition + risk, WITHOUT executing. It never
decides the change is correct — that is the model's semantic judgment. A wrong
resolution is recoverable (re-resolve); authority and freshness stay in the
trusted core. Delete this module and every Mnemosyne semantic is intact.
"""
from __future__ import annotations

from execution import workspace_intel as WI

INTENTS = ("LOCATE_IMPLEMENTATION", "MODIFY_SYMBOL", "VERIFY_CHANGE")


def resolve_intent(root, intent, symbol=None, objective=None, replacement=None,
                   scope=None) -> dict:
    """Resolve a semantic intent to a concrete tool call. Deterministic, pure."""
    intent = (intent or "").upper()
    symbol = symbol or ""

    if intent == "LOCATE_IMPLEMENTATION":
        if not symbol:
            return {"intent": intent, "ok": False,
                    "error": "LOCATE_IMPLEMENTATION requires a symbol"}
        return {"intent": intent, "ok": True, "mechanism": "evidence_bundle",
                "arguments": {"symbol": symbol}, "risk": "READ", "freshness": None}

    if intent == "MODIFY_SYMBOL":
        if not symbol:
            return {"intent": intent, "ok": False,
                    "error": "MODIFY_SYMBOL requires a symbol"}
        src = WI.get_symbol_source(root, symbol)
        if src is None:
            return {"intent": intent, "ok": False, "error": f"symbol {symbol!r} not found"}
        if replacement is None:
            return {"intent": intent, "ok": False,
                    "error": "MODIFY_SYMBOL requires the replacement body"}
        return {"intent": intent, "ok": True, "mechanism": "patch_symbol",
                "arguments": {"path": src["path"], "symbol": symbol,
                              "expected_hash": src["symbol_hash"],
                              "replacement": replacement},
                "objective": objective,
                "risk": "WRITE",
                "freshness": {"expected_hash": src["symbol_hash"],
                              "path": src["path"]}}

    if intent == "VERIFY_CHANGE":
        args = {"scope": "related", "symbol": symbol} if symbol else {"scope": "full"}
        return {"intent": intent, "ok": True, "mechanism": "run_verification",
                "arguments": args, "risk": "WRITE", "freshness": None}

    return {"intent": intent, "ok": False,
            "error": f"unknown intent {intent!r}; choose one of {INTENTS}"}
