"""Nexus core contracts.

These are the boring, stable objects every component communicates through.
Deliberately stdlib-only (dataclasses) so the contracts have zero dependencies;
the API layer can adopt the same shapes with Pydantic later without touching them.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol


def new_id(prefix: str) -> str:
    """Uniform opaque id. Keep it boring."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PASS = "pass"
    FAIL = "fail"


class TaskStatus(str, Enum):
    QUEUED = "queued"
    CLAIMED = "claimed"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    DONE = "done"
    FAILED = "failed"


class Risk(str, Enum):
    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"


class PolicyVerdict(str, Enum):
    """A policy decision's outcome — a contract, like `Decision`, that crosses the
    control -> execution boundary. The policy ENGINE lives in control/; this enum
    is what the execution plane consumes."""
    ALLOW = "allow"
    DENY = "deny"
    APPROVAL_REQUIRED = "approval_required"


# --------------------------------------------------------------------------
# Core objects
# --------------------------------------------------------------------------

@dataclass
class Task:
    task_id: str
    title: str
    created_at: datetime = field(default_factory=utcnow)
    status: TaskStatus = TaskStatus.QUEUED
    run_ids: list[str] = field(default_factory=list)
    user: str = ""              # user_id — who submitted this task (AD-035)
    agent: str = ""             # agent_id — which ROLE this task belongs to (AD-034)
    parent_run_id: str = ""     # the delegating run, if this task was delegated


@dataclass
class Claim:
    """A worker's exclusive lease on ONE claim-generation of a task.

    Ownership is generation-specific, not merely worker-specific (AD-028): a
    worker may legitimately re-claim a later generation, so a terminal transition
    must prove it holds the exact (worker_id, generation) it acquired — not just
    that it is "some worker" named by worker_id.
    """
    task: Task
    worker_id: str
    generation: int


class ReplayStatus(str, Enum):
    """A run's reproducibility, DERIVED from explicit conditions — never a
    subjective assessment (AD-029)."""
    REPRODUCIBLE = "reproducible"
    REPRODUCIBLE_WITH_DIFFERENCES = "reproducible_with_differences"
    NON_REPRODUCIBLE = "non_reproducible"


@dataclass
class UserIdentity:
    """Who is interacting with Nexus — a stable logical identity (AD-035).

    No authentication or provider-specific fields: an API key, OAuth token,
    endpoint URL, or SDK object is a secret/config, never identity."""
    user_id: str = ""

    @property
    def key(self) -> str:
        return f"user/{self.user_id}"


@dataclass
class AgentIdentity:
    """Which agent ROLE is acting — a stable role/configuration identity (AD-035).

    Distinct from worker_id (who ran it), task_id (which task), run_id (which
    attempt), and ModelIdentity (which engine). The agent survives model
    replacement."""
    agent_id: str = ""
    role: str = ""
    version: str = "1"

    @property
    def key(self) -> str:
        return f"agent/{self.agent_id}@{self.version}"


@dataclass
class ModelIdentity:
    """Which logical model configuration participated (AD-035).

    Deliberately NOT the provider configuration — an API key, endpoint URL, SDK
    object, or instantiated client is an implementation detail, never identity.
    Equality is on (model_id, family, version) only, so a model swap changes the
    identity while the concrete SDK object behind it is irrelevant."""
    model_id: str = "unknown"
    family: str = ""
    version: str = "1"

    @property
    def key(self) -> str:
        return f"model/{self.model_id}@{self.version}"


@dataclass
class RunManifest:
    """The inputs that define a run, captured BEFORE execution (AD-029).

    The manifest answers "what configuration/snapshots defined this run?" —
    distinct from the event log, which answers "what actually happened?". Both
    together give reproducibility; the events alone do not. Every field is Nexus
    vocabulary — never a provider object or SDK config.
    """
    run_id: str
    task_id: str
    task_title: str
    knowledge: str      # knowledge snapshot identity/version
    policy: str         # policy identity/version
    model: ModelIdentity  # which logical model configuration participated
    tools: str          # tool registry identity/version
    router: str = ""    # router configuration (reserved until model selection)
    max_replans: int = 2
    user: UserIdentity = field(default_factory=UserIdentity)     # who (AD-035)
    agent: AgentIdentity = field(default_factory=AgentIdentity)  # which role (AD-034/035)
    parent_run_id: str = ""     # the delegating run (empty = top-level)


@dataclass
class ReplayReport:
    """The outcome of replaying a run against its manifest.

    `status` is DERIVED: REPRODUCIBLE iff the inputs match AND the semantic
    traces match; REPRODUCIBLE_WITH_DIFFERENCES iff an input changed (or the
    traces diverged); NON_REPRODUCIBLE iff a required input is missing.
    """
    status: ReplayStatus
    input_differences: list[tuple[str, str, str]] = field(default_factory=list)
    first_divergent_event: str | None = None
    event_count: tuple[int, int] = (0, 0)


@dataclass
class Step:
    step_id: str
    name: str
    status: StepStatus = StepStatus.PENDING
    result: Any = None


@dataclass
class Run:
    run_id: str
    task_id: str
    created_at: datetime = field(default_factory=utcnow)
    steps: list[Step] = field(default_factory=list)


@dataclass
class Decision:
    """The router's answer — model + provider + WHY + constraints."""
    model: str
    provider: str
    reasons: list[str] = field(default_factory=list)
    constraints: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolCall:
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str | None = None
    call_id: str = field(default_factory=lambda: new_id("call"))
    # Model-neutral correlation identity: links a tool result back to the model
    # tool invocation that caused it, across a translation boundary. A provider's
    # own tool-call id is preserved HERE (as data), never as a provider field name.
    correlation_id: str = ""


@dataclass
class ToolResult:
    tool_call: ToolCall
    success: bool
    output: Any = None
    error: str | None = None


@dataclass
class ToolDefinition:
    """A discovered tool's Nexus-level definition — what an adapter produces and
    the registry consumes. Carries no provider objects (no MCP/OpenAI shapes)."""
    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)


@dataclass
class RetrievedChunk:
    """A retrieved chunk with full provenance, so a consumer never reaches back
    into the vector store to answer "where did this come from?"."""
    text: str
    source: str            # file path / URL / repo
    document: str          # parent document id or title
    location: str = ""     # section / line range within the document
    version: str = ""      # commit / version stamp
    relevance: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RetrievalResult:
    query: str
    chunks: list[RetrievedChunk] = field(default_factory=list)


@dataclass
class Episode:
    """One completed run, distilled into a retrievable memory record.

    Memory is the SEQUENCE plane — "what did we attempt, and how did it end" —
    as distinct from knowledge (the CONTENT plane: chunks of documents). An
    Episode is a deterministic projection of a run, not a raw dump of its event
    log: a future agent retrieves it to answer "has this been tried before?"
    """
    episode_id: str
    task_id: str
    summary: str
    outcome: str                      # "success" | "failed" | "unknown"
    relevant_entities: list[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=utcnow)
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass
class MemoryRecord:
    """Common envelope for durable, event-sourced memory (AD-036).

    A memory record is a PROJECTION of authoritative memory events — it is never
    a directly mutable row. The model (or any caller) can propose a change; only
    a memory event changes authoritative state. Carries a stable identity, an
    explicit version, provenance back to its source event, and a lifecycle."""
    memory_id: str = ""
    version: int = 1
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)
    status: str = "active"            # active | superseded | retired
    scope: str = ""                   # ownership: user_id / agent_id / project_id
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass
class Procedure(MemoryRecord):
    """Procedural memory — "how do we do this?" (a durable, versioned workflow)."""
    name: str = ""
    steps: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)


@dataclass
class SemanticMemory(MemoryRecord):
    """Semantic memory — "what stable fact do we know?" (subject/predicate/object)."""
    subject: str = ""
    predicate: str = ""
    object: str = ""


@dataclass
class Preference(MemoryRecord):
    """Preference memory — what the user or agent prefers."""
    key: str = ""
    value: str = ""


@dataclass
class NexusContinuityState:
    """The world as of a point in time — a model-neutral continuity bundle (AD-037).

    A read-side PROJECTION of authoritative state (identity + event-sourced
    memory), never a stored record. `as_of` names the point in time the world is
    reconstructed to, so a new session can resume "what was in force then", not
    merely "what is current now". Deterministic: the same inputs yield the same
    bundle.
    """
    as_of: datetime = field(default_factory=utcnow)
    user: UserIdentity = field(default_factory=UserIdentity)
    agent: AgentIdentity = field(default_factory=AgentIdentity)
    model: ModelIdentity = field(default_factory=ModelIdentity)
    procedures: list[Procedure] = field(default_factory=list)
    semantic_memories: list[SemanticMemory] = field(default_factory=list)
    preferences: list[Preference] = field(default_factory=list)


@dataclass(frozen=True)
class ContextRequest:
    """The provider/model-specific request a ContextAdapter produces (AD-038).

    The acceptance bar calls this "ModelRequest"; in code the name `ModelRequest`
    is already the execution-plane request (Phase 3.3), so the adapter's output
    is named `ContextRequest` to keep the two boundaries unambiguous.

    `provider` + `body` carry the provider-specific vocabulary ("messages",
    "temperature", "tools" for an OpenAI-flavored adapter; "prompt",
    "max_new_tokens" for a local-model adapter). That vocabulary exists ONLY here
    and in the adapter — never in the NexusContinuityState, which stays
    model-neutral. `model` is the target model key, translated (not selected)
    from the NCS's ModelIdentity.
    """
    provider: str                    # adapter-declared family: "openai" / "local" / ...
    model: str                       # target model key, translated from NCS.model
    body: dict[str, Any] = field(default_factory=dict)  # provider-specific payload


class ContextAdapter(Protocol):
    """The translation boundary: model-neutral NCS -> model-specific request.

    A ContextAdapter is a pure function of (ncs, configuration): it translates
    continuity into a provider/model-specific ContextRequest and returns it. It
    must NOT retrieve or mutate memory, access a MemoryStore, decide identity,
    select a model, invoke a model, call MCP, touch a provider SDK or the event
    store, or modify the NCS. The same NCS + the same adapter configuration
    always yields the same request.
    """

    def adapt(self, ncs: NexusContinuityState) -> ContextRequest: ...


@dataclass
class Capability:
    """A declared capability — an agency-level operation Nexus may perform (AD-040).

    Distinct from the execution-layer `ToolSpec`: a Capability is what a MODEL
    proposes by name; Nexus owns whether and how it executes. `risk` feeds the
    static policy gate; `parameters` is the declared surface."""
    name: str
    description: str = ""
    risk: Risk = Risk.READ
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass
class ActionRequest:
    """A model's PROPOSAL to invoke a capability (AD-040 / AD-042).

    The model proposes; it never authorizes. `action_id` is the identity of the
    LOGICAL action — the idempotency key (distinct from run/task/provider ids):
    the same id is the same logical action and executes at most once; a new id is
    a new action. Empty means "mint a fresh identity". `requested_by` is the agent
    ROLE (which survives model replacement — the model is only the backend).
    `claims` is carried but ignored by the authority: a model cannot assert its
    way into permission. `scope` is the project/domain the action targets."""
    capability: str
    action_id: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    requested_by: AgentIdentity = field(default_factory=AgentIdentity)
    scope: str = ""
    claims: list[str] = field(default_factory=list)


@dataclass
class ActionVerdict:
    """The authority's answer to an ActionRequest (AD-040).

    `verdict` reuses PolicyVerdict (ALLOW / DENY / APPROVAL_REQUIRED); `reasons`
    records WHY — the static risk class plus any continuity refinement. The
    authority returns this and causes nothing."""
    verdict: PolicyVerdict
    reasons: list[str] = field(default_factory=list)


class Authority(Protocol):
    """The authority boundary: (ActionRequest, NCS) -> ActionVerdict (AD-040).

    A pure, deterministic, model-independent, continuity-aware decision. It must
    never execute, never emit, never read the proposing model's identity, and
    never honor the model's self-assertions — only (proposal, continuity state,
    policy) are inputs.
    """

    def evaluate(self, action: ActionRequest, ncs: NexusContinuityState) -> ActionVerdict: ...


@dataclass
class ActionResult:
    """What happened when an allowed action executed (AD-041).

    The execution half of the agency boundary — distinct from ActionVerdict
    ("may this happen?"). It carries the outcome (success/output/error) plus the
    provenance needed to reconstruct the completion from the event log. The
    `action.completed` event is this object's authoritative record."""
    action_id: str
    capability: str
    success: bool
    output: Any = None
    error: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)
    requested_by: str = ""      # agent key
    scope: str = ""
    run_id: str = ""
    task_id: str = ""
    completed_at: str = ""      # isoformat timestamp (rides the event)


@dataclass
class LearningProposal:
    """A proposed adaptation, derived from authoritative experience (AD-044).

    A PROPOSAL, never a mutation: it writes nothing, changes no authoritative
    memory/identity/policy/history, and never appears in the NCS as learned
    knowledge. `kind` is the MemoryStore kind ("procedure" / "semantic" /
    "preference") — no learning-specific enum. `target` + `target_version` name
    the EXACT authoritative version observed (a later version does not retarget
    it). `evidence` references Nexus-owned artifacts (action_id, run_id, memory
    version) — not a copied transcript. `proposed_by` is the interpreter (a model
    or agent), never the authority."""
    kind: str
    target: str
    target_version: int
    proposed_change: dict[str, Any]
    evidence: list[dict[str, Any]]
    scope: str = ""
    proposed_by: str = ""
    proposal_id: str = field(default_factory=lambda: new_id("lrn"))
    created_at: datetime = field(default_factory=utcnow)


class LearningAnalyzer(Protocol):
    """The learner boundary: authoritative experience -> LearningProposal (AD-044).

    A pure, replaceable interpreter. It OBSERVES authoritative experience
    (evidence references) and produces a bounded candidate adaptation — a value
    object, never authoritative state. It never reads or writes a MemoryStore,
    never mutates the NCS, never emits.
    """

    def analyze(self, *, kind: str, target: str, target_version: int,
                evidence: list[dict[str, Any]], scope: str = "",
                proposed_by: str = "") -> LearningProposal: ...


@dataclass
class LearningVerdict:
    """The learning authority's answer to a LearningProposal (AD-045).

    `verdict` reuses PolicyVerdict (ALLOW / DENY / APPROVAL_REQUIRED); `reasons`
    records WHY — evidence authenticity, target freshness, scope, policy kind.
    The authority returns this and mutates nothing: ALLOW is permission, never
    adaptation."""
    verdict: PolicyVerdict
    reasons: list[str] = field(default_factory=list)


class LearningAuthority(Protocol):
    """The learning-authority boundary: (LearningProposal, NCS) -> LearningVerdict (AD-045).

    A pure, deterministic decision. It evaluates a proposal's evidence (against
    authoritative history), target freshness, and scope — never the proposer's
    identity or the proposal's self-claims — and returns a verdict. It never
    mutates: an ALLOW verdict is permission, not adaptation.
    """

    def evaluate(self, proposal: LearningProposal, ncs: NexusContinuityState) -> LearningVerdict: ...


@dataclass
class AdaptationResult:
    """The authoritative outcome of an authorized adaptation (AD-046).

    "What authoritative adaptation happened?" — the Phase 9 twin of the Phase 8
    ActionVerdict vs ActionResult split. One proposal produces at most one
    AdaptationResult: retrying returns the existing result, never a second
    version. `provenance` carries bounded references (proposal + evidence), never
    a copied transcript."""
    proposal_id: str
    memory_id: str
    previous_version: int
    new_version: int
    kind: str = ""
    scope: str = ""
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FederationPeer:
    """A local, bounded representation of another independent Nexus authority
    domain (AD-047).

    Distinct from AgentIdentity (an agent lives INSIDE a domain) and ModelIdentity
    (a reasoning backend): a peer IS another authoritative system. `peer_id` is
    THIS Nexus's stable, namespaced identity for the remote domain — derived
    locally, never the remote's arbitrary self-description. `capabilities` are
    CLAIMS (strings), never local authority; `metadata` is remote self-description,
    never consulted by local authority; `endpoint` is an opaque, transport-
    independent reference."""
    peer_id: str
    protocol_version: int
    capabilities: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    endpoint: str = ""


@dataclass
class DelegationRequest:
    """A bounded cross-domain request (AD-048). One authority domain asks another
    to CONSIDER performing something — never authority itself. `delegation_id` is
    the requesting domain's identity for the logical delegation (never a run/task/
    action id; the receiver mints its own action identity later). `requested_by`
    and `provenance` are bounded task context and self-claims — the latter is
    never consulted by the receiver."""
    delegation_id: str
    peer_id: str
    capability: str
    parameters: dict[str, Any] = field(default_factory=dict)
    scope: str = ""
    requested_by: str = ""
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass
class DelegationVerdict:
    """The receiving domain's decision about a delegation (AD-048).

    `verdict` reuses PolicyVerdict (ALLOW/DENY/APPROVAL_REQUIRED). ALLOW means the
    receiver ACCEPTS the delegation as eligible for local processing — never that
    anything executed."""
    verdict: PolicyVerdict
    reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class FederatedOutcome:
    """A bounded record of what another authority domain REPORTS happened in
    response to a delegation (AD-049). It is NOT an ActionResult: it is the
    receiving domain's record of a remote report — "B reported X", never "A
    observed X". `status` is "reported_success" | "reported_failure".
    `remote_action_id` is the peer's local action identity, an opaque reference."""
    delegation_id: str
    peer_id: str
    status: str
    remote_action_id: str = ""
    output: Any = None
    error: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass
class ModelRequest:
    """A model request with its own identity, so `model.requested` and
    `model.completed` can unambiguously belong to the same run/step."""
    messages: list[dict[str, Any]] = field(default_factory=list)
    max_tokens: int = 4096
    timeout_ms: int = 30000
    request_id: str = field(default_factory=lambda: new_id("req"))


@dataclass
class ModelResponse:
    """Nexus-shaped, NOT provider-shaped. The adapter translates the provider
    (OpenAI/Ollama/local) into these Nexus semantics and drops the rest.

    `success`/`error` mirror ToolResult: a valid response is success=True; a
    provider rejection is success=False with an error (AD-010's model twin)."""
    model: str
    content: str
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    cost: float = 0.0
    success: bool = True
    error: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)


@dataclass
class ApprovalRequest:
    """A request for human authorization of ONE specific proposed tool call.

    Bound to its identity (task/run/tool/risk) so approving one action cannot
    authorize another. Single-use: pending -> approved/denied -> consumed."""
    approval_id: str
    task_id: str
    run_id: str
    tool_name: str
    risk: Risk
    status: str = "pending"  # pending / approved / denied / consumed / expired


@dataclass
class Evaluation:
    """A verification outcome — evidence, not an evaluator-specific object.

    `passed` / `reason` / `replan_required` are the structured verdict the
    orchestrator interprets; `checks` is the raw evidence (Phase 1). The
    evaluator OBSERVES and JUDGES; the orchestrator DECIDES what happens next."""
    checks: dict[str, Any] = field(default_factory=dict)
    passed: bool = True
    reason: str = ""
    replan_required: bool = False


@dataclass
class Event:
    """The boring event envelope. Every subsystem can consume it."""
    event_id: str
    event_type: str
    timestamp: datetime
    run_id: str
    task_id: str
    component: str
    status: str
    payload: dict[str, Any] = field(default_factory=dict)
    parent_event_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat()
        return d


@dataclass
class Trace:
    """The execution tree. One node per decision/action, linked by parent ids."""
    run_id: str
    nodes: list[dict[str, Any]] = field(default_factory=list)


class Executor(Protocol):
    """The execution capability — the ONLY sanctioned path to a side effect.

    Decision != Action: control-plane components return Decisions (and other
    contracts); they never implement this. Only the execution plane turns a
    Decision into an action, and only through this interface.
    """

    def execute_tool(self, call: ToolCall) -> ToolResult: ...

    def run_model(self, request: ModelRequest) -> ModelResponse: ...
