# Operator Console — frontend

> **The Operator Console is a projection of authoritative Mnemosyne state. It may request operations and display evidence; it never defines truth, authority, identity, or continuity.**

`console.html` is the single-page operator UI. It has **no business logic and no
direct store access**: every figure it shows is fetched from an `apps/console.py`
endpoint that projects authoritative Mnemosyne state (identities, NCS, approvals,
events, action lifecycle). The only write path is the authorized one:

```
UI -> API -> authorized path -> transition -> event -> UI updates
```

The console never edits the memory store, the event log, or any database. There
is no endpoint here that mutates memory or state directly; the frontend may only
*request* operations (submit a task, approve/deny a pending approval) and then
*display the resulting evidence* from the event stream.

## Areas

| Area | Source | Behavior |
| --- | --- | --- |
| Submit task / request | `POST /ask` | requests a task; shows the returned `task_id` |
| Run/task status | `GET /tasks/{id}` | the `TaskState` projection |
| Identities | `GET /api/identities` | User / Agent / Model — stable identities, never provider config |
| Continuity / NCS | `GET /api/ncs` | the `ContinuityProjector` projection as-of now (memory summary) |
| Proposed actions / approvals | `GET /api/approvals` + `POST /approvals/{id}/approve\|deny` | projected `approval.*` lifecycle; approve/deny command the authorized path |
| Action lifecycle | `GET /api/actions/{id}` | `action_attempted` + `reconstruct_action` |
| Authoritative event stream | `WS /ws/events` (replay + tail) | the durable event log, live |
| Raw trace / log | `GET /traces/{run_id}` | raw event history |

## The interrupted-action rule

An action with an `action.requested` event but no terminal event is rendered as
**"NO TERMINAL EVENT / Outcome: UNKNOWN / Attempted: YES"** — an amber
*attempted / unknown* badge. It is **never** rendered as a red FAILED badge,
because the absence of a terminal event is an unknown outcome, not an observed
failure (AD-050).

## Reconnect / reconstruct

The **Reconstruct** button (and every reconnect) re-fetches each read-only
projection from authoritative state and re-opens the live stream. The console
holds no state of its own: a browser refresh or a server restart reconstructs the
exact same view from the same durable events + memory store.
