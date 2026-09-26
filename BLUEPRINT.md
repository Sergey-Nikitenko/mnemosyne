# Nexus — Blueprint

> **Nexus is not an LLM wrapper. It is an observable execution system for AI agents.**

> **Phases 0–6 are the Nexus framework; Phase 7+ is the Mnemosyne build on top of
> it (identity + continuity + memory). Mnemosyne vendors this framework and adds
> its own slices.**

| Word | What it means here |
|---|---|
| Models | reasoning engines |
| Knowledge | information |
| Tools | capabilities |
| Orchestrator | execution |
| Router | resource selection |
| Policy | authority |
| Evaluator | verification |
| State | continuity |
| Events | history |
| Tracer | observability |
| Dashboard | transparency |
| Workers | scalability |
| MCP | interoperability |

## One principle

**Build the contracts before the implementations.** Every component is replaceable.
If you can swap Qdrant → pgvector, Ollama → OpenAI, Redis → another queue, or
FastAPI → another API layer *without rewriting the orchestrator*, it worked.

Components never touch another component's internal state. They communicate by
**interface** (`component → interface → component`) or by **event**
(`component → event → consumer`).

## A second principle

**New capability comes from composing frozen guarantees, not bypassing them.**
Phases 7–10 gave the earlier machinery richer semantics; later phases should
increasingly be compositions of already-proven authority domains (authority →
authoritative result) rather than new machinery that reaches around them.

## A third principle

**The component that proposes a state-changing interpretation must never be
sufficient authority for that interpretation to become fact.** Nexus applies this
shape three times — ActionRequest → Authority, LearningProposal →
LearningAuthority, DelegationRequest → independent authorities — and it carries
Aurora's refinement: a proposer must never be the only witness to its own
decisions.

## The project-wide law

With Phases 0–10 frozen, one law summarizes the invariants every phase enforces:

> **Proposal before authority. Authority before mutation. Report before
> observation. Freshness at the write boundary. History is never rewritten. New
> capability comes from composing frozen guarantees, not bypassing them.**

## The phase gate

A new phase begins only when a concrete requirement asks a question that no
composition of frozen guarantees can answer — and answering it requires a new
architectural invariant with a testable acceptance bar, not merely another
implementation of an existing contract. In one line:

> **If composition can answer the question, don't invent a phase.**

## Control plane vs execution plane (the distinction that matters)

The directory split (`control/` vs `execution/`) is secondary. The rule is:

- **Control plane** answers *"what SHOULD happen?"* — router, policy, evaluator,
  tool registry, model registry. These are **pure**: they take inputs and return
  a contract (`Decision` / `Verdict` / `Evaluation`). No state mutation, no tool
  calls, no network.
- **Execution plane** answers *"what DID happen?"* — orchestrator, workers, queue,
  tool execution, verification. These perform the actions and emit events.

Data flow:

    control  --Decision-->  execution
    execution --Event---->  observability/control

**The router proposes; the policy disposes.** The router never bypasses policy,
and control-plane components never execute — they only decide.

Side effects flow through one sanctioned interface — the `Executor` capability
(`execute_tool`, `run_model`). **Decision ≠ Action**: the control plane can
*request*; only the execution plane can *cause*.

**Phase 1 is frozen** as of this contract:

    User Request → Router → Decision → Policy → DENY / APPROVAL_REQUIRED / ALLOW → Execution

No new abstraction gets added unless a golden/conformance test demands it.

## The contracts (core/contracts.py)

`Task` → `Run` → `Step`, plus `State`, `Event`, `Decision`, `ToolCall`,
`ToolResult`, `RetrievalResult`, `ModelRequest`, `ModelResponse`,
`ApprovalRequest`, `Evaluation`, `Trace`.

The event envelope is deliberately boring:

```json
{
  "event_id": "evt_123",
  "event_type": "tool.call.completed",
  "timestamp": "...",
  "run_id": "run_456",
  "task_id": "task_789",
  "parent_event_id": "evt_122",
  "component": "mcp",
  "status": "success",
  "payload": {}
}
```

Boringness is the feature: every subsystem can consume it.

## Phases

### Phase 0 — Foundation
Repository, configuration, structured logging, event model, state model, test
harness. Layout: `core/{contracts,events,state,tasks,errors,ids}`.

**Done when:** a fake agent executes a multi-step task entirely against mocks,
producing a complete trace and recoverable state.

### Phase 1 — Control plane
Model registry → Router → Policy → Evaluator → Tool registry.

- **Policy is independent of the LLM.** The model can never talk itself into
  permission. `DENY / ALLOW / APPROVAL_REQUIRED`.
- The **router returns a decision object**, not just a model name:

```json
{
  "model": "ollama/qwen",
  "provider": "local",
  "reasons": ["task requires coding", "repository is private", "local model satisfies context"],
  "constraints": {"max_tokens": 4096, "timeout_ms": 30000}
}
```

### Phase 2 — Knowledge and Memory (two different things)
- **Knowledge:** ingestion → document store → embeddings → retrieval.
  `Retriever` abstraction = `VectorRetriever` + `KeywordRetriever` +
  `MetadataFilter` + `Reranker`. The orchestrator only calls
  `retriever.search(query, filters)`.
- **Memory:** episodic, semantic, user/task state. "Memory" is not a synonym
  for "vector database."

### Knowledge identity & version semantics

A chunk's **identity** is `(source, document, location)`. Two chunks with the
same identity but a different version are the *same knowledge at different
points in time* — the key to stale-embedding detection, updates, and
reproducible runs.

| source | document | location | version |
|---|---|---|---|
| `github` | repo/path | line range / section | commit SHA |
| `web` | canonical URL | heading / section | content hash or crawl timestamp |
| `filesystem` | relative path | page / line range | content hash |

**Version semantics.** *"current"* is scoped to `(source, document, location)`.
Different knowledge identities may legitimately have different current versions.
Staleness is **never** inferred globally — a store that collapses to a single
global version is an architectural regression, not an optimization.

### Store lifecycle semantics (AD-006)

Ingestion never deletes; it only ever supersedes. The rules, so a retrying
backend and a replacing store behave identically:

- **Replacement** — adding an existing identity with a *new* version makes that
  version **current**; the old version is retained for audit, never surfaced by
  search.
- **Idempotency** — re-adding the *same* `(identity, version)` is a no-op
  (deterministic stable id ⇒ replace, not duplicate).
- **Source disappearance** — a source absent from a later ingest is **not**
  auto-deleted; removal is an explicit operation, never an implicit side effect.
- **Stale deletion** — stale versions are retained; pruning is a separate,
  explicit concern, not something search or ingest does on its own.
- **Metadata-only change** — metadata is *not* identity. A metadata change
  without a version bump updates the record in place (same id, same version).
- **Concurrent writers** — last-write-wins for "current", per identity. No
  merge: ordering is the only tiebreak, and it is atomic per record.

### Phase 2.2 — ingestion as a boundary

Each stage is independently replaceable behind a contract:

    Source → DocumentLoader → Document → Parser → Chunker → Chunk → Embedder → Vector → KnowledgeStore

No stage knows the concrete provider. The reference implementation
(`knowledge/inmemory.py`) is stdlib-only; a production adapter (Chroma, OpenAI
embeddings) is just another implementation of the same contracts.

**Update semantics:** a chunk's identity is `(source, document, location)`. The
store retrieves only the **current** version and never surfaces stale ones — so
the model never receives v1 and v2 at once without knowing why.

**Phase 2 acceptance (frozen):** *Knowledge and memory are replaceable,
persistent capabilities exposed through stable contracts; provenance, identity,
versioning, filtering, and lifecycle semantics survive implementation changes
and process restarts.*

That claim is demonstrated, not asserted, by the progression that shipped:
reference implementation → second implementation → real source → persistent
store → restart → idempotent ingestion → memory → hybrid retrieval → Chroma
(implementation #3). Chroma passes the conformance suite **unchanged** — the
anticlimax is the point. The stdlib implementation (`knowledge/inmemory.py`) is
**permanent** — the reference implementation and the fast test fixture — never
disposable scaffolding.

### Phase 2.5 — memory as the sequence plane

Knowledge is the *content* plane (chunks of documents, identity + version);
memory is the *sequence* plane (what did we attempt, and how did it end). The
two are different things — "memory" is not a synonym for "vector database."

A finished run is projected — deterministically, no LLM summarization yet —
into an **Episode**:

    Run + events + evaluation  ->  Episode  ->  EpisodeStore  ->  retrieve

- **Episode** (`core/contracts.py`): episode_id / task_id / summary / outcome /
  relevant_entities / timestamp / provenance. Provenance keeps the run_id and
  the evaluation evidence, so a retrieved memory is self-describing.
- **Extraction is deterministic**: the same run always projects to the same
  summary / outcome / entities / provenance. episode_id and timestamp are the
  only fresh-per-record fields.
- **Outcome is derived from events** (run.completed → success, run.failed →
  failed, neither → unknown), never from the model's opinion of itself — absence
  of a terminal event is never promoted into a terminal verdict.
- **`memory/` is a top-level package** (not under `knowledge/`): it imports core
  only. The reference store ranks by keyword overlap — no embeddings yet; a
  semantic-recall adapter is a later implementation behind the same contract.

**Phase 2.5 acceptance:** *Memory is the sequence plane — deterministic and
replaceable without touching the orchestrator.*

### Phase 2.6 — hybrid retrieval (fusion, not replacement)

The orchestrator calls one method — `retriever.search(query, filters)` — and
gets one `RetrievalResult`, whether one source or many produced it. A hybrid
retriever fans the query out to its candidate sources (vector + keyword + ...),
merges their results **by chunk identity**, and reranks the union:

    query ──► vector source ──┐
    query ──► keyword source ─┼─► merge (identity) ─► rerank ─► RetrievalResult
    query ──► future source ──┘

- **Fusion is by identity**, not by index: a chunk both sources found surfaces
  *once* — the same `(source, document, location, version)` discipline as the
  store.
- **The reranker is an internal stage**, deliberately absent from the Retriever
  contract. Callers never touch it; swapping it changes only ranking, never the
  boundary. (The contract stays `search(query, filters) -> RetrievalResult`.)
- **The metadata filter is a named stage**, applied at the boundary even if a
  source forgot to.
- A real semantic source (Chroma + embeddings) drops in as one more entry in
  `sources` — nothing else moves. Hybrid retrieval is an *implementation
  detail*, not an interface.

**Phase 2.6 acceptance:** *Replacing a single retriever with a hybrid changes
nothing for the caller — same contract, same result shape, reranking hidden.*

### Phase 3 — Execution plane
The loop, with a REPLAN branch and hard budgets:

```
plan → retrieve → act → verify → PASS → done
                              └→ FAIL → replan
```

Budgets: `max_steps`, `max_retries`, `max_cost`, `max_execution_time`.

Task queue + workers are first-class from day one (even one worker), so
multi-worker in Phase 6 is not a redesign.

### Phase 3.1 — execution contracts + the reference executor

The `Executor` capability (already a Phase 0 contract) is the ONLY sanctioned
path to a side effect: `execute_tool(ToolCall) -> ToolResult` and
`run_model(ModelRequest) -> ModelResponse`. Phase 3.1 lands its reference
implementation — a deterministic, side-effect-free `FakeExecutor` — so the
*execution semantics* are provable before any real side effect (subprocess,
network, MCP, model SDK) exists.

**The invariant (AD-009).** Every externally observable execution step emits an
event BEFORE it is considered complete:

    ToolCall ──► tool.requested ──► execute ──► ToolResult ──► tool.completed
    ModelRequest ──► model.requested ──► run ──► ModelResponse ──► model.completed

`InstrumentedExecutor` (an `Executor` wrapper) enforces it: the `*.requested`
event fires first, unconditionally; the `*.completed` event fires only on
return. If the inner executor raises, the log shows `*.requested` with no
`*.completed` — the step was never complete. That is what makes "kill a worker
at any point and ask what Nexus knows happened" answerable: the event/state
foundation from Phase 0 finally gets exercised by real execution.

**Phase 3.1 acceptance:** *A tool/model step is observable in the event log
before it is complete — and a step that never returns is observably incomplete.*

### Phase 3.2 — subprocess tool execution (the first real Executor)

`ToolCall` names a LOGICAL tool ("formatter"); it never names an executable. The
execution-layer registry maps that name to a `SubprocessSpec` (argv template +
limits), so the model is never the authority that chooses an arbitrary
executable. The path is:

    CONTROL → ToolCall → Executor → tool registry → subprocess adapter → OS process

- **`SubprocessToolExecutor`** runs the spec as an argv list with `shell=False`
  — no shell interpretation, no `shell(command_from_model)`.
- **Failure semantics (AD-010):** an *expected* tool outcome (non-zero exit,
  timeout) is a `ToolResult(success=False, error=…)`; a *launch* failure
  (unknown tool, missing executable, malformed spec) raises. The former is data
  about the tool; the latter is a bug in the system.
- **Timeout is contract semantics** (declared on the spec): a non-exiting
  process is killed and reported, never allowed to hang Nexus.
- **Output is bounded** (spec-declared cap): no unlimited stdout/stderr into an
  event or trace; truncation is explicit.
- **Only contracts cross the boundary**: no Popen, pipe, or raw return code
  escapes — the caller gets a `ToolResult` of plain, serializable data.

`FakeExecutor` stays permanent as the reference and fast test fixture; the
Subprocess adapter and the future MCP adapter both produce the same `ToolResult`
behind the same `Executor` protocol.

**Phase 3.2 acceptance:** *A real subprocess executor satisfies the execution
semantics FakeExecutor established, without changing Executor, the orchestrator,
or the control plane.*

### Phase 3.3 — model execution (provider → Nexus, offline)

`ModelRequest -> Executor.run_model() -> ModelResponse`, with one real adapter
(`SubprocessModelExecutor`, a local process speaking JSON on stdin/stdout) and
`FakeExecutor` kept permanent as the reference. The model side proves the same
boundary pattern 3.2 did for tools.

- **`ModelResponse` is Nexus-shaped, not provider-shaped (AD-012).** The adapter
  translates the provider and drops the rest: `usage.prompt_tokens` becomes
  `tokens_in`, `completion_tokens` becomes `tokens_out`, and `finish_reason` /
  `system_fingerprint` / the `usage` dict never reach the caller. `ModelResponse`
  = content + model identity + usage + execution metadata, plus the
  `success`/`error` status pair it shares with `ToolResult`.
- **Failure taxonomy (AD-010's model twin):** a valid response is `success=True`;
  a provider rejection (error payload, non-zero exit, malformed response) is
  `ModelResponse(success=False, error=…)`; a launch/config failure (no model,
  missing executable) raises.
- **Request identity:** `ModelRequest` carries a `request_id`, so
  `model.requested` and `model.completed` unambiguously belong to the same
  run/step — even when one step makes several model calls.
- **Bounded output:** response content is capped (spec-declared), so a runaway
  provider response cannot become an enormous event/trace payload.
- **Deterministic test path:** the golden test drives a local fake model process
  — no live cloud API is ever required to make `py scripts/check.py` pass.

**Phase 3.3 acceptance:** *A real model adapter satisfies the semantics
FakeExecutor established, and the caller only ever sees a Nexus-shaped
ModelResponse — never a provider object.*

### Phase 3.4 — the orchestrator (the central nervous system)

The first composition of everything built so far:

                 ORCHESTRATOR
                      │
              ┌───────┼───────┐
              ▼       ▼       ▼
          Retriever  Executor  State
              │       │        │
              │       ▼        │
              │  Model / Tool  │
              └───────┴────────┘
                      │
                   Events

- **The orchestrator owns sequencing and correlation; capabilities own
  execution (AD-013).** It may call `retriever.search`, `executor.run_model`,
  `executor.execute_tool`, `policy.decide_tool`, `state`, `emit` — never
  `subprocess.Popen`, `open`, `requests.get`, a vector store, a model SDK, or an
  MCP client. `tests/conformance/test_orchestrator_purity.py` enforces this with
  an AST gate on `execution/orchestrator.py`.
- **The deterministic loop:** `Task → plan → retrieve → model → [tool] → model →
  verify → answer`. No replanning, retries, or real verification yet (that's
  3.5); 3.4 establishes *coordination semantics*.
- **A model SUGGESTS a tool; the orchestrator DECIDES.** `ModelResponse.tool_calls`
  is structured tool intent (Nexus `ToolCall` contracts, not provider-shaped).
  The orchestrator runs each suggestion through the Phase 1 policy —
  `DENY / APPROVAL_REQUIRED / ALLOW` — so `Model → Tool` never bypasses
  `Router → Policy → Execution`. The golden test proves a READ tool executes, a
  WRITE tool is approval-gated, and a DESTRUCTIVE tool is denied, all inside the
  loop.
- **`PolicyVerdict` moved to core** — it is a contract (like `Decision`/`Risk`)
  that crosses control → execution; the policy *engine* stays in control/. That
  is what keeps `execution/` importing core only, per the layer boundaries.
- **Recovery reconnects:** the run returns both its live `RunState` and its event
  log, and the golden test asserts `reconstruct(events) == live_state` — the
  Phase 0 crash-recovery guarantee, now exercised by a real loop.

**Phase 3.4 acceptance:** *Given deterministic capabilities, Nexus coordinates a
complete multi-step task, emits a causally ordered trace, preserves recoverable
state, and never performs capability work itself.*

### Phase 3.5 — verification + bounded replanning (the controlled feedback loop)

The loop gains a feedback branch:

    plan → retrieve → act → verify
                            ├─ PASS ──────────────► answer
                            └─ FAIL (replan_required)
                                    └─► run.replanned ─► act again (bounded)

- **The evaluator is not another executor (AD-016).** It observes and judges —
  `evaluator.evaluate(...) -> Evaluation` — and the orchestrator interprets the
  verdict. The control-plane purity gate now also bans `core.state`, so the
  evaluator can neither execute a capability nor mutate execution state.
- **`Evaluation` carries a structured verdict:** `passed` / `reason` /
  `replan_required` (plus the Phase 1 `checks` evidence). `reason`/`evidence`
  are data, never an evaluator-specific object.
- **Verification is explicit (AD-014):** `tool succeeded != task succeeded`. A
  tool can return success while the attempt still fails the task — the two
  propositions are decoupled, and the golden test pins it (attempt 1's tool
  succeeds, the evaluation still FAILs).
- **Replanning is bounded and event-sourced (AD-015):** `max_replans` is part of
  the plan/run state; exhaustion is a terminal `FAILED` outcome, never an
  infinite loop. Every attempt's `evaluation.completed` and every
  `run.replanned` is an event, and `RunState.reconstruct` reproduces the exact
  attempt/replan trail.
- **Policy stays authoritative across replans:** each newly proposed tool
  re-enters the same `DENY / APPROVAL_REQUIRED / ALLOW` gate; replanning changes
  the plan, never the authority (a separate golden test proves a DESTRUCTIVE
  tool is denied on every attempt).

**Phase 3.5 acceptance:** *One deterministic task shows attempt-1 FAIL →
replan → attempt-2 PASS → completed, while no capability bypasses the Executor,
every proposed tool still passes policy, the replan count is bounded, all
attempts are observable, and state reconstructed from events equals live state.*

### Phase 3.6 — durable task execution (queue + worker)

The execution plane gains a durable boundary between "accepted" and "run":

    Task → Queue → Worker → Orchestrator → Events → State

- **Ownership is a durable event (AD-017).** `TaskQueue.claim` emits
  `task.claimed` (with `worker_id`); `enqueue` emits `task.queued`; the worker's
  ack emits `task.completed` / `task.failed`. A worker never owns execution
  solely in memory — a crash leaves "last durable event = X", never "the worker
  died". `TaskState` is the projection of those `task.*` events.
- **At-least-once, never exactly-once (AD-018).** A claimed-but-uncompleted task
  stays `CLAIMED` and is recoverable; re-running may execute a tool twice. That
  is explicit and observable — the same idempotency discipline the knowledge
  store already has (stable chunk ids), applied here to delivery semantics.
- **Queue / worker / orchestrator stay separate:** the queue does enqueue/claim/
  ack (SQLite, atomic single-`UPDATE` claim); the worker composes queue +
  orchestrator and knows nothing about retrieval or providers (AST-gated); the
  orchestrator is unchanged and knows nothing about how tasks are queued.
- **A `DurableEventBus`** persists every event to a SQLite log, so `RunState`
  and `TaskState` reconstruct from a fresh connection — the whole event/state
  system is now crash-surviving, not just in-memory.

**Phase 3.6 acceptance:** *the queue accepts a Task, the worker claims it, runs
the unchanged Orchestrator, ownership/completion/failure are durable and
observable, state reconstructs from the log, the worker has no provider
knowledge, and duplicate-delivery semantics are explicit.*

### Phase 3.7 — crash recovery (failure injection, not feature expansion)

3.6 built the machinery; 3.7 proves it under actual process death.

    enqueue -> w1 claims -> orchestrator runs -> KILL PROCESS
    -> fresh process/connection -> reconstruct -> recover abandoned CLAIMED task
    -> w2 claims -> orchestrator re-runs -> task.completed

- **Recovery is a lease rule on durable evidence (AD-019).** A claim records
  `claimed_at`; `CLAIMED + lease expired → recoverable`. `RecoveryManager`
  requeues such tasks based purely on status + `claimed_at` — never on knowing
  which worker died. The clock is injectable and deliberately simple.
- **Recovery belongs to queue infrastructure, never the orchestrator (AD-019).**
  The orchestrator has no idea whether it was started normally or because a
  previous worker died — it just runs the task. Recovery is an execution
  concern, not an agent-intelligence concern.
- **At-least-once is demonstrated, not asserted.** Case B kills the worker
  *after* the tool's side effect, before the completion event: the tool
  executes again on re-run (side effect count 2), exactly as AD-018 documents.
- **Reconstruction survives death:** `TaskState.reconstruct(all_events)` and
  `RunState.reconstruct(run, w2_events)` reproduce the recovered state from the
  durable log, closing the loop Phase 0 → 3.5 → 3.6 → 3.7.

**Phase 3.7 acceptance:** *a task, under real worker death, is never silently
lost — durable evidence identifies it, the queue recovers it, a fresh worker
finishes it, and the event log reconstructs the recovered state.*

### Phase 3.8 — MCP as another tool transport (implementation #3)

    ToolCall -> Executor -> (Subprocess adapter | MCP adapter) -> ToolResult

- **The adapter owns MCP's vocabulary (AD-020).** `integrations/mcp.py` speaks
  JSON-RPC 2.0 over stdio (`initialize` / `tools/list` / `tools/call`) and
  translates: `tools/list` → `ToolDefinition` (a Nexus contract), `tools/call`
  result → `ToolResult`. Nothing MCP-shaped crosses the boundary — the
  provider-specific envelope (`isError`, `content` blocks, `_meta`,
  `structuredContent`) is dropped.
- **MCP failures follow AD-010:** an MCP `isError` result is an expected failure
  (`ToolResult(success=False, …)`); transport/config/launch failure raises. No
  third category is invented.
- **Instrumentation is universal:** the MCP adapter runs through the same
  `InstrumentedExecutor`, so `tool.requested → MCP call → tool.completed` holds,
  and a transport exception leaves an observably incomplete step.
- **Policy stays above MCP:** the path is model → ToolCall → policy → Executor →
  MCP, never model → MCP client → tool. The golden test proves a DENIED tool
  never reaches the MCP server.
- **No MCP imports above the adapter:** `mcp` is now in the provider-leakage
  gate, confined to `integrations/`.
- **Progression complete:** FakeExecutor → SubprocessToolExecutor →
  McpToolExecutor — three implementations, one contract. The end-to-end golden
  task runs the unchanged Orchestrator with the MCP executor, then with the fake
  executor: the only thing that changes is dependency injection.

**Phase 3.8 acceptance:** *an MCP-backed tool executes inside the unchanged
orchestrator; discovery yields Nexus contracts, failures follow AD-010, policy
gates every call, and nothing provider-shaped leaks above the adapter.*

### Phase 4 — Surface
API (REST + WebSocket), dashboard (run view, trace tree, approval queue, *Why*
panel), CLI, and the Operator Console. The dashboard is driven directly off the
decision objects; the console is a projection-only operator view over the same
runtime.

Sequencing: 4.1 runtime composition → 4.2 REST `/ask` + status → 4.3 trace
projection → 4.4 WebSocket event stream → 4.5 approval lifecycle → 4.6 dashboard
→ 4.7 CLI → 4.8 Operator Console. (Not eight commits — these are the conceptual
boundaries.)

### Phase 4.1 — runtime composition (the composition root)

One root wires the real system together:

    NexusRuntime
        ├── Control (router, policy, evaluator)
        ├── Execution (worker, queue, executor)
        ├── Knowledge (retriever, store)
        └── Durable events -> State

- **Applications compose Nexus; components never discover each other through
  global state (AD-021).** `NexusRuntime` takes everything as constructor
  arguments and is the ONLY place that wires orchestrator + worker + queue + bus.
- **The application surface is uniform:** `ask` (async, returns a task id),
  `run_one`, `task`, `events` — identical whether the injected components are
  fakes (FakeExecutor, reference retriever, SQLite) or real (MCP + model adapter,
  Chroma, durable events, real workers). The golden test runs both and asserts
  only the components differ.
- **The surface is a leaf:** nothing below `apps/` may import `apps/` —
  execution never depends on the surface. Enforced by
  `tests/conformance/test_surface_boundary.py`.

**Phase 4.1 acceptance:** *one runtime composes fake or real components behind
the same application surface, with no global-state discovery, and the execution
plane remains surface-agnostic.*

### Phase 4.2 — the thin HTTP adapter (REST over the runtime)

    POST /ask -> NexusRuntime.ask() -> queue -> {"task_id", "status": "queued"}
    GET /tasks/{id} -> NexusRuntime.task() -> TaskState projection
    GET /traces/{run_id} -> NexusRuntime.events() -> event history

- **FastAPI/Pydantic stop at apps/ (AD-022).** The HTTP layer is an edge adapter:
  it delegates to `NexusRuntime` and serializes Nexus contracts to JSON. The
  Pydantic request model (`AskRequest`) never propagates into Nexus, and
  `TaskState` has no Pydantic dependency. `tests/conformance/test_http_boundary.py`
  makes any HTTP/framework import below `apps/` a failing build.
- **`/ask` is asynchronous:** it returns `202` + `task_id` + `status=queued`
  without running the orchestrator inline; a worker drains the queue separately.
- **Task ID ≠ Run ID:** `/tasks/{id}` is the execution lifecycle; `/traces/{run_id}`
  is one run/attempt's event history (matters once recovery + replanning show up).
- **Correlation is preserved at the edge:** `X-Task-ID` on `/ask`, and every
  trace event carries `event_id` / `run_id` / `task_id` / `parent_event_id`.
- **Deterministic semantics:** unknown task/trace → 404, malformed request → 422.
  `GET /traces` is observational (never mutates the event log). A restart leaves
  the API reporting the same durable state (the endpoint keeps no in-memory dict).

**Phase 4.2 acceptance:** *a thin HTTP adapter creates a durable queued task
asynchronously, reports durable task state and event history, survives a runtime
restart, has explicit missing-resource semantics, and leaks no HTTP type below
the surface.*

### Phase 4.3 — trace projection (one interpretation of a run)

    Durable events -> TraceProjector -> core.Trace -> HTTP / WebSocket / CLI / dashboard

- **The trace is a read-side projection, not a second source of truth (AD-023).**
  `observability/trace.py` derives the existing `core.Trace` from events —
  deterministically, without modifying the events, and without a `DashboardTrace`.
- **Decisions are observable:** the orchestrator now emits a `policy.decision`
  event (`verdict` / `risk` / `executed`) for every tool proposal, so the
  projector distinguishes *proposed → evaluated → allowed → executed → completed*
  from "tool happened" — the future Why panel's raw material.
- **Causality + replans survive:** every node keeps `event_id` +
  `parent_event_id`; `evaluation.completed` and `run.replanned` carry an
  `attempt`, so a FAIL→REPLAN→PASS history projects as two distinct attempts with
  no dashboard-specific logic.
- **Incomplete and recovery transitions stay visible:** `tool.requested` with no
  `tool.completed` projects as `interrupted` (never a manufactured success);
  `task.claimed → task.requeued → task.claimed → task.completed` projects the
  worker-recovery story directly from the event semantics.
- **The projector is pure** — it imports only `core.contracts` + `core.events`
  (enforced by `tests/conformance/test_trace_projection_purity.py`), so
  observability stays genuinely downstream of execution.

**Phase 4.3 acceptance:** *the event stream remains the source of truth; the
projector reproduces the same trace deterministically, preserves causality and
decisions, shows replans as attempts, leaves incomplete operations incomplete,
makes recovery visible, and has no execution/provider/HTTP dependency.*

### Phase 4.4 — WebSocket (subscribe to events, never the orchestrator)

    DurableEventBus -> event subscriber -> TraceProjector -> WebSocket adapter -> browser

- **The WebSocket subscribes to the event bus (AD-024).** It never polls the
  orchestrator and never receives callbacks from it; the orchestrator has no
  idea a browser exists.
- **Replay + live tail:** on connect, `/ws/runs/{run_id}` replays the run's
  durable history (projected), then tails live events. A completed run replays
  and closes cleanly; a refresh never loses the beginning of the run.
- **Filter at the edge:** the bus stays generic; the subscriber drops events
  whose `run_id` doesn't match. The wire format carries only Nexus-level
  keys — no MCP/OpenAI/Chroma vocabulary.
- **Backpressure is explicit:** a bounded per-client queue; a client that can't
  keep up is disconnected (disconnect-on-overflow) rather than stalling the bus.
- **Observability is genuinely downstream:** removing every WebSocket client
  changes neither the execution result nor the durable event log (asserted in
  the golden test).

**Phase 4.4 acceptance:** *a client connects, receives the durable replay then
live events, receives only its selected run, gets a deterministic replay for a
completed run, and cannot — by disconnecting or stalling — affect execution or
the event log; WebSocket/framework types never cross apps/.*

### Phase 4.5 — the approval lifecycle (durable authorization)

    model proposes WRITE -> policy APPROVAL_REQUIRED -> approval.required
    -> task WAITING_APPROVAL (durable) -> POST /approvals/{id}/approve
    -> approval.granted -> task QUEUED -> worker re-runs -> policy AGAIN -> tool executes

- **Approval is a durable task-state transition, not an HTTP callback (AD-025).**
  A WRITE proposal pauses the run: the worker records `task.waiting`, the task
  sits in `WAITING_APPROVAL`, and the worker/runtime/browser can all disappear
  without losing it.
- **Bound to a specific proposal:** `ApprovalRequest` carries approval_id,
  task_id, run_id, tool_name, risk. Approving one action never authorizes
  another (the golden test proves approving A does not authorize B).
- **Single-use:** `pending -> approved/denied -> consumed`; a consumed approval
  can never be replayed against a later call.
- **Policy is re-checked on resume:** a granted approval is evidence a human
  approved *that* proposal — it does not bypass the policy engine. If the
  current policy DENIES the tool, execution is blocked (approval unconsumed).
- **The surface commands, never executes:** `POST /approvals/{id}/approve`
  marks the approval and requeues the task; it never touches
  `Executor.execute_tool`. The audit trail
  (`policy.decision → approval.required → approval.granted → approval.consumed →
  tool.requested → tool.completed`) is fully reconstructible from events.

**Phase 4.5 acceptance:** *an approval is durable, single-use, task-bound, and
policy-governed; a worker can die, the runtime restart, and the browser
disconnect while a task waits — and the resumed execution still runs the tool
only if both a granted approval AND the current policy allow it.*

### Phase 4.6 — the dashboard (disposable presentation)

    Dashboard -> HTTP + WebSocket -> apps/ surface -> trace projection + approval API -> durable events

- **The dashboard is a projection consumer, not a Nexus component (AD-026).**
  `apps/dashboard.py` maps the projected `core.Trace` into a view model — status,
  milestones, attempts, decisions, recovery, interruptions — with no business
  logic. It renders state; it never reconstructs authority.
- **The Why panel consumes the decision, never recomputes it:** every
  `policy.decision` event now carries its own `reason`, so the UI shows
  `risk / verdict / executed / reason` verbatim — no `if risk == ...` on the UI.
- **Replan, recovery, and interruption are all visible** from the event semantics
  (attempt numbers, `task_claimed → task_requeued`, `interrupted` tools).
- **Event identity is an advanced detail:** every decision node exposes
  `event_id` / `parent_event_id` / `run_id` / `task_id` / `timestamp` for the
  details drawer — "click the event, here is the exact durable event."
- **Disposable by construction:** the dashboard imports only the projected
  contract; deleting it leaves the API, CLI, WebSocket, runtime, workers,
  events, state, and recovery intact (`test_surface_boundary.py` is the guardrail).

**Phase 4.6 acceptance:** *the dashboard is a disposable presentation layer over
Nexus's durable event and state projections — it can observe, display, and
request human authorization, but cannot execute capabilities or become an
independent source of truth.*

### Phase 4.7 — the CLI (another thin surface)

    nexus ask | task | trace | approve | deny  ->  NexusRuntime

- **The CLI is another surface adapter** — `ask` is asynchronous (returns a
  queued task id, exactly like HTTP), `task`/`trace` render the SAME projected
  view model the dashboard uses, and `approve`/`deny` call the SAME
  `runtime.approve`/`runtime.deny` the HTTP layer uses. No orchestration, no
  policy, no queue manipulation, no provider imports.

**Phase 4.7 acceptance:** *REST, WebSocket, the dashboard, and the CLI are four
disposable views of one execution model — they observe and command the same
durable runtime rather than implementing parallel agent behavior.*

### Phase 4.8 — the Operator Console (projection-only operator surface)

    Console -> apps/console.py (create_app + read-only projections)
             -> identities / NCS / approvals / events / action lifecycle

- **The console is a projection, never a new authority.** It composes the SAME
  `NexusRuntime` and `create_app(runtime)` the REST/WS/CLI/dashboard share, and
  adds read-only operator projections: `GET /api/identities` (User/Agent/Model),
  `GET /api/ncs` (the `ContinuityProjector` as-of now), `GET /api/approvals` (the
  `approval.*` lifecycle), `GET /api/events` (the raw log), and
  `GET /api/actions/{action_id}` (`action_attempted` + `reconstruct_action`). It
  adds no mutation path and no phase — composition answers the question, so the
  phase gate is respected rather than bypassed with a new phase.
- **The hard rule is explicit.** The frontend carries, verbatim: *"The Operator
  Console is a projection of authoritative Mnemosyne state. It may request
  operations and display evidence; it never defines truth, authority, identity, or
  continuity."* The only write path is the authorized one:
  `UI -> API -> authorized path -> transition -> event -> UI updates`.
- **An interrupted action is never a failure.** `action.requested` with no
  terminal event projects as `attempted=true, terminal=false, outcome=null`; the
  UI renders "NO TERMINAL EVENT / unknown / attempted", never a red FAILED badge
  (the AD-050 projection, not a UI guess).
- **The event stream is the center:** `WS /ws/events` replays the durable log then
  tails live events, using the SAME serialization as `/api/events` and
  `/traces/{run_id}` — the console holds no state of its own; a refresh or restart
  reconstructs the identical view from authoritative state.
- **Disposable by construction:** delete `apps/console.py` + `apps/static/` and
  the runtime, API, WebSocket, workers, events, state, and memory all keep working.
  `tests/conformance/test_console_surface.py` asserts the console GETs never mutate
  the event log or the memory store.

**Phase 4.8 acceptance:** *the console observes and commands the same durable
runtime as every other surface; it projects identities, NCS, approvals, events,
and action lifecycles read-only; an interrupted action renders attempted/unknown;
and REST, CLI, and raw state agree on the same task status.*

### Phase 4 complete — one execution model, many disposable surfaces

Nexus has a single execution model (contracts → policy → executor → durable
events → state) and multiple disposable surfaces over it. Every surface consumes
the same `NexusRuntime`, the same contracts, and the same event projections.

**Next: a whole-system architectural audit** (before Phase 5 hardening), in five
passes — dependency graph, event taxonomy, state/event transition graphs,
concurrency semantics, and public-contract compatibility.

### Phase 5 — Hardening

**Headline guarantee:** *Nexus guarantees atomic authorization and ownership
transitions under concurrent workers. A capability requiring exclusive
authorization can execute at most once for a given approved proposal.*

### Phase 5.1 — atomic approval consumption

`find_approved()` + `consume()` had a TOCTOU race (two workers could both read
APPROVED, both consume, both execute). Replaced with one atomic transition:
`consume_approved(approval_id)` is a single conditional
`UPDATE … SET status='consumed' WHERE approval_id=? AND status='approved'`, and
the caller checks the affected-row count. Exactly one worker gets 1 (executes);
every other gets 0 and must not execute. Proven by
`tests/golden/test_phase5_concurrency.py`, which races two workers (separate
connections) and asserts exactly one execution.

### Phase 5.2 — SQLite concurrency policy

Finding #2: a single connection written from two threads
(`check_same_thread=False` on one shared connection) is unsafe. Nexus now gives
each worker thread its OWN connection — thread-local, owned by the store component
(`execution/sqlite.py`), never by the worker thread. SQLite is configured
deliberately: `busy_timeout` (a writer waits, it does not fail),
`journal_mode=WAL` (one writer + many readers), `synchronous=NORMAL`,
`foreign_keys=ON`. `check_same_thread=False` remains only so `close()` can close
connections opened in now-exited worker threads; no connection is ever shared.
Every exclusive transition stays a single conditional statement — the database
arbitrates, never Python check-then-act. Proven by
`tests/golden/test_phase5_connections.py`.

### Phase 5.3 — task ownership atomicity

Approval consumption being atomic (5.1) does NOT prove task ownership is atomic,
so 5.3 proves the two ownership transitions separately:

- **claim race** — two workers race `claim` (`UPDATE … WHERE status = QUEUED`) on
  one task: exactly one CLAIMED, the loser gets an explicit `None`.
- **recovery/claim race** — a recoverer (`recover_abandoned`) and a worker
  (`claim`) race over an abandoned task: exactly one owner, never two, never a
  silent overwrite.

Proven by `tests/golden/test_phase5_ownership.py`.

### Phase 5.4 — per-attempt tool identity

`tool.requested` and `tool.completed` carried no per-call id, so two overlapping
tool calls in one run were indistinguishable in the trace. `ToolCall` carries a
Nexus-owned `call_id`, threaded through `tool.requested` / `tool.completed` /
`policy.decision`, and the trace projector pairs requested↔completed by call_id.

The identity is per **attempt**, not per proposal: the orchestrator re-mints
`call.call_id` at execution time and deliberately ignores whatever id the
provider/model attached (AD-012's twin for tool calls). Under at-least-once
recovery a re-executed call is a NEW call_id — two physical side effects never
collapse into one in the event log. Proven by
`tests/golden/test_phase5_correlation.py`: the same tool twice in one step yields
two distinct call_ids, and an interrupted call re-run after recovery gets a fresh
identity.

### Phase 5.5 — event-sourced approvals

Approval status lived only in the SQLite table. `ApprovalState.reconstruct`
(approval_id, events) now reproduces the lifecycle (pending → approved/denied →
consumed) from events alone — the same "state is a projection of events"
discipline already held by `RunState` and `TaskState`.

### Phase 5.6 — terminal step semantics

A step that raised an exception left no `step.failed` event. The orchestrator now
catches, emits `step.failed`, marks the step FAILED, then re-raises. Process death
still leaves no terminal event — that is exactly how an interruption is detected.

### Phase 5.7 — atomic recovery

`recover_abandoned` previously SELECTed expired tasks then UPDATEed each, so two
recoverers could both requeue the same task (double `task.requeued`). It is now
one atomic conditional `UPDATE … WHERE status='claimed' AND claimed_at < ?
RETURNING task_id`: exactly one recoverer gets the row.

### Phase 5.8 — event taxonomy cleanup

Removed the dead `TASK_CREATED` type; reserved `MODEL_SELECTED` and
`MODEL_FALLBACK` for Phase 6 model selection; `STEP_FAILED` is now live (5.6).
The taxonomy is back to "every type is either emitted or explicitly reserved."

Findings #3–#7 are proven together by `tests/golden/test_phase5_hardening.py`.

### Phase 5.9 — stale-owner isolation (A2-1)

Audit #2 flagged the last concurrency hole: terminal transitions identified the
task but did not prove the caller still owned the claim. Now `claim` mints a
`claim_generation`, and `complete`/`fail` are conditional on the exact
(worker_id, generation) the worker acquired. A stale worker whose lease expired
and whose task was re-claimed gets `False` back — harmless even if it stays
alive. Proven by `tests/golden/test_phase5_stale_owner.py` (stale completion AND
stale failure).

**Phase 5 is frozen** — 5.1–5.9, full-stack concurrency, two audits, 46 tests.

### Phase 5 (continued) — Hardening
- **Secrets:** never enter prompts, traces, or model-visible logs.
- **Tool execution:** timeouts, resource limits, filesystem boundaries,
  network restrictions, permission checks (a sandbox for shell/code execution).
- **Idempotency:** `idempotency_key` on every externally mutating operation,
  so a crash-and-retry can't create two PRs.

### Phase 6.1 — run identity + deterministic replay (reproducibility as a contract)

A run is reproducible only when Nexus persisted the INPUTS that define it (the
`RunManifest`) alongside the OUTPUTS (the event log). The manifest answers *"what
configuration/snapshots defined this run?"*; the events answer *"what actually
happened?"*. Events alone do NOT imply reproducibility — that's the mistake this
slice prevents.

- **`RunManifest`** (`core/contracts.py`) — task identity, knowledge snapshot,
  policy identity/version, model identity/config, tool-registry identity, router
  config (reserved), and replan limits. All Nexus vocabulary, never a provider
  object or SDK config (the same AD-012 discipline, applied to configuration).
- **Captured before execution (AD-029):** `run.manifest` is emitted before
  `run.started`, so the defining configuration is recorded before the first
  capability decision. Replaying "today's configuration" silently is not replay.
- **Semantic fingerprint, not byte-identity:** `observability/replay.py` reduces a
  run's events to a canonical projection — dropping event ids, timestamps,
  worker/run/claim/call ids — and hashes it. **Reproducible ≠ byte-identical.**
- **Derived replay status:** `ReplayReport` is REPRODUCIBLE (inputs match AND
  semantic traces match), REPRODUCIBLE_WITH_DIFFERENCES (an input changed), or
  NON_REPRODUCIBLE (a required input is missing) — never a subjective guess.
- **Deliberate nondeterminism is visible:** a difference is reported as *which
  input changed* (e.g. "knowledge: v1 → v2") plus the first divergent event, not
  "the agent behaved differently."
- **Honesty:** the reference/fake path proves true reproducibility; production
  providers later declare their nondeterministic characteristics explicitly —
  exactly how at-least-once delivery is honest rather than promised away.

Proven by `tests/golden/test_phase6_replay.py` (same inputs → MATCH; knowledge
change → named difference at `retrieval.completed`).

### Phase 6.2 — persisted-schema versioning

The on-disk format now carries an explicit version, so "we have old data" becomes
a deterministic answer instead of a guess. `execution/schema.py` defines
`SCHEMA_VERSION = 1` — a number DISTINCT from contract versions and component
identities (those answer different questions) — and stores it in SQLite's
`PRAGMA user_version` (a header integer, no extra table).

- **Compatibility rule:** read == `SCHEMA_VERSION`; migrate version 0 (the
  pre-versioning legacy format) to current at open; reject > `SCHEMA_VERSION` (a
  newer build's data is never silently misread) with a deterministic error.
- **Migration is a store hook:** `SqliteStore._migrate` reconciles an older schema
  before any read/write. The one real migration today adds the `claim_generation`
  column to a pre-5.9 tasks table, idempotently.
- **Non-destructive by construction:** migration is column-additive and header-only
  — data rows are never rewritten, so event payloads (and 6.1 fingerprints /
  manifests) are semantically unchanged.

Proven by `tests/golden/test_phase6_schema.py` (migrates an actual v0 fixture,
rejects a future version, and confirms the schema version never enters a manifest).

### Phase 6.3 — durable run identity / fingerprints

The 6.1 reproducibility artifacts are now first-class persisted records — without
introducing another replay system. `RunRecordStore` (`execution/run_records.py`)
persists `(run_id, manifest, fingerprint, status)`; the orchestrator writes the
manifest at start and the fingerprint at terminal.

- **manifest = conditions; fingerprint = semantic outcome; run_id = identity.**
  Neither the manifest nor the fingerprint is the run's identity, and neither is
  written into every event — the fingerprint is derived evidence about execution.
- **Terminal-only fingerprint:** written only at `run.completed` / `run.failed`; a
  partial/crashed run keeps a manifest but a NULL fingerprint (never fabricated).
- **Retrievable by run_id** from a fresh process — it survives restart and schema
  initialization.
- **Nexus vocabulary only:** the persisted manifest is strings/ints, never a
  provider object or SDK payload.
- `/traces` is unchanged — this is run identity, not a second trace model.

Proven by `tests/golden/test_phase6_records.py` (manifest+fingerprint persisted,
fresh-process retrieval, identical-input match, changed-input diff, crash → no
fabricated fingerprint).

### Phase 6.4 — multi-worker arbitration

Phase 5 proved task-level ownership; 6.4 answers the NEXT question: when workers
run concurrently, which run is AUTHORITATIVE for a task, and is that decision
durable? Deliberately NOT distributed consensus / leader election / a scheduler —
SQLite is still the boundary; this is arbitration semantics, not a distributed
system.

- **Terminal run records are guarded by the claim generation (AD-032):** the
  orchestrator records the manifest at start; the WORKER records the terminal
  fingerprint only after its claim generation is confirmed current
  (`queue.complete`/`fail` returned True). A stale worker's run keeps its manifest
  but a NULL fingerprint — it can never publish a terminal result.
- **Authoritative run is derived, not stored:** a task's authoritative run is the
  one with a terminal fingerprint; the guard guarantees at most one per task.
- **run_id and claim_generation stay separate identities** (execution attempt vs
  task ownership); the guard records their relationship without merging them.
- **Run records remain partitioned by run_id** — concurrent workers never
  cross-contaminate tasks.

Proven by `tests/golden/test_phase6_arbitration.py` (a stale worker mid-run is
recovered; only the current generation publishes a terminal run record).

### Phase 6.5 — Nexus as an MCP server (the ingress twin of 3.8)

3.8 proved Nexus can USE an MCP server as a tool provider; 6.5 proves the
inverse: Nexus can BE an MCP server for an external MCP client, without the
orchestrator (or any core/control/execution/knowledge component) seeing an MCP
object. `apps/mcp_server.py` owns MCP's vocabulary (JSON-RPC 2.0 over stdio,
stdlib only — no `mcp` SDK, enforced by the provider-leakage gate).

- **Discovery:** `tools/list` exposes Nexus capabilities as `ToolDefinition`
  contracts (`nexus.ask`, `nexus.task`); only Nexus contracts cross.
- **Invocation:** `tools/call` becomes a Nexus-level request; the result is
  re-wrapped in MCP's envelope only at the adapter. The durable task lifecycle is
  not bypassed — `nexus.ask` returns a queued task_id.
- **Policy stays authoritative:** the adapter only calls `runtime.ask`/`task`
  (the same surface as HTTP/CLI), so it cannot turn a denied capability into an
  allowed one, and approval-required operations keep their lifecycle.
- **Correlation:** the JSON-RPC request id is transport-only; Nexus's task_id /
  run_id / call_id are independent and returned in the result.
- **Failure taxonomy:** malformed request → protocol error; a valid Nexus
  operation that fails → an `isError` RESULT; transport failure → the loop exits.
  No second taxonomy.
- **Observability:** the task flows through the same durable events and trace
  projection as an internal invocation — no MCP-specific model.

Proven by `tests/conformance/test_nexus_mcp_boundary.py`.

### Phase 6.6 — multi-agent composition

The final slice: multiple agent ROLES (supervisor → research/coding/review)
collaborating through the SAME contracts, policy, durable execution, recovery, and
observability — an agent is a capability composition, not a new execution
substrate. `execution/agent.py` is a stable logical identity + a runtime; it has
no private orchestration/event/state model.

- **Explicit agent identity (AD-034):** `agent` on the Task and RunManifest — a
  role identity, never conflated with worker_id / task_id / run_id.
- **Delegation is durable + observable:** `parent_run_id` on the child's manifest
  links it to the delegating run, so the trace answers "which agent did this, and
  who delegated it."
- **Policy is not bypassed:** a child agent uses the same policy boundary; a WRITE
  child still requires approval.
- **Results are Nexus contracts:** the supervisor consumes child Outcomes — no
  provider/model object crosses.
- **Failure is contained:** a failed child replans and produces its own observable
  outcome; the supervisor's outcome is not corrupted.
- **Compositional reproducibility:** each child run keeps its own run_id and
  fingerprint; the child's fingerprint is a child outcome, never folded into the
  parent's identity.
- **No parallel execution model, no second agent-state database:** everything flows
  through the existing queue/worker/orchestrator/events/run-records.

Proven by `tests/golden/test_phase6_multiagent.py`.

**Phase 6 is complete:** 5.x ownership → 6.1 reproduction → 6.2 interpretation →
6.3 identity → 6.4 arbitration → 6.5 interoperability → 6.6 composition.

### Phase 7 — identity & continuity (Mnemosyne)

Phase 7 builds the persistent identity/continuity layer on top of the execution
substrate: **Nexus owns identity, memory, knowledge, continuity, and state; models
are pluggable reasoning backends.** (The build name for this plane is **Mnemosyne**;
Nexus remains the framework.)

### Phase 7.1 — identity contracts

Freeze the three identities before continuity, context adaptation, or routing can
invent their own (AD-035):

- **`UserIdentity`** — who is interacting (user_id; no auth/provider fields).
- **`AgentIdentity`** — which agent ROLE is acting (agent_id/role/version).
- **`ModelIdentity`** — which logical model configuration participated
  (model_id/family/version; never the API key, endpoint, SDK object, or client).

Each is a serializable, versionable core contract with a canonical `key`
(`user/alice`, `agent/researcher@1`, `model/fake-deterministic@1`), carried in the
`RunManifest`. The invariant: **a model swap changes only `ModelIdentity` — user
and agent are untouched.** `ModelIdentity` equality is on its logical fields only,
so the concrete SDK object behind it is irrelevant.

Proven by `tests/golden/test_phase7_identity.py` (persistence, fresh-process
reconstruction, model-swap invariant, Nexus-only serialization, and 6.1/6.3
fingerprint behavior intact).

### Phase 7.2 — memory taxonomy contracts

Durable, versioned, provenance-aware memory objects that will become inputs to the
future ContinuityProjector. Three kinds, extending (not replacing) the episodic
memory Phase 2.5 already established:

- **`Procedure`** — "how do we do this?" (a durable, versioned workflow).
- **`SemanticMemory`** — "what stable fact do we know?" (subject/predicate/object).
- **`Preference`** — "what the user/agent prefers."

**Event-sourced invariant (AD-036):** memory objects are PROJECTIONS of
authoritative `memory.created` / `memory.updated` events; the model cannot
directly rewrite authoritative memory. The only write path is `record()`, which
emits an event and re-projects — never `LLM → UPDATE table`. Versioning is
explicit (v1 is never silently mutated; a new event yields v2), and every record
carries provenance back to its source event / run / task / session.

Proven by `tests/golden/test_phase7_memory.py` (the full invariant in one test:
project → mutate-directly → authoritative state unchanged → legitimate update →
new version → provenance + version history).

### Phase 7.3 — ContinuityProjector / NCS

Reconstruct the world as of a point in time — the first genuine proof of
"new session ≠ new identity." `NexusContinuityState` is a read-side PROJECTION of
authoritative state (identity + event-sourced memory), never a stored record:

    authoritative state + explicit as_of -> ContinuityProjector -> NCS

- **No new store, no model, no mutation** — the projector only reads. It is pure
  and deterministic, so the same `as_of` yields the same NCS.
- **`as_of` reconstruction** — memory is projected to its version *in force at the
  given time*, not merely "latest". `Procedure v1 @ t1` vs `v2 @ t2` are
  distinguishable, which is what later makes "what was in force when this ran"
  answerable.
- **Model-neutral** — the NCS carries Nexus contracts only; a context adapter
  (7.4) will translate it, and the projector never knows which model receives it.
- **Event-sourced timestamps** — `created_at`/`updated_at` ride the memory events,
  so projection is deterministic (not fresh-per-call).

Proven by `tests/golden/test_phase7_continuity.py` (as-of reconstruction,
determinism, read-only, and a model swap leaving user/agent + memory unchanged).

### Phase 7.4 — ContextAdapter (the translation boundary)

Translate the model-neutral NCS into a provider/model-specific request without
letting provider concerns leak backward into Mnemosyne:

    NCS (model-neutral) -> ContextAdapter -> ContextRequest (provider/model-specific)

- **The adapter is a translation boundary, not an orchestration layer (AD-038).**
  `ContextAdapter.adapt(ncs) -> ContextRequest` is a pure, deterministic function
  of (ncs, configuration). It translates identity, procedures, semantic memories,
  preferences, and the as-of stamp into a target model's request shape.
- **Provider vocabulary stays on the far side.** The reference
  `OpenAIContextAdapter` (`core/context.py`) understands OpenAI's request
  VOCABULARY (a `messages` list with `role`/`content`, a `temperature`) but not
  its SDK or execution. A local-model adapter can emit a completely different
  shape (one raw `prompt`) from the same NCS.
- **The must-not list is structural.** The adapter never retrieves or mutates
  memory, never accesses a MemoryStore, never decides identity or selects a model,
  never invokes a model, never calls MCP, never touches a provider SDK or the
  event store, and never modifies the NCS. `core/context.py` is stdlib-only — the
  layer-boundary and provider-leakage gates hold it there — and the adapter's only
  dependency is its explicit, frozen configuration.
- **One continuity representation; many model representations.** The golden test
  adapts the SAME NCS through two adapters (OpenAI-flavored + local-model-flavored)
  and asserts the two requests differ in shape while the NCS is unchanged and
  provider vocabulary never appears in it.

Proven by `tests/golden/test_phase7_context_adapter.py` (determinism, purity,
translation, isolation, and a second adapter producing a different representation
from the same NCS).

### Phase 7.5 — model handoff / continuity independence

The property Phase 7 exists to prove: **a model change must not require transfer
of the previous model's private context for Nexus continuity to survive.**

    Session A -> Model A -> private context --X-- (must not cross)
                         -> authoritative memory (events)
                         -> NCS -> ContextAdapter -> ContextRequest -> Model B

- **Reconstructive, not transmissive (AD-039).** Model B *reconstructs*
  continuity from authoritative state; Model A never *transmits* it. The handoff
  is the existing 7.1→7.2→7.3→7.4 pipeline re-run with a new `ModelIdentity`:
  re-project the same event-sourced memory as-of, then adapt. Nothing new is
  added — no router, no summarization, no transcript persistence, no second
  continuity mechanism.
- **The invariant holds at the handoff:** user and agent identity stay
  byte-identical; only `ModelIdentity` changes (7.1), and memory the model
  deliberately committed survives (7.2) through the projection (7.3) into the
  target request (7.4).
- **The previous model's private context is structurally absent.** Model B's
  request is built from the NCS alone, so a session transcript, the old model's
  response, and any un-persisted private thought cannot appear in it. The golden
  test holds a `SECRET_A_ONLY` marker in Model A's context and asserts it stays
  out of B's request.
- **The proof stops at the model boundary.** Model B is a deterministic test
  double that RECEIVES the `ContextRequest`; no LLM inference, provider, or
  network is involved. Actual provider integration is a later concern.

Proven by `tests/golden/test_phase7_handoff.py` (identity invariant, private-
context isolation, persisted-state survival, determinism, reconstruction).

**Phase 7 is complete** — 7.1 identity → 7.2 authoritative memory → 7.3 as-of
continuity → 7.4 context adaptation → 7.5 model-independent handoff. Mnemosyne
proves the central claim: Nexus owns identity, memory, knowledge, continuity, and
state; models are pluggable reasoning backends, and continuity survives a model
change without the previous model's private context.

### Phase 8 — capability / agency plane

Phase 7 proved Mnemosyne can *remember*; Phase 8 asks what it can *do* with that
persistent state. The boundary is authority: a model proposes an operation, and
Nexus — not the model — is the authority that validates it. The novelty over the
Nexus execution plane is that the authority is now **continuity-aware** (it reads
the NCS) and **model-independent** (a model swap never changes a verdict).

### Phase 8.1 — capability contracts + the continuity-aware authority

Define the agency-level contracts and the authority boundary (AD-040):

    model -> ActionRequest -> Authority(action, NCS) -> ActionVerdict

- **The model proposes; the authority decides.** `Capability` (a declared
  operation + risk), `ActionRequest` (the proposal: capability + parameters +
  agent role + scope), and `ActionVerdict` (allow/deny/approval + reasons) are
  the contracts; the model only ever emits an `ActionRequest`.
- **Model-independent authority.** `ContinuityAuthority` (`control/authority.py`)
  never reads `ncs.model`, so the same proposal with the same NCS (modulo the
  proposing model) yields the identical verdict — the Phase 7 invariant, extended
  from memory to action.
- **Continuity-aware.** The authority composes the existing static policy gate
  (risk + allow/denylist, via `PolicyEngine`) with the durable NCS: a Preference
  (`capability.<name>`, scoped to the action's `scope`) overrides the risk
  verdict. The same proposal yields APPROVAL_REQUIRED / DENY / ALLOW as the
  durable state changes — driven by the world, not the model.
- **Decision ≠ Action, structurally.** The authority lives in `control/`, so the
  existing control-plane purity gate (`test_control_plane_purity.py`) enforces
  that it never executes, never emits, and never implements the Executor.
- **No self-authorization; static DENY is final.** The model's `claims` are never
  read, and continuity never softens a destructive/denylist DENY — the reflex arc
  stays authoritative.

Proven by `tests/golden/test_phase8_capability.py` (contract, model-independence,
continuity-awareness, scope-awareness, no self-authorization, DENY-final, purity).
The proof stops at the verdict — no execution, no autonomy.

### Phase 8.2 — controlled action execution

When the Authority returns ALLOW, execute the proposed capability through the
existing execution plane and record an authoritative, reconstructible completion
event (AD-041):

    ActionRequest -> Authority -> (ALLOW) -> Executor -> ActionResult
                    -> action.completed -> reconstruct

- **ALLOW is the only execution path.** `ActionRunner` (`execution/action.py`) is
  the single path from verdict to execution: DENY and APPROVAL_REQUIRED never
  reach the Executor. Neither the model nor the executor can bypass the verdict.
- **The existing Executor does the work.** The runner delegates to the injected
  `Executor` (the Phase 3 boundary) — no agency-specific execution engine. It maps
  the ActionRequest to a `ToolCall` and wraps the `ToolResult`.
- **ActionResult is the outcome, not the authorization.** `ActionVerdict` answers
  "may this happen?"; `ActionResult` answers "what happened?" (success/output/error
  + identity). They are never combined.
- **An authoritative, bounded completion event.** A successful execution emits
  `action.completed` carrying who requested, which capability, the authorized
  parameters, the run/task, when, and the bounded result — never a transcript.
- **Reconstruction.** `reconstruct_action(action_id, events)` projects the
  `ActionResult` back from the event log, deterministically — the agency layer's
  bridge to the event-sourced substrate (Phase 3/6).

Proven by `tests/golden/test_phase8_execution.py` (ALLOW executes exactly once;
DENY/APPROVAL never call the executor; result/provenance/identity preserved;
reconstruction identical and repeatable).

The slice stops here: no retries, no learning, no capability discovery, no
planning, no autonomy, no new persistence. Execution does not silently become
learning — that belongs to Phase 9.

### Phase 8.3 — action lifecycle / idempotency

Can Nexus execute an authorized action exactly once from Nexus's perspective,
preserve its lifecycle, and distinguish retry/recovery from a genuinely new
action (AD-042)?

    proposed -> authorized -> executing -> completed | failed

- **The action owns its identity.** `ActionRequest.action_id` is the identity of
  the LOGICAL action — the idempotency key, distinct from run/task/provider ids.
  The same id is the same logical action; a new id is a new action.
- **Idempotency is the hard invariant.** `ActionRunner` checks the authoritative
  event log (durable when a `DurableEventBus` is injected) for an existing
  `action.completed` / `action.failed` for that id, and returns the reconstructed
  result WITHOUT re-executing. A retry never becomes a second execution.
- **Failure is authoritative, not a retry trigger.** A failed execution emits
  `action.failed` (distinct from `action.completed`); retrying a failed id returns
  the existing failure — Nexus never auto-retries. A genuine re-attempt is a NEW
  action_id.
- **Compensation is a boundary, not an implementation.** A corrective operation is
  a new action through the full pipeline (ActionRequest → Authority → Executor →
  ActionResult). History is never rewritten: A.completed, then B.completed.
- **Reconstruction.** `reconstruct_action(action_id, events)` projects the
  terminal outcome (completed OR failed) deterministically — latest terminal wins.

Proven by `tests/golden/test_phase8_lifecycle.py` (exactly-once, idempotent retry,
deterministic reconstruction, distinct new action, authoritative failure with no
auto-retry).

Still frozen out: automatic retries, autonomous recovery, compensation planning,
new persistence, learning from outcomes. Execution is data; learning is Phase 9.

**Phase 8 is complete and frozen** — 8.1 authority → 8.2 execution → 8.3
lifecycle/idempotency. The invariant: *models may propose actions; Nexus alone
authorizes, executes, records, and reconstructs their authoritative outcomes — a
logical action has a Nexus-owned identity, and its history is never rewritten.*
Compensation is a new authoritative action, not a rewrite (AD-043): no
CompensationPlanner / UndoManager / special execution mechanism until a concrete
capability demands one.

### Phase 9 — learning / adaptation

Phase 8 proved Nexus can turn reasoning into controlled action; Phase 9 asks what
should *change* because of what happened. The governing rule is **proposal before
mutation**: experience may produce a versioned, evidence-grounded candidate
adaptation, but no observation, model output, score, or confidence value may
directly modify authoritative state.

### Phase 9.1 — learning proposals (the epistemic boundary)

Can Nexus derive a proposed adaptation from authoritative experience without
changing authoritative memory, identity, policy, or history (AD-044)?

    authoritative experience -> observe -> LearningProposal -> X (NO MUTATION)

- **One contract earns its place: `LearningProposal`.** It carries a Nexus-owned
  `proposal_id` (never the identity of a future memory version), the MemoryStore
  `kind` (no learning-specific enum), the exact `target` + `target_version`
  observed, a `proposed_change` (candidate data), `evidence` (references to
  Nexus-owned artifacts), `scope`, and `proposed_by` (the interpreter).
- **No LearningAuthority yet.** Observation produces a proposal; nothing accepts
  it. A proposal with confidence 0.9999 is still a proposal.
- **The source is authoritative experience.** Evidence references action_id /
  run_id / memory versions — not a copied transcript. Models may interpret
  experience; Nexus owns the experience being interpreted.
- **The strongest invariant: observation cannot mutate.** The analyzer receives
  evidence references and a scalar target version — never a MemoryStore — and its
  output is a value object that never enters the NCS as accepted knowledge.
- **Stale-target awareness starts here.** A proposal pins the version it observed;
  a later legitimate vN+1 does not retarget it (conflict resolution is 9.2).
- **Proposals are not yet event-sourced.** A value object returned by the learner
  is enough; a durable proposal lifecycle earns an event when 9.2 needs it.

Proven by `tests/golden/test_phase9_learning_proposal.py` (proposal-not-mutation,
evidence-grounded, target-versioned, scope-preserving, model non-authority,
continuity isolation, temporal stability).

### Phase 9.2 — learning authority (the authorization boundary)

Should this specific, evidence-grounded proposal be permitted to become
authoritative (AD-045)?

    LearningProposal -> LearningAuthority -> LearningVerdict -> X (NO MUTATION)

- **Authority is distinct from analysis.** 9.1 answers "what change does the
  evidence suggest?"; 9.2 answers "may that change be applied?" — and stops at the
  verdict. `LearningVerdict` reuses `PolicyVerdict` (ALLOW/DENY/APPROVAL_REQUIRED)
  + reasons; no adaptation occurs here.
- **Evidence authenticity.** `GroundedLearningAuthority` (`learning/authority.py`)
  verifies cited action_ids against authoritative history — a fabricated or
  nonexistent reference is DENY. A proposal cannot self-authorize.
- **Target freshness.** A proposal whose target version is no longer current is
  DENY (`stale_target`), never silently rebased: generate a fresh proposal against
  the current version.
- **Scope enforcement.** The proposal must be scoped to the target's exact scope;
  a mismatch is DENY (never silently broadened — project-local evidence cannot
  become global learning).
- **Proposer non-authority.** The verdict reads only (evidence, freshness, scope,
  kind); `proposed_by`, confidence, and self-claims are ignored.
- **Policy-controlled verdict.** A configurable kind-based `LearningPolicyRules`
  determines ALLOW/DENY/APPROVAL_REQUIRED (default: semantic → ALLOW, procedure /
  preference → APPROVAL_REQUIRED) — the analyzer never decides its own policy.
- **ALLOW is permission, not mutation.** Every verdict — including ALLOW — leaves
  memory, NCS, identity, proposal, and history untouched.

Proven by `tests/golden/test_phase9_learning_authority.py` (all three verdicts,
evidence authenticity, target freshness, scope enforcement, non-self-authorization,
purity, and proposal immutability).

### Phase 9.3 — authorized adaptation (the mutation boundary)

Given an ALLOWed, still-current proposal, create exactly one new authoritative
memory version with complete provenance — without rewriting history or letting
authorization go stale (AD-046):

    LearningProposal + ALLOW -> AdaptationRunner -> record_if_current
                              -> AdaptationResult -> memory.updated -> new version

- **Revalidation, never a stale token.** `AdaptationRunner.adapt(proposal, ncs)`
  invokes the authority itself against the CURRENT ncs immediately before mutation;
  an old ALLOW is not a capability token.
- **Atomic freshness in the store.** `MemoryStore.record_if_current(...)` is a
  compare-and-append: it appends `expected_version + 1` only if the current version
  still equals `expected_version`, in one conditional INSERT — the write boundary,
  not a prior check, owns the invariant.
- **Exactly-once by provenance.** A `proposal_id` already present in the target's
  history returns the existing `AdaptationResult` — no second version, no new
  adaptation store, no `learning.adapted` event (provenance is enough).
- **Append-only, provenance-preserving.** The prior version stays byte-identical;
  the new version carries bounded provenance (proposal + prior version + evidence).
- **Continuity convergence.** After adaptation the new version is ordinary
  authoritative memory: Phase 7 projection exposes it — no learning-specific NCS.
- **APPROVAL_REQUIRED / DENY never mutate.** The default policy keeps Procedure →
  APPROVAL_REQUIRED; adaptation never implements an approval workflow or weakens
  policy to make a test pass.

Proven by `tests/golden/test_phase9_adaptation.py` (revalidation, the atomic
compare-and-append race, exactly-once, append-only history, provenance, scope,
continuity convergence, and the non-ALLOW paths).

**Phase 9 is complete and frozen** — 9.1 observe → 9.2 authorize → 9.3 adapt. The
invariant: *adaptation is an authorized, compare-and-append transition — only a
currently permitted proposal may create exactly one new authoritative memory
version, with provenance to its prior version and evidence; stale authorization
never mutates state, and history is never rewritten.* A successful adaptation does
not recursively count as evidence for another (no self-feeding loop).

### Phase 10 — federation

Until now there has been one authority domain. Federation introduces other systems
with their own identity, state, policy, history, and authority. The governing
invariant: **federation composes authority; it does not merge authority** — neither
Nexus gets direct access to the other's authoritative state.

### Phase 10.1 — federation identity / claims

Can Nexus represent another independent Nexus and the capabilities it claims to
expose without treating those claims as local authority (AD-047)?

    remote declaration -> Federation -> FederationPeer -> X (not trusted)

- **One contract: `FederationPeer`.** A local, bounded representation of another
  authority domain — distinct from AgentIdentity (inside a domain) and
  ModelIdentity (a reasoning backend). `peer_id` is THIS Nexus's stable, namespaced
  identity for the remote domain, never the remote's arbitrary self-description.
- **Claims, not authority.** Advertised capabilities (strings) and metadata
  (including "trusted"/"authority") are carried as remote self-description, never
  consulted by local authority.
- **No state exchange.** The representation never exposes local NCS or raw
  memory/event history — only explicitly selected fields cross.
- **Transport independence.** `federation/peer.py` is core-only: no HTTP/MCP/SDK
  dependency (layer- and provider-gated).
- **Explicit compatibility.** `Federation` checks protocol version
  deterministically; an incompatible version fails (no negotiation yet).
- **No crypto.** 10.1 establishes the trust boundary contractually; authentication
  is a later mechanism.

Proven by `tests/golden/test_phase10_federation_peer.py` (independent identity,
stable representation, claims-not-authority, state isolation, bounded disclosure,
explicit compatibility, malicious self-authorization rejected).

### Phase 10.2 — federated delegation

Can Nexus A request a bounded capability from Nexus B without granting B authority
over A, or treating the request as authority over B (AD-048)?

    A authority -> DelegationRequest -> B authority -> DelegationVerdict -> X (no execution)

- **Two independent authority decisions.** `DelegationSender` (A-side) authorizes
  SENDING via A's own authority — if A would not permit the capability, the request
  never crosses (federation is not a bypass). `DelegationReceiver` (B-side)
  independently authorizes ACCEPTING via B's own authority.
- **Contracts, not ActionRequest reuse.** `DelegationRequest` carries a Nexus-owned
  `delegation_id` (never a run/task/action id — B mints its own action identity
  later), the peer, capability, bounded parameters/scope, and bounded provenance.
  `DelegationVerdict` reuses PolicyVerdict; ALLOW means "accepted in principle",
  never that anything executed.
- **Claims and permissions do not transit.** A's permission is permission to ask;
  B's ALLOW is permission to accept; neither the request's self-claims
  ("trusted"/"admin") nor A's identities confer authority inside B.
- **Bounded disclosure.** The request carries only task context — no NCS, memory,
  event history, or private model context.
- **Scope never broadens.** An incompatible/broader scope is DENY (no negotiation
  yet).
- **No execution.** The slice stops at B's verdict: no executor, no ActionResult,
  no completion event, and neither domain's state is mutated.

Proven by `tests/golden/test_phase10_delegation.py` (outbound gate, independent
inbound authority, refusal, self-authorization rejection, outbound bypass, scope
rejection, no execution, and state isolation).

### Phase 10.3 — federated outcome

When B executes an accepted delegation, what may A truthfully record (AD-049)?

    B ActionResult -> federation translation -> FederatedOutcome -> A receipt

- **Report ≠ observation.** A records "B reported X", never "A observed X":
  `FederatedOutcome` is a bounded remote report, and A's event is
  `federation.outcome.received` — never a copy of B's `action.completed`.
- **B composes Phase 8.** `DelegationService` (`federation/outcome.py`) executes an
  accepted delegation through B's own ActionRunner (re-authorizing at execution
  time — a stale ALLOW never bypasses current authority), and translates the
  ActionResult. B mints its own action identity; A never assigns one.
- **Exactly-once local receipt, conflict-preserving.** `FederatedOutcomeRecorder`
  records one `federation.outcome.received` per delegation_id (deterministic event
  id); a duplicate is idempotent, a conflicting report is rejected — history is
  never rewritten.
- **No authority/mutation injection.** Remote output is recorded only as bounded
  data; it never mutates A's authority, memory, NCS, actions, or learning.
- **Independent histories.** B's `action.completed` stays authoritative in B; A's
  `federation.outcome.received` stays authoritative in A; neither is copied.

Proven by `tests/golden/test_phase10_federated_outcome.py` (report≠observation,
stale-authorization, duplicate receipt, conflict rejection, no injection,
independent histories).

**Phase 10 is complete and frozen** — 10.1 represent → 10.2 delegate → 10.3 report.
The invariant: *federation connects independent authority domains without merging
them — peers exchange bounded claims, requests, and reports, while identity,
authorization, execution, state, and authoritative history remain locally owned.*

## Golden tasks

20–50 deterministic tasks that must pass after every architectural change:

```
G001 retrieve information from a document
G002 answer using two sources
G003 use a read-only GitHub tool
G004 attempt a prohibited write → denied
G005 request approval for a permitted write
G006 choose local model for a private task
G007 fall back to cloud model
G008 recover after tool failure
G009 recover after worker crash
G010 detect failed verification and replan
```

This is an engineering regression suite, not a marketing score.

## Architectural decisions (AD)

Decisions whose wrong interpretation could cause regressions. Not a changelog.

- **AD-001** — Knowledge versioning is per identity `(source, document, location)`, never global.
- **AD-002** — Retriever implementations may rank differently; only the contract must match.
- **AD-003** — Retrieved provenance (source/document/location/version/metadata) crosses the boundary with the chunk.
- **AD-004** — Provider objects never cross the boundary; adapters produce contracts.
- **AD-005** — Control plane is pure (decides, never executes); side effects require the `Executor` capability.
- **AD-006** — Store lifecycle: ingestion supersedes, never deletes; last-write-wins per identity; idempotent re-add; metadata is not identity.
- **AD-007** — Memory is the sequence plane, separate from knowledge: a finished run projects into an Episode deterministically (no LLM); outcome derives from events.
- **AD-008** — The reranker is internal to a Retriever implementation, never part of the Retriever contract (`search(query, filters) -> RetrievalResult`).
- **AD-009** — Every externally observable execution step emits an event *before* it is considered complete (`*.requested` first, `*.completed` only on return); a step that never returns is observably incomplete.
- **AD-010** — Execution failure semantics: an *expected* tool outcome (non-zero exit, timeout) is `ToolResult(success=False, …)`; a *launch* failure (unknown tool, missing executable, malformed spec) raises. Expected failures are data; launch failures are bugs.
- **AD-011** — The model is never the authority for executables: `ToolCall` names a logical tool, and only the execution-layer registry maps it to an executable. No arbitrary shell execution by default.
- **AD-012** — `ModelResponse` is Nexus-shaped, not provider-shaped: the adapter translates provider → Nexus and drops provider-specific concepts (`finish_reason`, `system_fingerprint`, the `usage` dict); the caller only ever sees Nexus semantics.
- **AD-013** — The orchestrator owns sequencing and correlation; capabilities own execution. It composes injected capabilities (retriever, executor, policy, tools, state, bus) and never performs capability work itself (no subprocess/file/network/vector-store/model-SDK/MCP).
- **AD-014** — Verification is explicit: `tool succeeded != task succeeded`. The evaluator returns an `Evaluation` (`passed` / `reason` / `replan_required`), and the orchestrator interprets it — the evaluator never decides what happens next.
- **AD-015** — Replanning is bounded and event-sourced: `max_replans` is run/plan state, exhaustion is a terminal outcome, and every attempt/replan is an event reconstructible into the same attempt trail.
- **AD-016** — The evaluator observes and judges; it never executes a capability or mutates execution state (returns `Evaluation`; the orchestrator coordinates).
- **AD-017** — Task ownership is a durable event (`task.claimed` with `worker_id`), never an in-memory flag; the event/state log is the source of truth for the task lifecycle (`queued → claimed → completed/failed`).
- **AD-018** — Task delivery is at-least-once, never exactly-once; duplicate tool execution after recovery is explicit and observable, and idempotency/recovery is applied where required (as with stable chunk ids).
- **AD-019** — Recovery is a lease rule on durable evidence (`CLAIMED` + expired `claimed_at` → recoverable), and it lives in queue infrastructure — never the orchestrator, which has no idea whether it is running normally or after a previous worker died.
- **AD-020** — MCP is implementation #N: the adapter owns MCP's vocabulary (JSON-RPC, `tools/list`, `tools/call`); `ToolCall`/`ToolResult`/`ToolDefinition` stay Nexus contracts, and MCP libraries are confined to the integration layer.
- **AD-021** — Applications compose Nexus; Nexus components do not discover each other through global state. The surface observes and commands through contracts/events, and execution never depends on the surface.
- **AD-022** — The HTTP layer is an edge adapter: FastAPI/Pydantic stop at apps/; Nexus contracts are serialized at the edge (never HTTP request models propagating inward), and task ID vs run ID stay distinct (`/tasks/{id}` lifecycle, `/traces/{run_id}` history).
- **AD-023** — The trace is a read-side projection of the event stream: events stay the source of truth, the projector is deterministic and non-mutating, decisions (`policy.decision`) are observable, and incomplete/recovery transitions remain visible — observability consumes core only, never execution/providers/HTTP.
- **AD-024** — The WebSocket subscribes to the event bus (never the orchestrator), replays durable history then tails live events filtered by run_id at the edge, uses a bounded per-client queue (disconnect-on-overflow), and is purely downstream — removing every client never changes execution or the event log.
- **AD-025** — Approval is a durable task-state transition, not an HTTP callback: the approval is single-use and bound to a specific proposal (task/run/tool/risk), the policy is re-checked on resume (approval never bypasses it), and the surface commands Nexus (requeues) without ever executing the tool.
- **AD-026** — The dashboard is a disposable presentation layer over Nexus projections: it renders state (decisions, attempts, recovery, interruption) without reconstructing authority, carries no business logic, and can request approval via the API but never execute a capability or become an independent source of truth.
- **AD-027** — Concurrency is a store contract, not a worker concern: each worker thread gets its own SQLite connection (thread-local, store-owned); SQLite is configured deliberately (`busy_timeout`, WAL, `synchronous=NORMAL`, `foreign_keys=ON`); and every exclusive transition (claim, consume, recover) is ONE conditional UPDATE the database arbitrates — the loser gets an explicit `None`, never an exception or silent overwrite. SQLite is the reference implementation; the store's method surface is the provider-neutral contract.
- **AD-028** — Ownership is generation-specific, not merely worker-specific: `claim` mints a fresh `claim_generation`, and terminal transitions (`complete`/`fail`) are conditional on the exact (worker_id, generation) the worker acquired. A stale worker whose lease expired is harmless — its completion/failure is a no-op (`False`), never an overwrite of the re-claimed owner.
- **AD-029** — Reproducibility is manifest + events, never events alone: a run's defining inputs (knowledge snapshot, policy, model, tools, router, replan limits) are captured in a `RunManifest` BEFORE execution; replay compares a semantic fingerprint of the event log (not raw bytes), and derives a status (REPRODUCIBLE / REPRODUCIBLE_WITH_DIFFERENCES / NON_REPRODUCIBLE) from explicit conditions, never a guess.
- **AD-030** — Persisted data carries a schema version, distinct from contract versions and component identities: the on-disk format version lives in SQLite's `PRAGMA user_version`; a build reads the current version, migrates the legacy (v0) format at open, and rejects newer versions deterministically — never silently interpreting old or future data.
- **AD-031** — Run identity is `run_id`; the manifest (conditions) and fingerprint (semantic outcome) are derived evidence, persisted as first-class records — never the run's identity, never written into every event. The fingerprint is computed at a terminal state from the canonical projection, so a partial/crashed run has no fabricated fingerprint.
- **AD-032** — A run's terminal fingerprint is authoritative only if its claim generation was current at completion: the orchestrator records the manifest at start; the worker records the terminal fingerprint only after its claim generation is confirmed current. A stale worker leaves a NULL fingerprint, so recovery can never manufacture a second authoritative run.
- **AD-033** — An ingress protocol (MCP, HTTP, CLI) is a disposable surface over `NexusRuntime`: the adapter owns the protocol's vocabulary and only Nexus contracts cross. Nexus can be an MCP server without the orchestrator (or core/control/execution/knowledge) importing MCP vocabulary — the provider-neutrality of 3.8's MCP executor, inverted.
- **AD-034** — An agent is a capability composition, not a new execution substrate: a stable logical identity (`agent`) plus a runtime, delegating through `parent_run_id`. Agent identity is never conflated with worker_id/task_id/run_id; a delegated agent cannot bypass policy; and each child run keeps its own identity and fingerprint.
- **AD-035** — Identity and continuity are Nexus-owned, durable, and model-agnostic: `UserIdentity`, `AgentIdentity`, and `ModelIdentity` are stable, versionable contracts carried in the `RunManifest`; the model is a pluggable reasoning backend that instantiates (never owns) state. A model swap changes only `ModelIdentity` — user and agent identity are untouched — and `ModelIdentity` equality is on logical fields only, never the provider config (API key, endpoint, SDK object, client).
- **AD-036** — Memory is event-sourced: memory objects are projections of authoritative `memory.created` / `memory.updated` events, never directly mutable rows. A model (or any caller) can propose a change; only an event changes authoritative state. Versioning is explicit (v1 is never silently rewritten), and every record carries provenance back to its source event / run / task / session.
- **AD-037** — Continuity is a projection, not a store: the ContinuityProjector reconstructs a `NexusContinuityState` (identity + event-sourced memory as of an explicit `as_of`) deterministically and read-only. It never mutates, never calls a model, and the NCS is model-neutral — a context adapter (7.4) translates it per model.
- **AD-038** — Context adaptation is a translation boundary, not an orchestration layer: `ContextAdapter.adapt(ncs) -> ContextRequest` is a pure, deterministic function of (ncs, configuration) that turns a model-neutral NCS into a provider/model-specific request. It never retrieves or mutates memory, never decides identity or selects a model, never invokes a model or calls MCP, never touches a provider SDK or the event store, and never modifies the NCS. Provider vocabulary lives only in the request/adapter; the NCS stays model-neutral.
- **AD-039** — A model change must not require transfer of the previous model's private context for Nexus continuity to survive: the handoff is reconstructive, not transmissive. Model B reconstructs continuity from authoritative state (re-project the event-sourced memory as-of with the new `ModelIdentity`, then adapt), never from Model A's messages, hidden state, prompt, transcript, or response. User/agent identity stay byte-identical; only `ModelIdentity` changes; and persisted memory reaches Model B through the same 7.3→7.4 pipeline.
- **AD-040** — The authority that validates a proposed action is a pure, model-independent, continuity-aware function of (proposal, NCS, policy): `Authority.evaluate(action, ncs) -> ActionVerdict`. The model proposes; the authority decides; the executor executes; events record. The verdict never reads the proposing model's identity, reads durable continuity (preferences scoped to the action) to refine the static risk gate, ignores the model's self-assertions, and never executes or emits. Static DENY (destructive/denylist) is final — continuity refines, never overrides the reflex arc.
- **AD-041** — Action execution is gated by the authority and recorded as an authoritative, reconstructible event: only an ALLOW verdict reaches the Executor (DENY / APPROVAL_REQUIRED never execute); the outcome is an `ActionResult` (distinct from `ActionVerdict`); and a successful execution emits a bounded `action.completed` event carrying enough provenance (who/capability/parameters/run/task/when/result) to reconstruct the completion deterministically. Execution never silently becomes learning.
- **AD-042** — An action's identity is its `action_id` — the idempotency key, distinct from run/task/provider ids. The same id is the same logical action and executes at most once from Nexus's perspective: a retry returns the reconstructed authoritative terminal outcome (completed or failed) without re-executing, and a failed action is never auto-retried. A new id is a new logical action. Terminal outcomes are authoritative, reconstructible events; failure is a known outcome, never a trigger for learning or history rewrite.
- **AD-043** — Compensation is a new authoritative action, never a rewrite. A corrective operation is proposed, authorized, executed, and recorded exactly like any other action — with its own identity — so the event log records `A.completed` then `B.completed`, never a rewritten A. No CompensationPlanner, UndoManager, or special execution mechanism is introduced until a concrete capability demands one.
- **AD-044** — Learning is proposal before mutation: experience may produce a versioned, evidence-grounded candidate adaptation, but no observation, model output, score, or confidence value may directly modify authoritative state.
- **AD-045** — Learning authority is distinct from learning analysis: a proposal may recommend change, but only Nexus authority may permit adaptation; stale, unverified, or improperly scoped proposals cannot authorize themselves, and an ALLOW verdict never mutates authoritative state.
- **AD-046** — Adaptation is an authorized, compare-and-append transition: only a currently permitted proposal may create exactly one new authoritative memory version, with provenance to its prior version and evidence; stale authorization never mutates state, and history is never rewritten.
- **AD-047** — Federation begins with independent authority domains: a remote Nexus may declare identity, compatibility, and capability claims, but those declarations never become local authority or shared state; only explicitly bounded information crosses the federation boundary.
- **AD-048** — Delegation transfers a bounded request, never authority: the requesting Nexus must authorize sending it, the receiving Nexus independently decides whether to accept it, and neither peer's claims, permissions, or identities confer authority inside the other domain.
- **AD-049** — A federated outcome is an authoritative record of what a peer reported, not a promotion of the peer's execution into local fact: the executing Nexus owns its action and history, the receiving Nexus owns only the durable receipt, duplicate reports are idempotent, conflicting reports never rewrite history, and remote output confers no local authority or mutation.
- **AD-050** — An authorized logical action becomes an observable attempt before execution begins: `action.requested` is durably recorded before the Executor may cause a side effect, terminal events record only observed completion or failure, and the absence of a terminal event remains an unknown outcome rather than a failure verdict.
- **AD-051** — Memory retirement is an append-only temporal transition, not deletion or content revision: from its retirement point forward a retired memory no longer participates in continuity, while its prior versions and pre-retirement projections remain reconstructible; retirement is freshness-checked at the authoritative write boundary and never silently reactivates through ordinary updates.

## Contract conformance: MUST MATCH vs MAY DIFFER

| MUST MATCH (the boundary) | MAY DIFFER (implementation detail) |
|---|---|
| contract shape | ranking |
| identity | scoring |
| provenance | chunk boundaries |
| version | embedding strategy |
| metadata | retrieval algorithm |
| filters | |
| error semantics | |

A conformance test that asserts a MAY-DIFFER property turns the abstraction into
a disguised implementation spec. Don't.

## The progression (every capability)

```
Contract → stdlib reference → second independent implementation → conformance → production adapter
```

Chroma is **implementation #3** — landed in `knowledge/chroma.py` — and it is not
an architectural event: it satisfies the same `KnowledgeStore` protocol, and the
conformance suite passed with it **unchanged**. If adding it had required
modifying the orchestrator, the boundary — not the new implementation — would be
what was wrong.

## Repository layout

```
nexus/
  apps/        api, worker, dashboard, cli
  core/        contracts, events, state, tasks, errors, ids
  control/     router, policy, evaluator, models, tools
  knowledge/   ingestion, parsing, chunking, embeddings, retrieval
  memory/      episodes, extraction, episodic recall
  execution/   orchestrator, planning, workers, queue, verification
  integrations/ mcp, ollama, openai, github
  observability/ tracing, events, metrics
  tests/       unit, integration, golden, fixtures
  deploy/      docker, oracle, nginx
  docs/        architecture, decisions, roadmap, api
```

The separation that matters: **contracts, control, knowledge, execution,
integrations** — not a flat pile of files.

## Architecture conformance — principles → their enforcing tests

Every architectural principle has a test that makes violating it a failing build.
The suite answers two questions: *"does Nexus work?"* (golden tasks) and
*"does Nexus still conform to the architecture?"* (conformance tests).

| Principle | Enforcing test |
|---|---|
| Core is dependency-light (stdlib only) | `tests/conformance/test_layer_boundaries.py` |
| Nothing imports upward (control/execution → core only) | `tests/conformance/test_layer_boundaries.py` |
| Control plane is pure (decides, never executes/emits) | `tests/conformance/test_control_plane_purity.py` |
| Side effects require the `Executor` capability (Decision ≠ Action) | `tests/conformance/test_control_plane_purity.py` |
| No provider leakage (core consumes contracts, adapters produce them) | `tests/conformance/test_no_provider_leakage.py` |
| Retriever boundary holds across implementations (same contract, no ranking assumption) | `tests/conformance/test_retriever_contract.py` |
| Persisted store is observationally equivalent across a restart | `tests/conformance/test_persistence_contract.py` |
| Chroma (implementation #3) persists across a real process restart, contract unchanged | `tests/conformance/test_chroma_persistence.py` |
| Ingestion is idempotent (ingest x3 = one logical chunk) | `tests/golden/test_phase2_idempotency.py` |
| Memory is the sequence plane (deterministic run→episode projection, no LLM) | `tests/golden/test_phase2_memory.py` |
| Hybrid retrieval is an implementation detail (one contract, reranker internal) | `tests/golden/test_phase2_hybrid.py` |
| Execution steps emit an event before completion (requested first, completed only on return) | `tests/golden/test_phase3_executor.py` |
| Executor boundary holds across implementations (only serializable contracts cross) | `tests/conformance/test_executor_boundary.py` |
| Real subprocess execution: bounded output, timeout, expected-fail-as-ToolResult, no shell | `tests/golden/test_phase3_subprocess.py` |
| Model execution: provider→Nexus translation, request identity, rejection-as-failure, bounded output | `tests/golden/test_phase3_model.py` |
| Orchestrator coordinates, never performs capability work (AST gate) | `tests/conformance/test_orchestrator_purity.py` |
| The full deterministic loop: Task→Answer, ordered trace, recoverable state, policy authoritative | `tests/golden/test_phase3_orchestrator.py` |
| Verification + bounded replanning: FAIL→replan→PASS, tool-success≠task-success, reconstructible attempts | `tests/golden/test_phase3_replan.py` |
| Policy stays authoritative across replans (DENY persists on every attempt) | `tests/golden/test_phase3_replan_policy.py` |
| The worker has no provider/retrieval knowledge (only composes queue + orchestrator) | `tests/conformance/test_worker_purity.py` |
| Durable task execution: ownership/completion durable + observable, state reconstructible from the log | `tests/golden/test_phase3_worker.py` |
| Crash recovery under real process death (two failure points, lease-based requeue, reconstruct) | `tests/golden/test_phase3_recovery.py` |
| MCP adapter boundary: only Nexus contracts cross (provider-specific fields dropped, AD-010) | `tests/conformance/test_mcp_boundary.py` |
| MCP end-to-end: unchanged orchestrator, policy above MCP, DI-only swap | `tests/golden/test_phase3_mcp.py` |
| The surface is a leaf; execution never depends on it | `tests/conformance/test_surface_boundary.py` |
| Runtime composition: applications compose Nexus, no global-state discovery, uniform surface | `tests/golden/test_phase4_runtime.py` |
| HTTP/framework types stop at the surface (nothing below apps/ imports them) | `tests/conformance/test_http_boundary.py` |
| Thin HTTP adapter: async /ask, durable task status, event history, restart-safe, explicit 4xx | `tests/golden/test_phase4_api.py` |
| Trace projection is pure (no execution/provider/HTTP deps) | `tests/conformance/test_trace_projection_purity.py` |
| Trace projector: deterministic, decisions/replans/incomplete/recovery visible | `tests/golden/test_phase4_trace.py` |
| WebSocket: replay + live tail, run_id filter, downstream (no execution/event-log effect) | `tests/golden/test_phase4_ws.py` |
| Approval lifecycle: durable waiting, single-use, task-bound, policy re-checked, downstream purity | `tests/golden/test_phase4_approval.py` |
| Dashboard: disposable projection consumer — decisions/attempts/recovery/interruption, no authority | `tests/golden/test_phase4_dashboard.py` |
| CLI: thin surface adapter — same async ask + same projected views as REST/WS/dashboard | `tests/golden/test_phase4_cli.py` |
| Operator Console: projection-only — identities/NCS/approvals/events/action-lifecycle read-only; interrupted action is attempted/unknown, never failed; REST/CLI/raw state agree | `tests/conformance/test_console_surface.py` |
| Atomic approval consumption: two workers race, exactly one executes (single-use) | `tests/golden/test_phase5_concurrency.py` |
| Hardening: per-call tool identity, event-sourced approval, terminal step semantics, atomic recovery, dead-event cleanup | `tests/golden/test_phase5_hardening.py` |
| Per-worker connections + deliberate SQLite policy (5.2): one connection per thread, busy_timeout/WAL, concurrent independent work | `tests/golden/test_phase5_connections.py` |
| Task ownership is atomic (5.3): claim race + recovery/claim race → exactly one owner | `tests/golden/test_phase5_ownership.py` |
| Per-attempt tool identity (5.4): same tool twice → distinct call_ids; recovery re-run → new call_id | `tests/golden/test_phase5_correlation.py` |
| Full-stack concurrency: N workers drain READ/WRITE/BOOM tasks end-to-end — correct terminal states, per-attempt identity, clean partition | `tests/golden/test_phase5_system.py` |
| Stale-owner isolation (A2-1): a stale worker's completion/failure is a no-op; only the current claim generation may transition | `tests/golden/test_phase5_stale_owner.py` |
| Run identity + deterministic replay (6.1): manifest before execution, semantic fingerprint, named input difference | `tests/golden/test_phase6_replay.py` |
| Persisted-schema versioning (6.2): migrate legacy v0, reject future versions, schema version recorded separately | `tests/golden/test_phase6_schema.py` |
| Durable run identity / fingerprints (6.3): manifest + terminal fingerprint persisted, retrievable by run_id, crash → no fabricated fingerprint | `tests/golden/test_phase6_records.py` |
| Multi-worker arbitration (6.4): a stale worker cannot publish a terminal run record; exactly one authoritative run per task | `tests/golden/test_phase6_arbitration.py` |
| Nexus as an MCP server (6.5): adapter owns MCP vocabulary, durable lifecycle preserved, failure taxonomy intact | `tests/conformance/test_nexus_mcp_boundary.py` |
| Multi-agent composition (6.6): explicit agent identity + durable delegation, policy preserved, child outcomes compositional | `tests/golden/test_phase6_multiagent.py` |
| Identity contracts (7.1): User/Agent/Model identities persisted in the manifest; a model swap changes only ModelIdentity | `tests/golden/test_phase7_identity.py` |
| Memory taxonomy contracts (7.2): event-sourced, versioned, provenanced; direct mutation cannot alter authoritative state | `tests/golden/test_phase7_memory.py` |
| ContinuityProjector / NCS (7.3): deterministic as-of reconstruction, read-only, model-independent | `tests/golden/test_phase7_continuity.py` |
| ContextAdapter boundary (7.4): deterministic, pure, faithful translation; provider vocabulary stays off the NCS | `tests/golden/test_phase7_context_adapter.py` |
| Model handoff / continuity independence (7.5): reconstructive, not transmissive — Model A's private context never crosses | `tests/golden/test_phase7_handoff.py` |
| Capability/authority boundary (8.1): model proposes; a model-independent, continuity-aware authority decides | `tests/golden/test_phase8_capability.py` |
| Controlled action execution (8.2): ALLOW is the only execution path; the existing Executor executes; completion reconstructs | `tests/golden/test_phase8_execution.py` |
| Action lifecycle / idempotency (8.3): same action_id executes at most once; failure is authoritative and not auto-retried | `tests/golden/test_phase8_lifecycle.py` |
| Learning proposals (9.1): observation produces a bounded, evidence-grounded proposal without mutation | `tests/golden/test_phase9_learning_proposal.py` |
| Learning authority (9.2): evidence authenticity, target freshness, scope enforcement; ALLOW is permission, never mutation | `tests/golden/test_phase9_learning_authority.py` |
| Authorized adaptation (9.3): revalidated, compare-and-append, exactly-once, append-only, provenance-preserving | `tests/golden/test_phase9_adaptation.py` |
| Federation identity / claims (10.1): representation before trust; remote claims never become local authority or shared state | `tests/golden/test_phase10_federation_peer.py` |
| Federated delegation (10.2): a bounded request crosses, authority never does; nothing executes | `tests/golden/test_phase10_delegation.py` |
| Federated outcome (10.3): A records "B reported X", never "A observed X"; exactly-once receipt, conflict-preserving, independent histories | `tests/golden/test_phase10_federated_outcome.py` |
| Router never bypasses policy | `tests/golden/test_phase1_composition.py` |
| State is recoverable (a projection of events) | `tests/golden/test_phase0_foundation.py` |
| The boring event envelope (one uniform shape) | enforced by the `Event` dataclass itself |

As each phase ships, its architectural principles get added here with their test.
A principle without a test is not a principle — it is an intention.

A conformance test exists only because there is an architectural property to
preserve — never because a code pattern "feels nicer." The suite is a map of
intent, not a second linter.
