"""Supported composition root — one boundary that owns construction + lifecycle
(OBS-001, resolved as a Phase-4 surface — NOT a new phase).

The Operator Console + two operational sessions proved: the components already
compose, but every caller had to hand-wire the dependency graph (17+ objects, 4
store paths, a shared EventBus invariant, a shared MemoryStore invariant). This
module gives that wiring ONE supported owner.

    MnemosyneConfig  ->  CompositionRoot.build(config)  ->  MnemosyneSystem

Invariant: the composition root owns construction and lifecycle, NEVER authority.
It configures, instantiates, connects, starts, and closes existing frozen
components. It never authorizes, mutates authoritative memory directly, reinterprets
events, manufactures outcomes, or bypasses the Action/Learning/Federation
boundaries. `MnemosyneSystem.close()` writes nothing — shutdown is observably
neutral (an `action.requested` with no terminal survives close()/rebuild as
attempted/unknown, never a fabricated failure).

Non-goals (this slice): no new phase, no new authority, no new persistence
semantics, no new memory write path, no service locator / dependency dictionary,
no provider logic in continuity, no federation peer discovery, no config file
format, no `mnemosyne init`, no `mnemosyne serve`, no CLI business logic.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from core.contracts import (
    AgentIdentity, Capability, ModelIdentity, UserIdentity,
)
from control.authority import ContinuityAuthority
from control.policy import PolicyEngine, PolicyRules
from control.tools import ToolRegistry, ToolSpec
from execution.action import ActionRunner
from execution.approvals import ApprovalStore
from execution.durable import DurableEventBus
from execution.fake import FakeExecutor
from execution.queue import TaskQueue
from federation.outcome import FederatedOutcomeRecorder
from federation.peer import Federation
from knowledge.inmemory import ComposedRetriever
from learning.adaptation import AdaptationRunner
from learning.analyzer import EvidenceAnalyzer
from learning.authority import GroundedLearningAuthority
from memory.continuity import ContinuityProjector
from memory.memory import MemoryStore
from apps.console import create_console_app
from apps.runtime import NexusRuntime


@dataclass(frozen=True)
class CorpusEntry:
    """One knowledge document to ingest — data, never a constructed component."""
    source: str
    document: str
    version: str
    text: str


@dataclass(frozen=True)
class MnemosyneConfig:
    """Frozen configuration data — the operator's choices, never components.

    Contains only the choices `_ops_session*.py` forced the operator to make by
    hand. No MemoryStore, Authority, ActionRunner, or other constructed object
    lives here. Persistence is one `workspace` directory; the store paths are
    derived deterministically from it (Session #2's four manual path decisions
    collapse to one).
    """
    workspace: str
    user: UserIdentity = field(default_factory=UserIdentity)
    agent: AgentIdentity = field(default_factory=AgentIdentity)
    model: ModelIdentity = field(default_factory=ModelIdentity)
    # deterministic model responses (the "model/provider selection" as DATA, fed
    # to the reference deterministic Executor — a real provider is a later executor
    # behind the same protocol)
    model_script: tuple = ()
    tools: tuple = ()      # ToolSpec definitions (name/description/risk/parameters)
    corpus: tuple = ()     # CorpusEntry documents to ingest
    policy: PolicyRules = field(default_factory=PolicyRules)
    worker_id: str = "worker-1"
    max_replans: int = 2


class MnemosyneSystem:
    """The constructed system — one object owns the graph and its lifecycle.

    Public members are the canonical topology (the Operator Console's diagram);
    the truly internal wiring (retriever/tools/policy/executor/bus/queue/approvals)
    is owned but not advertised, so the root is not a service locator."""

    def __init__(self, *, config: MnemosyneConfig, runtime, bus, queue, approvals,
                 memory, retriever, tools, policy, executor, authority, runner,
                 projector, analyzer, learning_authority, adaptation,
                 federation, federated_recorder, console_app) -> None:
        self.config = config
        # canonical public members
        self.runtime = runtime
        self.memory = memory
        self.projector = projector
        self.authority = authority
        self.runner = runner
        self.analyzer = analyzer
        self.learning_authority = learning_authority
        self.adaptation = adaptation
        self.federation = federation
        self.federated_recorder = federated_recorder
        self.console_app = console_app
        # owned internals (closed by close(); not part of the public surface)
        self._bus = bus
        self._queue = queue
        self._approvals = approvals
        self._retriever = retriever
        self._tools = tools
        self._policy = policy
        self._executor = executor
        self._closed = False

    @property
    def identities(self):
        return (self.config.user, self.config.agent, self.config.model)

    def close(self) -> None:
        """Close everything the root owns. Idempotent. Writes nothing — closing
        never fabricates a terminal event or mutates authoritative state."""
        if self._closed:
            return
        self._closed = True
        self._queue.close()
        self._approvals.close()
        self._bus.close()
        self.memory.close()


class CompositionRoot:
    """Constructs the canonical supported Mnemosyne topology from explicit config.

    A concrete class, not a protocol (no interface for symmetry's sake). It may
    construct/configure/connect; it never authorizes, mutates, or bypasses a
    frozen boundary."""

    def build(self, config: MnemosyneConfig) -> MnemosyneSystem:
        os.makedirs(config.workspace, exist_ok=True)
        events_path = os.path.join(config.workspace, "events.db")
        queue_path = os.path.join(config.workspace, "queue.db")
        approvals_path = os.path.join(config.workspace, "approvals.db")
        memory_path = os.path.join(config.workspace, "memory.db")

        # persistence (one EventBus shared by every consumer — the shared-state invariant)
        bus = DurableEventBus(events_path)
        queue = TaskQueue(queue_path, bus=bus)
        approvals = ApprovalStore(approvals_path, bus=bus)
        memory = MemoryStore(memory_path)

        # knowledge
        retriever = ComposedRetriever()
        for entry in config.corpus:
            retriever.ingest(entry.source, entry.document, entry.version, entry.text)

        # control (policy + tool registry; capabilities derived from the same tools)
        policy = PolicyEngine(config.policy)
        tools = ToolRegistry()
        for spec in config.tools:
            tools.register(spec)
        capabilities = [Capability(name=t.name, description=t.description,
                                   risk=t.risk, parameters=t.parameters)
                        for t in config.tools]

        # execution (the deterministic reference Executor — a real provider is a
        # later executor behind the same protocol; no provider logic enters here)
        executor = FakeExecutor(model_script=list(config.model_script))
        runtime = NexusRuntime(retriever=retriever, executor=executor, queue=queue,
                               event_bus=bus, tools=tools, approvals=approvals,
                               policy=policy, worker_id=config.worker_id,
                               max_replans=config.max_replans)

        # agency (authority decides; runner executes — never a bypass)
        authority = ContinuityAuthority(capabilities, policy)
        runner = ActionRunner(authority, executor, bus)

        # continuity (read-side projection of identity + memory)
        projector = ContinuityProjector()

        # learning (analyze -> authority -> adapt; the only write path)
        analyzer = EvidenceAnalyzer()
        learning_authority = GroundedLearningAuthority(events=lambda: bus.load_events())
        adaptation = AdaptationRunner(learning_authority, memory)

        # federation (available, NOT activated: no peer discovery, no network)
        federation = Federation(protocol_version=1)
        federated_recorder = FederatedOutcomeRecorder(bus)

        # the Operator Console observes the SAME runtime/event/memory world
        console_app = create_console_app(
            runtime, memory_store=memory, user=config.user, agent=config.agent,
            model=config.model, projector=projector)

        return MnemosyneSystem(
            config=config, runtime=runtime, bus=bus, queue=queue, approvals=approvals,
            memory=memory, retriever=retriever, tools=tools, policy=policy,
            executor=executor, authority=authority, runner=runner,
            projector=projector, analyzer=analyzer,
            learning_authority=learning_authority, adaptation=adaptation,
            federation=federation, federated_recorder=federated_recorder,
            console_app=console_app)
