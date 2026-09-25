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

# Run the entire suite (53 golden + conformance tests)
python scripts/check.py
```

```bash
python tests/golden/test_phase7_identity.py   # the model-swap invariant
```

---

## Status

**Phase 7.1 complete.** The three identity contracts are frozen in the `RunManifest`,
the model-swap invariant is proven, and the framework underneath remains untouched
(Phases 0–6 are documented in [`BLUEPRINT.md`](BLUEPRINT.md)).

Next on the arc: continuity projection (7.2), per-model context adapters (7.3),
model routing (7.4), and memory deepening (7.5).

Mnemosyne is the Titaness of Memory, mother of the Muses — every art and act of
reasoning flows from her. The reasoning engines are downstream; the memory and
identity is upstream and permanent.
