# Mnemosyne

**Mnemosyne is the identity, continuity, and memory layer for AI agents — built on the [Nexus](https://github.com/Sergey-Nikitenko/nexus) framework.**

The governing principle:

> **Nexus owns identity, memory, knowledge, continuity, and state. Models are pluggable reasoning backends.**

The model is a replaceable reasoning engine. Who you are, which agent role is
acting, what you remember, and the decisions you've made do not live inside GPT or
Claude — they live in Mnemosyne. Swap the model, and nothing is forgotten, because
the model never owned the memory in the first place.

---

## The thesis, in one picture

```
                MNEMOSYNE
┌─────────────────────────────────────────────┐
│  UserIdentity    — who                       │
│  AgentIdentity   — which role                │
│  ModelIdentity   — which engine              │
│  + continuity, memory, decisions, projects   │
└──────────────────────┬──────────────────────┘
                       │
              ┌────────┴────────┐
              ▼                 ▼
          MODEL A            MODEL B
          GPT/etc.           Claude/etc.
              │                 │
              └────────┬────────┘
                       ▼
                (shared Nexus state)
```

The model changes; the underlying identity doesn't.

---

## What Mnemosyne adds on top of Nexus

Nexus (the framework, vendored in this repo) is the observable execution system —
contracts, policy, durable execution, recovery, reproducibility, arbitration, MCP
interop, multi-agent composition. Mnemosyne is the **persistent identity +
continuity** plane on top of it.

**Phase 7.1 — identity contracts** (the foundation slice, now frozen):

- `UserIdentity` — who is interacting (`user/alice`), no auth/provider fields.
- `AgentIdentity` — which agent role is acting (`agent/researcher@1`), distinct
  from worker/task/run.
- `ModelIdentity` — which logical model config participated
  (`model/fake-deterministic@1`), **never** the API key, endpoint, or SDK object.

The invariant that holds the whole thing together: **a model swap changes only
`ModelIdentity` — the user and agent are untouched.** That is what makes "swap GPT
for Claude" a configuration change instead of an amnesia event.

---

## Quick start

```bash
# Python 3.12+
pip install -r requirements.txt

# Run the entire suite (71 golden + conformance tests)
python scripts/check.py
```

```bash
python tests/golden/test_phase7_identity.py   # the model-swap invariant
python tests/golden/test_phase7_memory.py     # event-sourced memory taxonomy
python tests/golden/test_phase7_continuity.py # as-of continuity projection
python tests/golden/test_phase7_context_adapter.py  # the NCS -> request translation boundary
python tests/golden/test_phase7_handoff.py          # model handoff: continuity survives a model swap
python tests/golden/test_phase8_capability.py       # authority: the model proposes, Nexus decides
python tests/golden/test_phase8_execution.py        # controlled execution: ALLOW runs, DENY/APPROVAL never execute
python tests/golden/test_phase8_lifecycle.py        # lifecycle/idempotency: same action runs at most once
python tests/golden/test_phase9_learning_proposal.py # learning proposals: observe, never mutate
python tests/golden/test_phase9_learning_authority.py # learning authority: ALLOW is permission, not mutation
python tests/golden/test_phase9_adaptation.py         # authorized adaptation: compare-and-append, exactly-once
python tests/golden/test_phase10_federation_peer.py    # federation identity: representation before trust
python tests/golden/test_phase10_delegation.py         # federated delegation: a bounded request, never authority
python tests/golden/test_phase10_federated_outcome.py   # federated outcome: A records "B reported X", never "A observed X"
```

### Serve the Operator Console

One supported command launches and owns the whole system — no Python imports, no
hand-wired components:

```bash
python -m apps.serve serve \
    --workspace ./workspace \
    --user-id alice \
    --agent-id mnemosyne \
    --model-id fake
```

1. This builds the canonical system from explicit configuration and serves the
   Operator Console at `http://127.0.0.1:8000`.
2. Open `http://127.0.0.1:8000` to inspect identities, NCS, approvals, events, and
   action lifecycles.
3. **Ctrl+C** stops the server and closes every store it owns (no terminal event
   is fabricated).
4. Run the **same command** again to reconstruct the same authoritative workspace.
5. `python -m apps.serve --help` lists every flag a new operator needs.

The serve command is a disposable lifecycle surface — it translates configuration
into `MnemosyneConfig`, delegates construction to the composition root, and serves
the existing console. Deleting it changes no Mnemosyne semantic.

---

## Operator Console (projection-only)

A Phase-4 surface (`apps/console.py` + `apps/static/console.html`), not a new
phase: it composes the same `NexusRuntime` and projects identities, the NCS
summary, pending approvals, the raw event log, and the action lifecycle —
read-only, over the same events and memory store every other surface shares.

> **The Operator Console is a projection of authoritative Mnemosyne state. It may
> request operations and display evidence; it never defines truth, authority,
> identity, or continuity.**

The only write path is the authorized one (`UI → API → authorized path →
transition → event → UI updates`). An interrupted action (`action.requested` with
no terminal event) renders *attempted / unknown* — never a red FAILED badge.
`tests/conformance/test_console_surface.py` proves it is projection-only and that
REST, CLI, and raw state agree on the same authoritative task status.

---

## Status

**Phase 7 complete.** Identity (7.1), memory (7.2), continuity (7.3), context
adaptation (7.4), and model handoff (7.5) are frozen: `UserIdentity` /
`AgentIdentity` / `ModelIdentity`, event-sourced `Procedure` / `SemanticMemory` /
`Preference`, a `ContinuityProjector` that reconstructs the world as of a point in
time, a `ContextAdapter` that translates that model-neutral continuity state into
a provider/model-specific request, and the handoff proof that a model change never
requires the previous model's private context — deterministically, read-only, and
without provider vocabulary leaking backward (Phases 0–6 are the Nexus framework,
documented in [`BLUEPRINT.md`](BLUEPRINT.md)).

Phase 7 is the proof milestone for Mnemosyne's central claim: Nexus owns identity,
memory, continuity, and state; models are pluggable reasoning backends.

**Phase 8 complete (frozen).** The capability/agency plane: a model proposes an
`ActionRequest`; a continuity-aware `ContinuityAuthority` returns an `ActionVerdict`
(8.1); an `ActionRunner` executes an ALLOWed action through the existing `Executor`
and records `action.completed` / `action.failed` (8.2); and the `action_id` is the
idempotency key — the same logical action executes at most once, and a failed
action is authoritative, never auto-retried (8.3). The invariant: *models may
propose actions; Nexus alone authorizes, executes, records, and reconstructs
their authoritative outcomes — a logical action has a Nexus-owned identity, and
its history is never rewritten.*

**Phase 9 complete (frozen).** The learning plane: a `LearningAnalyzer` produces a
`LearningProposal` (9.1); a `GroundedLearningAuthority` returns a `LearningVerdict`
(9.2); and an `AdaptationRunner` applies a currently-ALLOWed proposal as exactly
one new authoritative memory version via an atomic, in-store compare-and-append
(9.3). The invariant: *adaptation is an authorized, compare-and-append transition
— stale authorization never mutates state, and history is never rewritten.*

**Phase 10.1 shipped.** Federation begins with representation before trust: a
remote Nexus may declare identity, protocol version, and capability claims, and a
`Federation` component represents it as a bounded, namespaced `FederationPeer`.
Those claims never become local authority or shared state; protocol compatibility
is checked explicitly; no crypto, no transport dependency.

**Phase 10.2 shipped.** Federated delegation: a `DelegationSender` authorizes
sending via A's own authority; a `DelegationReceiver` independently accepts or
refuses via B's own authority. A `DelegationVerdict.ALLOW` means "accepted in
principle" — nothing executes, no shared state, no authority transit.

**Phase 10 complete (frozen).** Federated outcome closes the loop: B executes an
accepted delegation through its own Phase 8 machinery and reports a bounded
`FederatedOutcome`; A records only `federation.outcome.received` — "B reported X",
never "A observed X". The invariant: *federation connects independent authority
domains without merging them — identity, authorization, execution, state, and
authoritative history remain locally owned.*

Mnemosyne is the Titaness of Memory, mother of the Muses — every art and act of
reasoning flows from her. The reasoning engines are downstream; the memory and
identity is upstream and permanent.
