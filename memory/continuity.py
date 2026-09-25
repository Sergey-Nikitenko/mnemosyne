"""ContinuityProjector — reconstruct the world as of a point in time (AD-037).

A read model over authoritative state. It does NOT create a store, does NOT call
a model, does NOT mutate anything: given identity + an event-sourced memory store
+ an explicit `as_of`, it projects a deterministic `NexusContinuityState` (NCS).

    authoritative state + as_of  ->  ContinuityProjector  ->  NCS

The NCS is model-neutral. A later context adapter (7.4) translates it into a
model-specific `ModelRequest`; the projector never knows what model will receive
it. That is what makes continuity independent of the model: the same NCS feeds a
GPT adapter, a local adapter, or any future adapter.
"""
from __future__ import annotations

from core.contracts import NexusContinuityState


class ContinuityProjector:
    """Pure, stateless: reconstructs the NCS as-of a point in time."""

    def project(self, as_of, memory_store, *, user, agent, model) -> NexusContinuityState:
        """Reconstruct identity + event-sourced memory as of `as_of`.

        `memory_store` needs `list_as_of(kind, as_of)` (the reference
        MemoryStore provides it). Identity is supplied explicitly — it is the
        caller's current identity, not something the projector derives."""
        return NexusContinuityState(
            as_of=as_of,
            user=user,
            agent=agent,
            model=model,
            procedures=memory_store.list_as_of("procedure", as_of),
            semantic_memories=memory_store.list_as_of("semantic", as_of),
            preferences=memory_store.list_as_of("preference", as_of),
        )
