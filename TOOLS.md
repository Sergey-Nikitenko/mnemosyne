# Tools — catalog and usage ledger

Tracks every tool (built + proposed): what it does, build status, and how many times the model
used it in ordinary Argus work. Updated after each run.

**Build threshold (frozen):** build a proposed tool when (a) its target mechanical work or failure
mode is observed in **≥3 ordinary-work runs**, or (b) the failure mode it fixes is **dominant**
(≥50% of a run's failures). This matches the recurrence/dominance discipline. (`patch_symbol`
gets no special treatment — never instruct the model to use it.)

## Built tools (track usage)

| tool | what it does | uses (ordinary work, approx) |
| --- | --- | --- |
| list_dir / read_file / write_file / edit_file / run_command | base filesystem + verification | high |
| workspace_map | deterministic file/dir map (no content) | ~9 |
| find_symbol | symbol name → path:line (AST index) | ~3 |
| find_references | symbol → usages path:line | ~6 |
| related_tests | symbol/module → associated test files | ~2 |
| get_symbol_source | symbol → exact source + imports + hashes | 1 |
| dependency_neighborhood | symbol → callers/callees/imports/tests | 0 |
| verification_candidates | path/symbol → direct/dependent/related tests | 0 |
| patch_symbol | version-guarded AST-span edit (rejects stale) | 0 |
| changed_since | added/modified/deleted vs observed state | 0 |

## Proposed tools (build when threshold fires)

| tool | what it does | target cost / failure it fixes |
| --- | --- | --- |
| diff_artifact / diff_since | source diffs with symbol boundaries | re-reading whole files for changes |
| project_health | syntax/import/test/git/dirty snapshot | early-round health discovery |
| diagnose_change | post-mutation parse/import/circular/missing | mechanical breakage discovery |
| run_verification | execute scope → structured pass/fail | command construction + output parsing |
| insert_symbol / add_import / rename_symbol | mechanical source transforms | full-file regen for tiny edits |
| find_api_usage | call sites with surrounding snippets | reading every referencing file |
| find_dataflow | static assignment/attribute/constructor flow | reconstructing data movement |
| test_failure_digest | group failures by root/module/frame | reading repetitive failures |
| error_to_source | traceback frame → project location | post-failure navigation |
| workspace_query | structured deterministic queries | ad-hoc grep/py one-liners |
| artifact_status | CURRENT/STALE/UNKNOWN/REMOVED + hash | asking whether evidence is current |
| work_delta | factual work since checkpoint | rebuilding work state after restart |
| record_design_claim | model externalizes a decision/claim | losing design state across tranches |
| design_claims | return claims, mark stale | re-solving explicit decisions |
| loop_report | detect repeated unchanged cycles | burning rounds on cycling |
| budget_status | tranche/frontier/pressure view | guessing remaining budget |

## Failure-mode ledger (drives the build threshold)

| failure mode | count (ordinary work) | dominant? |
| --- | --- | --- |
| old-string-miss | 8 (ARG2-01:3 · ARG2-03:2 · ARG2-04:3) | **YES — 8/8** |
| missing-path | 0 | no |
| other | 0 | no |

**Threshold fired:** old-string-miss is dominant (8/8 across 3 jobs). Surgical-edit hardening is
earned. Two candidate fixes, both already built: normalize `edit_file` matching (line-ending/
whitespace tolerance) — the tool the model actually uses — or the model adopting `patch_symbol`
(built, 0 uses). The model has not yet adopted the symbol-level edit tool.

## Capability layer (EXPERIMENTAL)

Deterministic, speculative, recoverable. These propose a presentation; the trusted tool policy
still gates every mutation. Built deliberately, outside the conformance suite until promoted.
**Wired as read-only tools** in the serve path (`apps/serve.py` → `capability/executor.py`);
`context_need` is a bounded explicit query (MODEL_REQUESTED) — no budget, no escape hatch.

| function | what it provides | status |
| --- | --- | --- |
| `evidence_bundle(symbol)` | source + references + callers + calls + tests + verification candidates in one retrieval | EXPERIMENTAL |
| `route(goal, ...)` | candidate evidence set from goal text | EXPERIMENTAL |
| `HotContext` | small active working neighborhood | EXPERIMENTAL |
| `change_context(prev)` | h1 -> h2 deltas | EXPERIMENTAL |
| `decision_context(x)` | evidence around the unresolved decision | EXPERIMENTAL |
| `context_packet(...)` | standardized model-facing projection | EXPERIMENTAL |
| `compile_packet(...)` | compose + budget-reduce | EXPERIMENTAL |
| `context_need(symbol)` | bounded query (MODEL_REQUESTED); no budget reset | EXPERIMENTAL |
| `tier_presentation(obj, tier)` | FULL / EXCERPT / DELTA / SUMMARY / REFERENCE | EXPERIMENTAL |
| `Reason` (enum) | GOAL / FRONTIER / DIRECT_TARGET / DEPENDENCY / TEST_IMPACT / RECENT_CHANGE / CONTINUITY / MODEL_REQUESTED / POLICY_REQUIRED / UNRESOLVED_DECISION | EXPERIMENTAL |

## Toy box (build order — frozen)

Built deliberately as EXPERIMENTAL capability toys; promotion to the trusted core still needs
3 ordinary-work observations that execution *depends* on the mechanism. Order attacks one term of
T_total each: rediscovery → tool mechanics → rereading → verification waste.

| # | toy | attacks | status |
| --- | --- | --- | --- |
| 1 | **Work Capsule** (`capability/work_capsule.py`) | rediscovery — "where was I?" | **BUILT** — read-only `work_capsule` tool; reconstructs goal / frontier / completed / failed attempts / changes / verification debt / blocked approvals / hot evidence / world version / capabilities from the durable event log |
| 2 | OperationIntent (`capability/operation.py`) | tool mechanics — model names intent, Nexus resolves the tool | **BUILT** — `resolve_intent` (READ) + `execute_intent` (WRITE): LOCATE_IMPLEMENTATION / MODIFY_SYMBOL / VERIFY_CHANGE → exact tool + args + freshness hash + risk |
| 3 | World Delta (`capability/delta.py`) | rereading — exact file/symbol/tests changes since last known version | **BUILT** — `world_delta` (READ): files + hash-precise symbol changes + changed tests + world version |
| 4 | Verification Fabric (`capability/verify.py`) | verification waste — cheapest sufficient verification, facts not terminal dumps | **BUILT** — `verify_fabric` (WRITE): related → full escalation, structured facts; recognized by the evaluator |
| 5 | Decision Registry + invalidation (`capability/decisions.py` + `capability/store.py`) | re-deciding — model authors the conclusion, Nexus keeps its validity envelope | **BUILT** — `record_decision` / `decision_status` (CURRENT/STALE/RETIRED) / `reaffirm_decision` / `retire_decision` over a capability-owned scratch store (never authoritative) |
| 6 | Work Graph | workflow topology | not started |
| 7 | Transactional ChangeSet | multi-edit bookkeeping | not started |
| 8 | Shadow Workspace | cost of experimentation | not started |
| 9 | Capability Graph | legal operation transitions | not started |
| 10 | Environment Adapters | environment-specific mechanics | not started |

Work Capsule field sourcing is explicit and honest: `declared_decisions` / `evidence_refs` /
`ineligible_mechanisms` / `acceptance_contract` return empty/None until their source toy lands
(#5 / #9) — a capsule never invents a field it has no durable source for.

## Little toys (orientation — wired, EXPERIMENTAL)

`capability/toys.py` — 18 tiny deterministic read-only tools over the workspace-intelligence
primitives. Each answers one cheap question; all wired as `Risk.READ` tools.

| tool | answers |
| --- | --- |
| `peek_symbol(name)` | location + kind + docstring + methods, no body |
| `file_outline(path)` | imports / classes+methods / functions / constants, no bodies |
| `symbol_history(name)` | recent version history (git) + current hash |
| `why_stale(target)` | mechanical cause of staleness (vs acknowledged world; decisions need #5) |
| `what_uses_this(target)` | imported_by / called_by / tests / public_exports / config_refs |
| `blast_radius(target)` | direct + transitive dependents, tests, risk shape |
| `imports_for(symbol)` | canonical import line + definition collision |
| `import_health(path)` | possibly_missing / unused imports (deterministic, CANDIDATE-strength, not a linter) |
| `signature(name)` | parameter + return signature, no body |
| `call_examples(name, n)` | real call sites with source snippets |
| `test_for(target)` | direct + indirect test CASES (not just files) |
| `failure_focus()` | last verification failure on record + suggested evidence |
| `show_contract(tool)` | requires / causes / may fail / does not, from the capability spec |
| `explain_rejection(call_id)` | the governor's verdict + allowed operation classes |
| `next_mechanical_options()` | the doors that currently exist (available vs unavailable) |
| `where_am_i()` | goal / phase / frontier / world / dirty / debt |
| `what_changed()` | delta since the last acknowledged world version |
| `why_is_this_here(id)` | reason / tier / freshness (symbols/paths today) |
| `verify_claim(subject, predicate, target)` | **backbrief-as-organ**: mechanical FACT/REFUTED/UNKNOWN with a receipt (defined_in/calls/tested_by/referenced_by/imports) — never semantic judgment |
| `simulate_context(goal, subject, intent, budget)` | **proprioceptive context gauge**: preview the packet (tier/byte breakdown, downgrades, estimated tokens) with zero model calls |

## Coherence layer (the house describes ONE reality)

After the capability buildout, a systems read surfaced two smells: tools returning
something stronger than they know (A1–A9), and surfaces re-encoding governor truth in
parallel (B1/B2/B3). The coherence layer makes every surface a projection of the same sources.

**Constitutional law (added after the Progress Obligation retirement):**

> Optimize the environment, not the model's judgment. Mnemosyne may reduce the cost of
> obtaining, maintaining, presenting, executing, and verifying work; it must not manufacture
> semantic urgency, and a model must never gain computational freedom by writing something.

**Hard rule (verbatim):** tool calls are never capped. The orchestrator's `max_tool_rounds`
enforcement and the `RepetitionGate` read-refusal are removed; only the wall-clock timeout and the
operator stop terminate a run. Repetition is observed (flagged), never blocked.

**Refinement:** local deterministic capability invocation is **unmetered by default** — resource
policy governs actual scarce resources and consequential effects, never invocation count. The
framework must never make ignorance cheaper than asking the house. Capabilities carry a
`CapabilityCost` (`capability/cost.py`): FREE_LOCAL_READ / CONTEXT_BEARING / COMPUTE_HEAVY /
WORLD_MUTATING / EXTERNAL_PAID / IRREVERSIBLE, rendered by `capability_explain`.

| primitive | what it is | file |
| --- | --- | --- |
| **GateView** | ONE read-only projection of "what is admissible now" (registration, risk, operation class, policy verdict, menu) — reads the same `execution/commitment` sets the orchestrator enforces; classification is TELEMETRY, not coercion | `capability/gate_view.py` |
| **Epistemic Output Contract** | FACT / MEASUREMENT / HEURISTIC / INFERENCE / CANDIDATE / UNKNOWN; a weaker class may inform a stronger decision but never masquerade as one | `capability/epistemic.py` |
| **CostTelemetry** | pure counter over tool calls (evidence ops / mutations / verifications / failures) — the observation half that survived; never rejects, never resets a budget | `execution/commitment.py` |
| `capability_explain(tool)` | one capability end-to-end: registered / risk / operation class / policy verdict / output epistemics | `capability/coherence.py` |
| `house_consistency_check()` | mechanically walks registry + classification sets, reports C002 (phantom), C027 (overlapping evidence) | `capability/coherence.py` |
| `next_mechanical_options()` | a GateView projection — reports cost telemetry + mechanical doors (debt/blocked), never pressure to mutate | `capability/coherence.py` |
| `explain_rejection(call_id)` | explains a rejection from its durable record (the retired commitment verdict is gone) | `capability/coherence.py` |
| **Evidence Purchase** | repetition keyed on (subject, evidence class), not tool name — FLAGS a repurchase (never blocks), so the presentation can be compacted; resets on world mutation | `capability/purchase.py` |

Fixes landed against the report: **A1** (`verify_fabric.implicated` only for RELATED-level failures),
**A2** (blast_radius: memoized reference map + honest `second_order_dependents` field),
**A3** (escalation keyed on `direct_dependents`, not the `risk_shape` display label),
**A5** (`import_health.missing` → `possibly_missing`, CANDIDATE-strength, real binding collection),
**A6** (`test_for.direct` matches Name-only, labeled textual-not-executed),
**A7** (`signature` renders positional-only `/` and keyword-only `*` markers),
**A8** (`symbol_manifest` keyed `path::name` + collision marker),
**A9** (`imports_for` reports `as_imported_from` package root),
**B2** (`verify_fabric` → QUERY_TOOLS, `run_elevated` → MUTATION_TOOLS for telemetry),
**B3** (rejection result carries its own `call_id`),
**C1** (Evidence Purchase — cross-door repurchase flagged, never blocked),
**C2** (`world_delta` no longer consumes the ack).
**Retired:** the Progress Obligation / CommitmentGate (25-discovery-ops → force MUTATE|QUERY|VERIFY|STOP) —
replaced by CostTelemetry + non-coercive cost reduction. Lesson preserved in OBSERVATIONS.md.
**A4 done** (the shipped `route` is now the intent-classified router — DISCOVER/LOCATE/READ/MUTATE/VERIFY/DECLARE →
SYMBOL_SOURCE/FILE/REFERENCES/NEIGHBORHOOD/TEST/UNRESOLVED candidates with reason/tier/cost/epistemic/retrieval handle).
**Unified action identity done** (`tool.*` events carry `correlation_id`, which `_agency_call` also uses as
`action_id`, so one id traverses call → action → result in the durable log).
