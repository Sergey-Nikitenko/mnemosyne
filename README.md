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

# Run the entire suite (60 golden + conformance tests)
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
```

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

**Phase 8.3 shipped.** The capability/agency plane now has a full lifecycle:
a model proposes an `ActionRequest`; a continuity-aware `ContinuityAuthority`
returns an `ActionVerdict` (8.1); an `ActionRunner` executes an ALLOWed action
through the existing `Executor` and records `action.completed` / `action.failed`
(8.2); and the `action_id` is the idempotency key — the same logical action
executes at most once, and a failed action is authoritative, never auto-retried
(8.3).

Mnemosyne is the Titaness of Memory, mother of the Muses — every art and act of
reasoning flows from her. The reasoning engines are downstream; the memory and
identity is upstream and permanent.
