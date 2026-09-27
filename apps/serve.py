"""`mnemosyne serve` — a lifecycle surface, not a runtime or authority layer (4.10).

    mnemosyne serve -> CLI args -> MnemosyneConfig -> CompositionRoot.build(config)
                    -> system.console_app -> HTTP/WebSocket server
                    -> (shutdown) -> system.close()

The CLI translates explicit operator configuration into `MnemosyneConfig`,
delegates construction to `CompositionRoot`, serves the EXISTING `console_app`
(no second application), and guarantees closure of the composed system via
`finally`. It never constructs a store, authority, runner, projector, learning, or
federation component itself; it never authorizes, mutates memory, reinterprets
events, or manufactures outcomes. Deleting this module leaves every Mnemosyne
semantic intact — it is a disposable lifecycle surface.

Run (from a repository checkout, no packaging required):

    python -m apps.serve serve --workspace ./workspace --user-id user/alice \
        --agent-id agent/mnemosyne --model-id model/fake

The leading `serve` is optional (so the same code works behind a future
`mnemosyne` console-script entry point). Configuration is deliberately primitive:
one flag per required `MnemosyneConfig` field. If that becomes ugly in repeated
use, that is the operational evidence that earns a serializable config file — it
is not hidden here.
"""
from __future__ import annotations

import argparse
import sys

from core.contracts import AgentIdentity, ModelIdentity, Risk, UserIdentity
from apps.composition import CompositionRoot, CorpusEntry, MnemosyneConfig
from control.tools import ToolSpec


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mnemosyne",
        description="Serve the Mnemosyne Operator Console (a projection of authoritative state).",
    )
    parser.add_argument("--workspace", required=True, metavar="DIR",
                        help="workspace directory — the persistence root (required)")
    parser.add_argument("--user-id", default="", metavar="ID",
                        help="user identity, e.g. user/alice (who)")
    parser.add_argument("--agent-id", default="", metavar="ID",
                        help="agent identity, e.g. agent/mnemosyne (which role)")
    parser.add_argument("--model-id", default="unset", metavar="ID",
                        help="model identity, e.g. model/fake (which reasoning backend)")
    parser.add_argument("--model-backend", default="fake", choices=["fake", "deepseek"],
                        help="reasoning backend: fake (deterministic echo) or deepseek (real cloud model)")
    parser.add_argument("--model-api-key-file", default="", metavar="PATH",
                        help="path to the DeepSeek API key file (secret, never logged)")
    parser.add_argument("--model-name", default="deepseek-chat", metavar="NAME",
                        help="provider model name for the deepseek backend")
    parser.add_argument("--project-dir", default="", metavar="DIR",
                        help="bounded filesystem root for the file tools (empty = fake tools)")
    parser.add_argument("--max-tool-rounds", type=int, default=1000, metavar="N",
                        help="max model->tools cycles per attempt before a terminal answer (default 1000; continuous work is long-lived)")
    parser.add_argument("--host", default="127.0.0.1",
                        help="bind host (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000,
                        help="bind port (default 8000)")
    parser.add_argument("--worker-id", default="worker-1", metavar="ID",
                        help="worker identity stamped on claims (default worker-1)")
    parser.add_argument("--lease-seconds", type=float, default=30.0, metavar="SECS",
                        help="worker recovery lease: a CLAIMED task older than this is stale (default 30)")
    return parser


def _default_tools():
    """The file/command capability surface the model may propose in a serve run.
    Every tool is a ToolSpec (data); authority is the policy's, never the model's.
    `run_elevated` is the OS-elevation path (the Windows UAC prompt) — it is a WRITE
    risk, so it always requires operator approval."""
    desc = ("Run one executable with arguments directly. This is not a shell; shell "
            "operators (|, >, <, &&, ||, ;, $, backticks) and built-ins are not "
            "interpreted — they are literal arguments. Invoke programs directly, e.g. "
            "py -m pytest -q.")
    return (
        ToolSpec("list_dir", "list files in a directory; path is RELATIVE to the project root and absolute paths are rejected", Risk.READ,
                 {"type": "object", "properties": {"path": {"type": "string"}}}),
        ToolSpec("read_file", "read a file's contents; path is RELATIVE to the project root and absolute paths are rejected", Risk.READ,
                 {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}),
        ToolSpec("edit_file", "replace a unique old string with new string in a file; path is RELATIVE to the project root", Risk.WRITE,
                 {"type": "object", "properties": {"path": {"type": "string"}, "old_string": {"type": "string"}, "new_string": {"type": "string"}}, "required": ["path", "old_string", "new_string"]}),
        ToolSpec("write_file", "write a file's full contents; path is RELATIVE to the project root", Risk.WRITE,
                 {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}),
        ToolSpec("run_command", desc, Risk.WRITE,
                 {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}),
        ToolSpec("run_elevated", "run a command with OS elevation (triggers the Windows UAC prompt); requires operator approval", Risk.WRITE,
                 {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}),
        ToolSpec("workspace_map", "return a deterministic map of the project's files and directories", Risk.READ,
                 {"type": "object", "properties": {}}),
        ToolSpec("find_symbol", "find where a function or class is defined by name, returning file and line numbers", Risk.READ,
                 {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}),
        ToolSpec("find_references", "find usages of a symbol across the project", Risk.READ,
                 {"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]}),
        ToolSpec("related_tests", "find test files that reference a symbol or module", Risk.READ,
                 {"type": "object", "properties": {"symbol": {"type": "string"}, "module": {"type": "string"}}}),
        ToolSpec("get_symbol_source", "return the exact source and hashes for a function or class", Risk.READ,
                 {"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]}),
        ToolSpec("dependency_neighborhood", "return callers, called symbols, and tests for a symbol", Risk.READ,
                 {"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]}),
        ToolSpec("verification_candidates", "return the direct tests for a file or symbol", Risk.READ,
                 {"type": "object", "properties": {"path_or_symbol": {"type": "string"}}, "required": ["path_or_symbol"]}),
        ToolSpec("changed_since", "return added/modified/deleted files since a previous manifest", Risk.READ,
                 {"type": "object", "properties": {"manifest": {"type": "object"}}}),
        ToolSpec("patch_symbol", "replace a function or class body via AST span, guarded by an expected symbol hash", Risk.WRITE,
                 {"type": "object", "properties": {"path": {"type": "string"}, "symbol": {"type": "string"}, "expected_hash": {"type": "string"}, "replacement": {"type": "string"}}, "required": ["path", "symbol", "expected_hash", "replacement"]}),
        ToolSpec("run_verification", "run tests and return a structured result (related tests for a symbol or the full suite)", Risk.WRITE,
                 {"type": "object", "properties": {"scope": {"type": "string"}, "symbol": {"type": "string"}, "path_or_symbol": {"type": "string"}}}),
        ToolSpec("list_projects", "list the remembered projects (name and path) the operator has registered", Risk.READ,
                 {"type": "object", "properties": {}}),
        ToolSpec("select_project", "switch the observation scope to a remembered project by name (navigation, not mutation — the project's files are unchanged; later writes are still gated separately)", Risk.READ,
                 {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}),
        # ---- Context capability layer (EXPERIMENTAL read-side; proposes, never decides) ----
        ToolSpec("evidence_bundle", "one retrieval for a symbol: source + references + callers + calls + tests + verification candidates (deterministic, read-only)", Risk.READ,
                 {"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]}),
        ToolSpec("route", "Context Router: goal + intended operation -> candidate evidence set, each with kind/reason/tier/cost/freshness/epistemic/retrieval handle (intent: DISCOVER/LOCATE/READ/MUTATE/VERIFY/DECLARE) — read-only, handles only", Risk.READ,
                 {"type": "object", "properties": {"goal": {"type": "string"}, "intent": {"type": "string"}, "subject": {"type": "string"}, "limit": {"type": "integer"}}}),
        ToolSpec("change_context", "added/modified/deleted files since a previous manifest hash map (deterministic, read-only)", Risk.READ,
                 {"type": "object", "properties": {"manifest": {"type": "object"}}, "required": ["manifest"]}),
        ToolSpec("decision_context", "evidence assembled around an unresolved decision (a symbol or module), with an optional change delta (read-only)", Risk.READ,
                 {"type": "object", "properties": {"decision": {"type": "string"}, "manifest": {"type": "object"}}, "required": ["decision"]}),
        ToolSpec("context_packet", "standardized model-facing context projection: goal, frontier, decision, hot evidence, relationships, changes, obligations (read-only)", Risk.READ,
                 {"type": "object", "properties": {"goal": {"type": "string"}, "frontier": {"type": "string"}, "decision": {"type": "string"}, "hot": {"type": "array"}, "manifest": {"type": "object"}, "obligations": {"type": "array"}}, "required": ["goal"]}),
        ToolSpec("compile_packet", "context_packet reduced to a token/evidence budget (the decision context tiers down when over budget) (read-only)", Risk.READ,
                 {"type": "object", "properties": {"goal": {"type": "string"}, "frontier": {"type": "string"}, "decision": {"type": "string"}, "hot": {"type": "array"}, "manifest": {"type": "object"}, "obligations": {"type": "array"}, "budget_bytes": {"type": "integer"}}, "required": ["goal"]}),
        ToolSpec("context_need", "bounded query: explicitly request evidence for ONE symbol under a MODEL_REQUESTED reason (read-only)", Risk.READ,
                 {"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]}),
        ToolSpec("work_capsule", "assemble the Work Capsule: the job's computational state (goal, frontier, completed, failed attempts, changes, verification debt, blocked approvals, hot evidence, world version, capabilities) — deterministic, read-only, replaces re-reading the transcript", Risk.READ,
                 {"type": "object", "properties": {"goal": {"type": "string"}, "acceptance": {"type": "string"}, "hot": {"type": "array"}}}),
        # ---- little toys (orientation; deterministic, read-only) ----
        ToolSpec("peek_symbol", "tiny cousin of get_symbol_source: a symbol's location, kind, docstring, and methods — NO body, cheap orientation", Risk.READ,
                 {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}),
        ToolSpec("file_outline", "AST skeleton of a file: imports, classes + methods, functions, top-level constants — no source bodies", Risk.READ,
                 {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}),
        ToolSpec("symbol_history", "has this symbol been moving? recent version history (git if available) + current hash", Risk.READ,
                 {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}),
        ToolSpec("why_stale", "explain the mechanical cause of staleness for a symbol/decision (vs the acknowledged world version)", Risk.READ,
                 {"type": "object", "properties": {"target": {"type": "string"}, "manifest": {"type": "object"}}, "required": ["target"]}),
        ToolSpec("what_uses_this", "dumb convenience: imported_by / called_by / tests / public_exports / config_refs counts for a symbol", Risk.READ,
                 {"type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]}),
        ToolSpec("blast_radius", "if I change this, what becomes mechanically suspect: direct + transitive dependents, tests, risk shape", Risk.READ,
                 {"type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]}),
        ToolSpec("imports_for", "the canonical import line for a symbol + whether it is defined in more than one place", Risk.READ,
                 {"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]}),
        ToolSpec("import_health", "post-edit housekeeping for a file: missing imports, unused imports (deterministic, not a linter)", Risk.READ,
                 {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}),
        ToolSpec("signature", "a function/class signature (parameters + return) without retrieving the body", Risk.READ,
                 {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}),
        ToolSpec("call_examples", "real call sites of a function/class from the project, with source snippets", Risk.READ,
                 {"type": "object", "properties": {"name": {"type": "string"}, "n": {"type": "integer"}}, "required": ["name"]}),
        ToolSpec("test_for", "opinionated related_tests: direct + indirect test CASES (not just files) for a target", Risk.READ,
                 {"type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]}),
        ToolSpec("failure_focus", "after a failed verification: what's on record + suggested evidence (from the durable log)", Risk.READ,
                 {"type": "object", "properties": {}}),
        ToolSpec("show_contract", "executable documentation for a tool: requires / causes / may fail / does not, from the capability spec", Risk.READ,
                 {"type": "object", "properties": {"tool": {"type": "string"}}, "required": ["tool"]}),
        ToolSpec("explain_rejection", "when Nexus said no, explain the rejection verdict and the allowed operation classes", Risk.READ,
                 {"type": "object", "properties": {"call_id": {"type": "string"}}, "required": ["call_id"]}),
        ToolSpec("next_mechanical_options", "the doors that currently exist: phase, available vs unavailable operation classes (never tells you what to think)", Risk.READ,
                 {"type": "object", "properties": {}}),
        ToolSpec("where_am_i", "one-line orientation: goal, phase, frontier, world, dirty, verification debt", Risk.READ,
                 {"type": "object", "properties": {}}),
        ToolSpec("what_changed", "delta since the model's most recent acknowledged world version", Risk.READ,
                 {"type": "object", "properties": {}}),
        ToolSpec("why_is_this_here", "why an object is in context: reason / tier / freshness (for symbols/paths today)", Risk.READ,
                 {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}),
        # ---- OperationIntent (toy #2) ----
        ToolSpec("resolve_intent", "name the need, get the mechanism: resolve LOCATE_IMPLEMENTATION / MODIFY_SYMBOL / VERIFY_CHANGE to the exact tool + arguments + freshness precondition + risk (read-only, no execution)", Risk.READ,
                 {"type": "object", "properties": {"intent": {"type": "string"}, "symbol": {"type": "string"}, "objective": {"type": "string"}, "replacement": {"type": "string"}, "scope": {"type": "string"}}, "required": ["intent"]}),
        ToolSpec("execute_intent", "resolve a mutating/verifying intent AND execute it through the bounded executor (MODIFY_SYMBOL or VERIFY_CHANGE; read intents are refused)", Risk.WRITE,
                 {"type": "object", "properties": {"intent": {"type": "string"}, "symbol": {"type": "string"}, "objective": {"type": "string"}, "replacement": {"type": "string"}, "scope": {"type": "string"}}, "required": ["intent"]}),
        # ---- World Delta (toy #3) + Verification Fabric (toy #4) ----
        ToolSpec("world_delta", "the world's movement since the acknowledged version: files (added/modified/deleted), symbols (changed/added/removed, hash-precise), changed tests, decisions-invalidated, current world version — richer than changed_since (read-only)", Risk.READ,
                 {"type": "object", "properties": {}}),
        ToolSpec("verify_fabric", "cheapest-sufficient verification with escalation: run related tests first, escalate to the full suite only when cross-module; return facts (status/level/passed/failed/failures/implicated) instead of terminal dumps", Risk.WRITE,
                 {"type": "object", "properties": {"symbol": {"type": "string"}}}),
        # ---- Decision Registry + Invalidation (challengeable intelligence) ----
        ToolSpec("record_decision", "author a semantic conclusion: claim + supporting evidence anchors (symbols/paths) + scope — Nexus stores provenance/freshness, NOT reasoning", Risk.WRITE,
                 {"type": "object", "properties": {"claim": {"type": "string"}, "supported_by": {"type": "array", "items": {"type": "string"}}, "scope": {"type": "string"}, "decision_id": {"type": "string"}}, "required": ["claim"]}),
        ToolSpec("decision_status", "report a decision's mechanical validity: CURRENT / STALE (which supporting evidence changed, h1->h2) / RETIRED, and whether RECONSIDER/REAFFIRM/RETIRE is required (read-only)", Risk.READ,
                 {"type": "object", "properties": {"decision_id": {"type": "string"}}, "required": ["decision_id"]}),
        ToolSpec("reaffirm_decision", "re-anchor a decision's supporting evidence to the current world, returning it to CURRENT", Risk.WRITE,
                 {"type": "object", "properties": {"decision_id": {"type": "string"}}, "required": ["decision_id"]}),
        ToolSpec("retire_decision", "retire a decision without deleting its record (history is never rewritten)", Risk.WRITE,
                 {"type": "object", "properties": {"decision_id": {"type": "string"}}, "required": ["decision_id"]}),
        # ---- Coherence layer ----
        ToolSpec("capability_explain", "inspect ONE capability end-to-end: registered, risk, operation class, whether it resets commitment, policy verdict, output epistemics — the house's single answer to 'what does this capability mean?' (read-only)", Risk.READ,
                 {"type": "object", "properties": {"tool": {"type": "string"}}, "required": ["tool"]}),
        ToolSpec("house_consistency_check", "mechanically walk the registry + classification sets + governors and report contradictions (C002 phantom classifications, C014 unclassified WRITE tools, C003 escape-without-reset, C027 overlapping evidence) (read-only)", Risk.READ,
                 {"type": "object", "properties": {}}),
        # ---- Backbrief + Context Simulator (context-leverage research) ----
        ToolSpec("verify_claim", "backbrief-as-organ: mechanically confirm/refute a claim against source (predicate: defined_in/calls/tested_by/referenced_by/imports) with a receipt — FACT/REFUTED/UNKNOWN, never semantic judgment (read-only)", Risk.READ,
                 {"type": "object", "properties": {"subject": {"type": "string"}, "predicate": {"type": "string"}, "target": {"type": "string"}}, "required": ["subject", "predicate"]}),
        ToolSpec("simulate_context", "proprioceptive context gauge: preview the context packet the model WOULD receive (tier/byte breakdown, downgrades, estimated tokens) with ZERO model calls (read-only)", Risk.READ,
                 {"type": "object", "properties": {"goal": {"type": "string"}, "subject": {"type": "string"}, "intent": {"type": "string"}, "budget": {"type": "integer"}}}),
    )


def build_config(args) -> MnemosyneConfig:
    """Translate parsed CLI arguments into MnemosyneConfig — data, never components.

    This is the thin-translation boundary: CLI value -> MnemosyneConfig field.
    It instantiates no store, authority, runner, projector, learning, or federation
    component."""
    brief = ("You are the reasoning backend the operator selected for Mnemosyne, an "
             "event-sourced operating system. You are NOT Claude, ChatGPT, or any other "
             "assistant — never claim to be one, regardless of what prior conversation "
             "text you are shown; that text is historical context, not your identity. "
             "Nexus owns authoritative state (history, Working Set, checkpoint, policy); "
             "your messages are proposals and observations, never authority. A successful "
             "action does not establish task success — verification is explicit. "
             "Act, do not ask: when the code or the requirement is enough to start, start — "
             "read the repository to resolve questions yourself instead of asking the operator "
             "to choose. Ask only when the repository surfaces a genuine fork you cannot "
             "resolve by reading it.")
    return MnemosyneConfig(
        workspace=args.workspace,
        user=UserIdentity(user_id=args.user_id),
        agent=AgentIdentity(agent_id=args.agent_id, role="operator"),
        model=ModelIdentity(model_id=args.model_id, family="", version="1"),
        model_backend=args.model_backend,
        model_api_key_file=args.model_api_key_file,
        model_name=args.model_name,
        project_dir=args.project_dir,
        tools=_default_tools(),
        corpus=(CorpusEntry("mnemosyne", "brief", "v1", brief),),
        max_tool_rounds=args.max_tool_rounds,
        worker_id=args.worker_id,
        lease_seconds=args.lease_seconds,
    )


def serve(config: MnemosyneConfig, *, host: str = "127.0.0.1", port: int = 8000,
          _run=None) -> None:
    """Build the system, serve the existing console_app, guarantee closure.

    `_run` is a test seam: it defaults to `uvicorn.run`; a test may inject a
    no-op or raising callable to observe lifecycle ownership without binding a
    real socket."""
    import uvicorn
    run = _run or uvicorn.run
    system = CompositionRoot().build(config)
    print(f"Serving Mnemosyne Operator Console at http://{host}:{port} (Ctrl+C to stop)")
    try:
        run(system.console_app, host=host, port=port)
    finally:
        system.close()  # exactly one close; guaranteed on normal and exceptional exit


def worker(config: MnemosyneConfig, *, lease_seconds: float | None = None,
           poll_seconds: float = 1.0, stop=None, _run_one=None) -> None:
    """Run a supported worker (OBS-007 / Phase 4.11).

    ONE startup recovery pass (recover stale work using the configured lease), then a
    bounded drain loop whose ONLY execution primitive is `runtime.run_one()`
    (claim → run → complete/fail). Idle waiting = a short interruptible sleep when the
    queue is empty. Graceful termination = `stop` is set: the in-flight `run_one()`
    completes and no new task is claimed. `close()` writes nothing, so shutdown is
    observably neutral (an `action.requested` with no terminal survives as
    attempted/unknown).

    `_run_one` is a test seam (defaults to `system.runtime.run_one`). The worker adds no
    authority, no heartbeat, no scheduler, and no new execution semantic — recovery policy
    already exists (AD-019); the worker only decides WHEN to invoke it (once, at startup)."""
    import threading
    import time

    from execution.recovery import RecoveryManager

    system = CompositionRoot().build(config)
    run_one = _run_one or system.runtime.run_one
    stop = stop if stop is not None else threading.Event()
    lease = lease_seconds if lease_seconds is not None else config.lease_seconds
    try:
        recovered = RecoveryManager(system.runtime.queue, lease).recover()
        if recovered:
            print(f"worker: recovered {len(recovered)} stale task(s)")
        while not stop.is_set():
            outcome = run_one()
            if outcome is None:
                # queue empty -> idle wait (interruptible by the stop signal)
                stop.wait(poll_seconds)
        # loop exited via stop: the in-flight run_one() already completed; claim no new task
    finally:
        system.close()


def main(argv=None) -> int:
    import signal
    import threading

    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "worker":
        argv = argv[1:]
        args = _build_parser().parse_args(argv)
        stop = threading.Event()

        def _handle(signum, frame):
            stop.set()

        signal.signal(signal.SIGINT, _handle)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, _handle)
        worker(build_config(args), lease_seconds=args.lease_seconds, stop=stop)
        return 0
    if argv and argv[0] == "serve":
        argv = argv[1:]  # accept the optional 'serve' subcommand
    args = _build_parser().parse_args(argv)
    serve(build_config(args), host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
