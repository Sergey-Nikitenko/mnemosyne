"""Capability tool executor — EXPERIMENTAL (speculative, recoverable).

Wraps a bounded tool executor and serves the context-stack tools
(`evidence_bundle`, `route`, `change_context`, `decision_context`, `context_packet`,
`compile_packet`, `context_need`) as read-only retrievals. It PROPOSES a
presentation; it never decides authority, freshness, or verification truth, and it
never mutates a file or authoritative state. Deleting this module (and its one
wiring line in apps/composition.py) leaves every Mnemosyne semantic intact — the
inner executor is unchanged and the trusted core never imports capability.
"""
from __future__ import annotations

from core.contracts import ToolCall, ToolResult
from capability import context as CAP
from capability import pipeline as PIPE
from capability import work_capsule as WC
from capability import toys as TOYS
from capability import operation as OP
from capability import delta as DELTA
from capability import verify as VERIFY
from capability import decisions as DEC
from capability import coherence as COH
from capability import backbrief as BB
from capability import simulator as SIM
from capability.store import CapabilityStore
from capability.gate_view import GateView
from capability.purchase import PurchaseGate, WORLD_MUTATORS


def _int(v, default):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


class CapabilityToolExecutor:
    def __init__(self, inner, root_provider, *, events_provider=None,
                 capabilities_provider=None, specs_provider=None,
                 state_dir=None, gate_view=None) -> None:
        self._inner = inner                # delegated for non-capability tools
        self._root = root_provider         # callable() -> str (current bounded root)
        self._events = events_provider     # callable() -> list[Event] (durable log)
        self._caps = capabilities_provider  # callable() -> list[str] (tool names)
        self._specs = specs_provider       # callable() -> list[ToolSpec]
        self._ack = None                   # last acknowledged world manifest (what_changed)
        self._ack_symbols = None           # last acknowledged symbol manifest (world_delta)
        self._store = CapabilityStore(state_dir)  # scratch bookkeeping (never authoritative)
        self._gate_view = gate_view if isinstance(gate_view, GateView) else GateView([])
        self._purchase = PurchaseGate()   # evidence repurchase gate (C1)

    def _ok(self, call, data):
        # Evidence Purchase (C1): FLAG a repurchase so it can be presented cheaply —
        # never reject. Cost-reduction, not semantic pressure.
        args = dict(call.arguments or {})
        verdict = self._purchase.verdict(call.tool_name, args)
        if verdict == "flag" and isinstance(data, dict):
            data = {**data, "repurchase": self._purchase.prior(call.tool_name, args),
                    "presentation_hint": "already-presented — consider a compact/reference form"}
        return ToolResult(tool_call=call, success=True, output=data)

    def _err(self, call, msg):
        return ToolResult(tool_call=call, success=False, output={}, error=msg)

    def _current_root(self) -> str:
        if callable(self._root):
            return self._root()
        return str(self._root or "")

    def _ev(self):
        return self._events() if callable(self._events) else []

    def _caps_list(self):
        return self._caps() if callable(self._caps) else []

    def _specs_dict(self):
        out = {}
        if callable(self._specs):
            for s in self._specs():
                out[getattr(s, "name", None)] = s
        return out

    def _record_ack(self):
        try:
            root = self._current_root()
            self._ack = WC.manifest(root)
            self._ack_symbols = DELTA.symbol_manifest(root)
        except Exception:
            pass

    def execute_tool(self, call: ToolCall) -> ToolResult:
        name = call.tool_name
        args = dict(call.arguments or {})
        try:
            if name == "evidence_bundle":
                symbol = str(args.get("symbol", ""))
                if not symbol:
                    return self._err(call, "evidence_bundle requires a symbol")
                return self._ok(call, CAP.evidence_bundle(self._current_root(), symbol))
            if name == "context_need":
                symbol = str(args.get("symbol", ""))
                if not symbol:
                    return self._err(call, "context_need requires a symbol")
                return self._ok(call, CAP.context_need(self._current_root(), symbol))
            if name == "route":
                goal = str(args.get("goal", ""))
                return self._ok(call, PIPE.route(
                    self._current_root(), goal,
                    intent=args.get("intent") or "DISCOVER",
                    subject=args.get("subject") or "",
                    limit=_int(args.get("limit"), 12)))
            if name == "change_context":
                manifest = args.get("manifest") or {}
                return self._ok(call, PIPE.change_context(self._current_root(), manifest))
            if name == "decision_context":
                decision = str(args.get("decision", ""))
                if not decision:
                    return self._err(call, "decision_context requires a decision")
                return self._ok(call, PIPE.decision_context(
                    self._current_root(), decision, args.get("manifest")))
            if name == "context_packet":
                goal = str(args.get("goal", ""))
                if not goal:
                    return self._err(call, "context_packet requires a goal")
                return self._ok(call, PIPE.context_packet(
                    self._current_root(), goal, str(args.get("frontier", "")),
                    args.get("decision"), args.get("hot"), args.get("manifest"),
                    args.get("obligations")))
            if name == "compile_packet":
                goal = str(args.get("goal", ""))
                if not goal:
                    return self._err(call, "compile_packet requires a goal")
                return self._ok(call, PIPE.compile_packet(
                    self._current_root(), goal, str(args.get("frontier", "")),
                    args.get("decision"), args.get("hot"), args.get("manifest"),
                    args.get("obligations"), _int(args.get("budget_bytes"), 16000)))
            if name == "work_capsule":
                out = WC.work_capsule(
                    self._current_root(), self._ev(), self._caps_list(),
                    goal=str(args.get("goal", "")),
                    acceptance=args.get("acceptance"),
                    hot=args.get("hot"))
                self._record_ack()
                return self._ok(call, out)
            # ---- little toys (orientation) ---------------------------------
            if name == "peek_symbol":
                return self._ok(call, TOYS.peek_symbol(self._current_root(), str(args.get("name", ""))))
            if name == "file_outline":
                return self._ok(call, TOYS.file_outline(self._current_root(), str(args.get("path", ""))))
            if name == "symbol_history":
                return self._ok(call, TOYS.symbol_history(self._current_root(), str(args.get("name", ""))))
            if name == "why_stale":
                return self._ok(call, TOYS.why_stale(self._current_root(), str(args.get("target", "")), self._ack))
            if name == "what_uses_this":
                return self._ok(call, TOYS.what_uses_this(self._current_root(), str(args.get("target", ""))))
            if name == "blast_radius":
                return self._ok(call, TOYS.blast_radius(self._current_root(), str(args.get("target", ""))))
            if name == "imports_for":
                return self._ok(call, TOYS.imports_for(self._current_root(), str(args.get("symbol", ""))))
            if name == "import_health":
                return self._ok(call, TOYS.import_health(self._current_root(), str(args.get("path", ""))))
            if name == "signature":
                return self._ok(call, TOYS.signature(self._current_root(), str(args.get("name", ""))))
            if name == "call_examples":
                return self._ok(call, TOYS.call_examples(self._current_root(), str(args.get("name", "")), _int(args.get("n"), 3)))
            if name == "test_for":
                return self._ok(call, TOYS.test_for(self._current_root(), str(args.get("target", ""))))
            if name == "failure_focus":
                return self._ok(call, TOYS.failure_focus(self._current_root(), self._ev()))
            if name == "show_contract":
                return self._ok(call, TOYS.show_contract(self._current_root(), str(args.get("tool", "")), self._specs_dict()))
            if name == "explain_rejection":
                return self._ok(call, COH.explain_rejection(
                    self._gate_view, self._ev(), str(args.get("call_id", ""))))
            if name == "next_mechanical_options":
                return self._ok(call, COH.next_mechanical_options(
                    self._current_root(), self._ev(), self._caps_list(), self._gate_view))
            if name == "capability_explain":
                return self._ok(call, COH.capability_explain(
                    self._gate_view, str(args.get("tool", ""))))
            if name == "house_consistency_check":
                return self._ok(call, COH.house_consistency_check(self._gate_view))
            if name == "verify_claim":
                return self._ok(call, BB.verify_claim(
                    self._current_root(), str(args.get("subject", "")),
                    str(args.get("predicate", "")), args.get("target")))
            if name == "simulate_context":
                return self._ok(call, SIM.simulate_context(
                    self._current_root(), str(args.get("goal", "")),
                    subject=args.get("subject"), intent=args.get("intent") or "DISCOVER",
                    budget=_int(args.get("budget"), 12000)))
            if name == "where_am_i":
                return self._ok(call, TOYS.where_am_i(self._current_root(), self._ev(), self._caps_list()))
            if name == "what_changed":
                prev = self._ack
                self._record_ack()
                return self._ok(call, TOYS.what_changed(self._current_root(), prev))
            if name == "why_is_this_here":
                return self._ok(call, TOYS.why_is_this_here(self._current_root(), str(args.get("id", ""))))
            # ---- OperationIntent (toy #2) ---------------------------------
            if name == "resolve_intent":
                return self._ok(call, OP.resolve_intent(
                    self._current_root(), str(args.get("intent", "")),
                    symbol=args.get("symbol"), objective=args.get("objective"),
                    replacement=args.get("replacement"), scope=args.get("scope")))
            if name == "execute_intent":
                resolved = OP.resolve_intent(
                    self._current_root(), str(args.get("intent", "")),
                    symbol=args.get("symbol"), objective=args.get("objective"),
                    replacement=args.get("replacement"), scope=args.get("scope"))
                if not resolved.get("ok"):
                    return self._err(call, resolved.get("error", "unresolvable intent"))
                if resolved.get("risk") != "WRITE":
                    return self._err(call, "read intent — use resolve_intent or evidence_bundle (execute_intent only mutates/verifies)")
                inner = self._inner.execute_tool(ToolCall(
                    tool_name=resolved["mechanism"], arguments=resolved["arguments"]))
                if inner.success:
                    self._purchase.reset()  # a mutation re-opens evidence
                return ToolResult(tool_call=call, success=inner.success,
                                  output={"intent": resolved["intent"],
                                          "mechanism": resolved["mechanism"],
                                          "arguments": resolved["arguments"],
                                          "freshness": resolved.get("freshness"),
                                          "result": inner.output},
                                  error=inner.error)
            # ---- World Delta (toy #3) + Verification Fabric (toy #4) ----
            if name == "world_delta":
                # a READ: do not consume the ack (that would blind what_changed)
                return self._ok(call, DELTA.world_delta(self._current_root(),
                                                       self._ack, self._ack_symbols))
            if name == "verify_fabric":
                def _run(scope, sym):
                    return self._inner.execute_tool(ToolCall(
                        tool_name="run_verification",
                        arguments={"scope": scope, "symbol": sym or ""}))
                out = VERIFY.verify_fabric(self._current_root(), args.get("symbol"), run=_run)
                self._record_ack()
                return ToolResult(tool_call=call, success=(out.get("status") == "PASS"),
                                  output=out)
            # ---- Decision Registry + Invalidation ------------------------
            if name == "record_decision":
                res = DEC.record_decision(
                    self._current_root(), self._store, str(args.get("claim", "")),
                    supported_by=args.get("supported_by") or [],
                    scope=str(args.get("scope", "")),
                    decision_id=args.get("decision_id"))
                return ToolResult(tool_call=call, success=bool(res.get("ok")),
                                  output=res, error=None if res.get("ok") else res.get("error"))
            if name == "decision_status":
                return self._ok(call, DEC.decision_status(
                    self._current_root(), self._store, str(args.get("decision_id", ""))))
            if name == "reaffirm_decision":
                res = DEC.reaffirm_decision(self._current_root(), self._store,
                                            str(args.get("decision_id", "")))
                return ToolResult(tool_call=call, success=bool(res.get("ok")),
                                  output=res, error=None if res.get("ok") else res.get("error"))
            if name == "retire_decision":
                res = DEC.retire_decision(self._store, str(args.get("decision_id", "")))
                return ToolResult(tool_call=call, success=bool(res.get("ok")),
                                  output=res, error=None if res.get("ok") else res.get("error"))
        except Exception as exc:
            return self._err(call, str(exc))
        result = self._inner.execute_tool(call)
        # a world mutation re-opens all evidence (freshness at the write boundary)
        if result.success and call.tool_name in WORLD_MUTATORS:
            self._purchase.reset()
        return result
