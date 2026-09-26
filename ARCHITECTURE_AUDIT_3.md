# Architecture Audit #3 — Compositional Integrity, Phases 0–10

> **Audit question.** Can Nexus/Mnemosyne compose identity, memory, control,
> execution, learning, and federation without any boundary manufacturing
> authority, observation, freshness, or history that it does not actually possess?
>
> **Second question (only after the first).** What important question, if any,
> remains that no composition of Phases 0–10 can answer?

Audits #1–#2 asked whether the pieces obeyed their boundaries in isolation. This
one asks whether the frozen guarantees still compose correctly when 0–10 are
treated as one system. Method: nine passes over the actual code (not
documentation), adversarial timelines and chains, and the Vandor review's probes
as external inputs. Findings are classified P0–P4; "interesting future feature"
is deliberately separated from "architecture defect."

## Severity taxonomy

| Class | Meaning |
|---|---|
| P0 — invariant violation | a frozen guarantee is false |
| P1 — composition violation | individual guarantees hold, but composing them breaks one |
| P2 — unguarded semantic | correct behavior exists but is not contractually/test protected |
| P3 — mechanism gap | schema/design anticipates something not implemented |
| P4 — expansion opportunity | no guarantee is false; the system cannot answer a useful new question |

## Findings summary

| ID | Class | Sev | Summary | Status |
|---|---|---|---|---|
| COMP-1 | composition | P1 | `ActionRunner` executes through the raw Executor, not `InstrumentedExecutor` — the agency path emits no `action.requested` before execution, so a crashed action is observably invisible (AD-009 dropped for Phase 8) | OPEN |
| TIME-1 | temporal | P3 | `MemoryRecord.status` ("retired") exists but nothing sets, honors, or filters on it — retirement is a mechanism gap | OPEN (dormant) |
| EVENT-1 | event | P3 | `MODEL_SELECTED` is documented "reserved, not yet emitted" but is emitted in the runtime/router path; `MODEL_FALLBACK` is genuinely reserved | OPEN (minor) |
| COMP-2 | composition | P3 | learning evidence does not distinguish a logical action (`action_id`) from a physical retry (`call_id`) — latent amplification risk if learning ever auto-collects outcomes | OPEN (latent) |
| EXT-D | expansion | P4 | memory effectiveness (eligible → selected → delivered → followed) is unobservable | OPEN (opportunity) |

No P0 and no P2 were found. The two P2-class concerns surfaced by the Vandor
review are now CLOSED (Probe 1, pinned) and ARCHITECTURALLY COVERED (Probe 2,
needs an integration runbook, not a phase).

---

## Pass 1 — Authority graph

Constructed from code. Every path capable of externally meaningful change, and
the boundary that gates it:

| Mutation | Proposed by | Authorized by | Executed by | Freshness | Evidence |
|---|---|---|---|---|---|
| tool/action | model → `ActionRequest` | `ContinuityAuthority` (ALLOW) | `ActionRunner` → `Executor` | idempotency (`action_id`) + re-authorize | `action.completed` / `action.failed` |
| memory write | caller → `record()` | (event-sourced; no policy gate) | `MemoryStore.record` | `record_if_current` (adaptation only) | `memory.created` / `memory.updated` |
| adaptation | `EvidenceAnalyzer` → `LearningProposal` | `LearningAuthority` (ALLOW) | `AdaptationRunner` → `record_if_current` | atomic compare-and-append | `memory.updated` (new version) |
| approval | model → tool proposal | human → `ApprovalStore.approve` | orchestrator → `consume_approved` → `execute_tool` | policy re-checked on resume; single-use | `approval.*` |
| delegation send | caller → `DelegationSender` | A's `Authority` (ALLOW) | `DelegationSender` (mint request) | — | bounded `DelegationRequest` |
| delegation accept | `DelegationRequest` | B's `DelegationReceiver` | (verdict only) | — | `DelegationVerdict` |
| federated exec | delegation | B's `ActionRunner` (re-authorize) | B's `Executor` | re-authorize at execution | B `action.*` |
| federated receipt | `FederatedOutcome` (B's report) | (report, not authority) | `FederatedOutcomeRecorder` | idempotent + conflict-reject | `federation.outcome.received` |

**Adversarial shortcuts searched:** model→mutation, model→executor,
proposal→store, approval→executor-without-policy, learning-proposal→memory,
delegation→remote-executor, remote-output→local-mutation, projection→store.

**Result.** Every authoritative transition has an explicit authority boundary.
No proposer self-ratifies: the authority paths ignore `claims`/`requested_by`/
`proposed_by`/`provenance`. The one open path — `MemoryStore.record()` — is
event-sourced rather than policy-gated, and is only reachable by trusted
composition (the model has no `MemoryStore` reference; the sanctioned learning
write is separately gated through `AdaptationRunner`). That is a design choice,
not a violation. **AUTH: no finding.**

---

## Pass 2 — Truth / observation / provenance graph

The Phase 10 distinction is the anchor and it holds everywhere it is applied:

    B action.completed  ≠  A federation.outcome.received
    B observed execution  ≠  A observed "B reported execution"

Each derived object was audited for "what authoritative evidence permits this
statement":

- `Episode.outcome` — run.completed→success, run.failed→failed, neither→unknown (pinned; Probe 1).
- `RunState`/`TaskState`/`ApprovalState` — projections of `run.*`/`task.*`/`approval.*` events.
- `MemoryRecord`/`NCS` — projections of `memory.created`/`memory.updated`.
- `ActionResult` — B's own `action.completed` (B observed its own action).
- `FederatedOutcome` — B's report; A records only receipt.
- `ReplayReport`/fingerprint — semantic projection of events, never a guess.
- `RunRecord` fingerprint — terminal-only, NULL for a partial run.

**Provenance-laundering test** (the malicious-report chain): a fabricated
`remote_action_id` cannot be laundered into A's learning evidence because
`LearningAuthority._authentic` verifies action ids against A's *action* events —
and A has none for B's action. The chain is blocked at the authority, not merely
at the recorder. Proven by `tests/golden/test_phase10_composition.py`.

**Result.** No observation→report→evaluation→proposal→inference promotion was
found. The one consequence worth naming: **federation cannot currently be a
legitimate learning-evidence source** (the evidence model only recognizes local
action ids, not `federation.outcome.received`). That is conservative (safe), and
a P4 expansion, not a hole. **TRUTH: no violation.**

---

## Pass 3 — Temporal integrity / freshness / lifecycle

The independently correct temporal mechanisms — knowledge versions, memory
versions, NCS `as_of`, policy re-evaluation, action idempotency, learning
`target_version`, `record_if_current`, approval lifecycle, worker generations,
delegation acceptance-vs-execution — were tested against adversarial timelines.

- **Stale learning** — proposal targets v1; v2 appears; `record_if_current`
  rejects at the write boundary. ✅ proven (9.3).
- **Stale delegation** — B accepts; policy changes; execution re-authorizes and
  DENIES. ✅ proven (10.3).
- **Stale approval** — granted; policy changes; resume re-checks policy; single-use
  `consume_approved`. ✅ proven (4.5/5.1).
- **Historical continuity** — `NCS(t1) ≠ NCS(t2)` when memory versions differ. ✅.

**TIME-1 (P3).** `MemoryRecord.status` ("active / superseded / retired") exists
in the contract, but no `record()`/`_project()` sets it, no `retire()` exists, and
`list_as_of()` does not filter on it. A "retired" Procedure would keep projecting
into the NCS. This is a **mechanism gap**, dormant today (no corpus, nothing sets
non-"active" status), correctness-threatening only once a wrong Procedure needs to
retire but cannot. **Not a new phase; a 7.x hardening slice.** Severity is bounded
to memory/continuity.

**Acceptance bar met** except for the retirement gap: no permission, projection,
proposal, or version silently remains current after its dependent state changed.

---

## Pass 4 — Reconstruction and identity conservation

Every identity was inventoried for owner, namespace, minting boundary, lifetime,
and what it must not be conflated with. The critical distinctions all hold:

- `delegation_id ≠ action_id` — B mints its own action id; A never assigns one.
- A `action_id ≠` B `action_id` — separate authority domains.
- `run_id ≠ task_id`; `call_id ≠` logical `action_id` (per-attempt vs logical).
- `ModelIdentity ≠ AgentIdentity`; `peer/*` ≠ local `user/`·`agent/`·`model/`.

Reconstruction from a fresh process or authoritative inputs was verified for
`TaskState`, `RunState`, `ApprovalState`, `Episode` (deterministic projection),
`MemoryRecord` (event projection), `NCS`, `ActionResult` (`reconstruct_action`),
`AdaptationResult` (via memory provenance), `FederatedOutcome`
(`reconstruct_federated_outcome`), `Trace`, and `RunRecord`. No "called
authoritative but not reconstructible" object was found. **IDENT/RECON: no finding.**

---

## Pass 5 — Boundary / dependency / leakage

The actual dependency graph matches the intended authority shape:

    federation → core · learning → core · execution → core · memory → core
    control → core · knowledge → core · observability → core · integrations → core
    apps → anything

Enforced by `test_layer_boundaries.py` (no upward import) and
`test_no_provider_leakage.py` (no SDK below the adapter layers). No circular
dependency was introduced by 7–10. Capability possession (not merely imports) was
audited: `Executor` reaches only `ActionRunner`/`Orchestrator`/`InstrumentedExecutor`;
`MemoryStore` write reaches only `MemoryStore` itself and `AdaptationRunner`;
`LearningAuthority` reaches only `AdaptationRunner`; event emission reaches the
sanctioned emitters. No component receives a stronger object than it requires.
**BOUND: no finding.**

---

## Pass 6 — Event taxonomy and semantic completeness

All live event types were tabulated (see the reference table below) with producer,
evidence-of, reconstructs-into, terminal, and **absence-means**. The "absence"
column is the Vandor contribution; for most events the answer is *nothing*:

- no `run.completed` → not *failed* (→ `unknown`, → NULL fingerprint).
- no `action.completed` → not *denied* (→ refused or never executed).
- no `federation.outcome.received` → not *remote failure* (→ A never received a report).

**EVENT-1 (P3, minor).** `MODEL_SELECTED` is documented "RESERVED (not yet
emitted)" in `core/events.py`, but it is emitted in the runtime/router path;
`MODEL_FALLBACK` is genuinely reserved. Comment drift only — the taxonomy itself
is sound. No declared-but-unused, no emitted-but-unconsumed, no unpaired
correlation, no payload-stronger-than-producer events were found. **Acceptance
bar met** (one doc-drift finding).

---

## Pass 7 — Composition attacks

Six cross-phase chains were constructed. Two produced findings; the rest held.

1. **MODEL→ACTION→MEMORY** — held. Action output never reaches memory; only the
   gated adaptation path writes.
2. **MEMORY→ACTION** — held up to TIME-1: stale/wrong *preference* memory can
   influence authority because it cannot be retired. This is the retirement gap's
   real consequence, not a separate violation.
3. **ACTION→LEARNING→ACTION** — held. No automatic outcome→proposal path; the
   analyzer is explicit and the authority gates adaptation.
4. **FEDERATION→LEARNING** — held (TRUTH). Remote action ids cannot authenticate
   as local evidence. Proven by the new composition test.
5. **FEDERATION→ACTION** — held. Remote output never reaches A's `ActionRunner`.
6. **RECOVERY→ACTION→LEARNING** — **COMP-2 (P3, latent).** Phase 3 is at-least-once
   and mints a fresh `call_id` per physical attempt; Phase 8's `action_id` is the
   logical identity; Phase 9's evidence model verifies each cited action id is real
   but does not require "one logical action = one evidence vote." There is no
   automatic recovery→learning path today, so it is latent — but if learning ever
   auto-collects outcomes, physical retries could read as independent evidence
   unless the evidence model correlates to logical `action_id`.

**COMP-1 (P1).** `ActionRunner` (`execution/action.py`) calls
`self.executor.execute_tool(...)` directly and emits only the terminal
`action.completed`/`action.failed`. It does not wrap the Executor in
`InstrumentedExecutor`, so there is no `action.requested` before execution. A
crash mid-action therefore leaves **no event at all** — the action is observably
invisible, not "requested-but-not-completed." This drops AD-009 ("every observable
execution step emits an event *before* it is considered complete") for the Phase 8
execution path — the composition of Phase 8 over Phase 3 forgot Phase 3's
instrumentation. The fix is small (wrap the Executor in `InstrumentedExecutor`, or
emit `action.requested` before `execute_tool`), and it is a **hardening slice, not
a new phase**. The federated path (10.3) inherits the same gap via `DelegationService`.

**Acceptance bar met with one violation** (COMP-1): composition does not amplify
authority, evidence, or identity — but it does *drop* an observability guarantee.

---

## Pass 8 — External probes and known debts

- **Probe A — interrupted Episode.** Disposition: **CLOSED / GUARDED.** Outcome
  defaults to `unknown`; pinned by `test_phase2_memory.py` and documented.
- **Probe B — heterogeneous model handoff.** Disposition: **ARCHITECTURALLY
  COVERED / INTEGRATION EXPERIMENT OUTSTANDING.** NCS is model-neutral and provider
  vocabulary is confined to `ContextAdapter`. A runbook experiment (same
  `NCS(as_of=T)`, two real providers, assert boundary) is the next step — not a
  golden dependency.
- **Probe C — retirement.** Disposition: **REAL MECHANISM GAP / BOUNDED TO
  MEMORY-CONTINUITY / NOT A NEW PHASE** (= TIME-1).
- **Probe D — effectiveness.** Disposition: **CORRECTNESS UNAFFECTED /
  EFFECTIVENESS UNOBSERVABLE.** No instrument can answer "was memory eligible /
  selected / delivered / followed?" — the chain is only observable at its ends.
  This is the cleanest P4: a new question, not a new correctness hole.

---

## Pass 9 — Phase-boundary challenge

Every frontier candidate was forced through the gate — a candidate earns a phase
only if it (1) asks a crisp question 0–10 cannot answer, (2) requires a *new
architectural invariant*, not another implementation, (3) cannot be expressed as
hardening/composition, and (4) has a concrete acceptance test.

| Candidate | Can 0–10 answer by composition? | Missing primitive | Verdict |
|---|---|---|---|
| retirement / lifecycle | YES (7 hardening) | a `retire` transition + projection filtering | **7.x hardening** |
| memory effectiveness | NO — but a new *question*, not a new *guarantee* | eligible/selected/delivered/followed instrumentation | **unlocked intersection (4×7×8×9)** |
| autonomy | *undecided* — goal-persistence/stop-conditions may be genuinely new, or may be 3+8+7+9 extended in time | needs a crisp "unanswered question" before it earns a phase | **no phase; keep proposed** |
| real-provider continuity | YES (runbook) | none — tests the existing boundary | **runbook** |
| trust / security | deferred by design | authentication implements, not defines, federation | **after federation; not yet** |
| evolution / experimentation | partially (6+9) | promotion/rollback/A-B evaluation | **no phase; intersection** |
| distributed runtime | YES (5's natural expansion) | distributed persistence/ownership | **not yet; needs a real distributed need** |

**Frontier decision: no new phase.** The audit's only P1 (COMP-1) and its P3
(TIME-1) are *hardening* slices inside frozen phases, not new boundaries. The one
genuinely new *question* (effectiveness) is an observability axis, not a new
invariant — it may later earn a phase, but not now.

---

## Reference: authority + evidence matrix

| Thing | Proposed by | Authorized by | Caused by | Evidenced by |
|---|---|---|---|---|
| Action | model / caller | `Authority` | `ActionRunner` | `action.*` |
| Adaptation | `EvidenceAnalyzer` | `LearningAuthority` | `AdaptationRunner` | `memory.*` (new version) |
| Delegation send | caller | A `Authority` | `DelegationSender` | bounded request |
| Remote action | delegation | B `Authority` | B `ActionRunner` | B `action.*` |
| Federated fact | B report | — (report, not authority) | `FederatedOutcomeRecorder` | `federation.outcome.received` |
| Approval | model proposal | human | `consume_approved` → executor | `approval.*` |

## Reference: identity conservation

`user_id`/`agent_id`/`model_id` (identity) · `task_id`/`run_id`/`step_id`/`call_id`/
`claim_generation` (execution) · `approval_id`/`action_id`/`proposal_id`/`memory_id`
(agency/learning/memory) · `peer_id`/`delegation_id`/`remote_action_id` (federation).
Every namespace is distinct; cross-domain ids are never promoted into local ids.

## Reference: event taxonomy (abridged)

| Event | Producer | Evidence of | Reconstructs into | Terminal | Absence means |
|---|---|---|---|---|---|
| `run.completed` / `run.failed` | orchestrator | terminal run outcome | `RunState`, `Episode` | yes | `unknown` / NULL fingerprint (not *failed*) |
| `action.completed` / `action.failed` | `ActionRunner` | terminal action outcome | `ActionResult` | yes | refused / never executed (not *denied*) |
| `federation.outcome.received` | recorder (A) | A received a report | `FederatedOutcome` | per-delegation | no report received (not *remote failure*) |
| `memory.created` / `memory.updated` | `MemoryStore` | memory changed | `MemoryRecord`, `NCS` | per-version | no change |
| `approval.*` | `ApprovalStore` | authorization lifecycle | `ApprovalState` | consumed | pending |

*(The full taxonomy is enumerated in `core/events.py`; the above is the
audit-relevant subset.)*

---

## Conclusion — the freeze bar

From code and tests, Audit #3 can defend:

> **Across Phases 0–10, authority cannot be created by proposal, truth cannot be
> strengthened by projection or report, stale permission cannot survive the
> boundary where state changes, identities remain owned by their authority
> domains, and composing frozen guarantees does not create a bypass unavailable
> within any individual phase.**

The composition question is answered in the affirmative, with one exception
(COMP-1: the agency path drops AD-009's "event before complete" instrumentation).
That exception is a contained hardening fix, not an architectural flaw in the
composition model itself.

The second question — *what important question remains that no composition of
0–10 can answer?* — has one honest candidate: **memory effectiveness** (is
authoritative memory actually useful when behavior is produced?). It is a new
question, but it does not yet require a new invariant, so it remains an unlocked
intersection rather than Phase 11. The audit does not nominate a new phase, and
does not owe us one.
