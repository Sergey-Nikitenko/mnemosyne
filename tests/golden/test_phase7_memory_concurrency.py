"""Phase 7.x golden task — MemoryStore concurrency contract (AD-027).

The Operator Console surfaced a real defect (OBS-SQLITE-001): a single shared
`sqlite3` connection raised `InterfaceError` when a console read (NCS projection
→ `list_as_of`) ran concurrently with a legitimate authoritative write (Phase 9
adaptation → `record_if_current`). MemoryStore now gives each thread its OWN
connection — the SAME policy as the execution stores, implemented locally because
`memory/` imports core only. This test reproduces the exact workload that failed
and proves:

1.  a reader repeatedly projects NCS/list_as_of while legitimate writes occur;
2.  record_if_current keeps its atomic compare-and-append under concurrency;
3.  no InterfaceError / ProgrammingError / connection misuse;
4.  no duplicate memory versions;
5.  no successful write disappears from a subsequent projection;
6.  historical as_of semantics stay deterministic;
7.  retirement freshness stays intact under concurrent reads;
8.  close() closes every connection it owns and never modifies authoritative state.

Run:  py tests/golden/test_phase7_memory_concurrency.py
"""
import os
import sys
import tempfile
import threading
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.contracts import (  # noqa: E402
    AgentIdentity, Event, ModelIdentity, UserIdentity, utcnow,
)
from core.events import EventType  # noqa: E402
from learning.adaptation import AdaptationRunner  # noqa: E402
from learning.analyzer import EvidenceAnalyzer  # noqa: E402
from learning.authority import GroundedLearningAuthority  # noqa: E402
from memory.continuity import ContinuityProjector  # noqa: E402
from memory.memory import MemoryStore  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


# One authoritative terminal action event — the evidence a legitimate Phase-9
# proposal must cite to pass the learning authority's authenticity check.
EVIDENCE = [Event(
    event_id="evt-evidence", event_type=EventType.ACTION_COMPLETED,
    timestamp=utcnow(), run_id="run-ev", task_id="task-ev", component="action",
    status="success", payload={"action_id": "act.evidence"})]


def main():
    print("Phase 7.x golden task: MemoryStore concurrency (AD-027)")
    tmp = tempfile.mkdtemp()
    store = MemoryStore(os.path.join(tmp, "memory.db"))
    projector = ContinuityProjector()
    analyzer = EvidenceAnalyzer()
    authority = GroundedLearningAuthority(events=lambda: list(EVIDENCE))
    adaptation = AdaptationRunner(authority, store)
    user = UserIdentity(user_id="u")
    agent = AgentIdentity(agent_id="a")
    model = ModelIdentity(model_id="m")

    def project():
        return projector.project(utcnow(), store, user=user, agent=agent, model=model)

    store.record("semantic", "fact.seed",
                 {"subject": "s", "predicate": "p", "object": "o"}, scope="project")

    # -- the exact workload that exposed OBS-SQLITE-001 ----------------------
    errors: list[str] = []
    stop = threading.Event()

    def reader():
        try:
            while not stop.is_set():
                ncs = project()  # list_as_of x3, on this thread's connection
                _ = [(m.memory_id, m.version) for m in ncs.semantic_memories]
        except Exception as e:
            errors.append(f"{type(e).__name__}: {e}")

    t = threading.Thread(target=reader, daemon=True)
    t.start()

    N = 20
    for i in range(N):
        store.record("semantic", f"fact.{i}",
                     {"subject": f"s{i}", "predicate": "p", "object": f"o{i}"},
                     scope="project")
        ncs = project()
        proposal = analyzer.analyze(
            kind="semantic", target=f"fact.{i}", target_version=1,
            evidence=[{"action_id": "act.evidence"}], scope="project",
            candidate={"subject": f"s{i}!", "predicate": "p", "object": f"o{i}!"})
        adaptation.adapt(proposal, ncs)

    stop.set()
    t.join(timeout=10)

    check(not errors, f"reader projected NCS/list_as_of through every write with no error ({errors})")

    # 4/5 — no duplicate versions; no successful write disappeared
    for i in range(N):
        versions = [h.version for h in store.history("semantic", f"fact.{i}")]
        check(versions == [1, 2],
              f"fact.{i} is exactly [v1, v2] (no duplicate, no lost write): {versions}")
    final = project()
    check(len(final.semantic_memories) == N + 1,
          "every write is visible in the final projection")

    # 2 — record_if_current remains an atomic compare-and-append under concurrency
    store.record("semantic", "fact.cas",
                 {"subject": "s", "predicate": "p", "object": "o"}, scope="project")
    results: list = []

    def cas_worker():
        results.append(store.record_if_current(
            "semantic", "fact.cas", {"subject": "x", "predicate": "p", "object": "o"},
            expected_version=1, scope="project"))

    threads = [threading.Thread(target=cas_worker) for _ in range(2)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    wins = [r for r in results if r is not None]
    check(len(wins) == 1, "exactly one concurrent record_if_current wins (atomic CAS)")
    check(store.get("semantic", "fact.cas").version == 2,
          "the winner appended exactly v2 (no duplicate version)")

    # 6 — historical as_of semantics stay deterministic
    early = store.list_as_of("semantic", datetime(2020, 1, 1, tzinfo=timezone.utc))
    check(early == [], "as-of before any write projects empty")
    t_fixed = utcnow()
    snap1 = [m.memory_id for m in store.list_as_of("semantic", t_fixed)]
    snap2 = [m.memory_id for m in store.list_as_of("semantic", t_fixed)]
    check(snap1 == snap2 and len(snap1) == N + 2,
          "same as_of projects the same records, deterministically (seed + N facts + CAS)")

    # 7 — retirement freshness stays intact under concurrent reads
    rerrs: list[str] = []
    stop2 = threading.Event()

    def reader2():
        try:
            while not stop2.is_set():
                _ = store.list_as_of("semantic", utcnow())
        except Exception as e:
            rerrs.append(f"{type(e).__name__}: {e}")

    t2 = threading.Thread(target=reader2, daemon=True)
    t2.start()
    ret = store.retire_if_current("semantic", "fact.0", expected_version=2)
    stop2.set()
    t2.join(timeout=10)
    check(ret is not None and ret.status == "retired",
          "retirement succeeded under concurrent reads")
    check(not rerrs, f"no reader error during retirement ({rerrs})")
    check(store.retire_if_current("semantic", "fact.0", expected_version=1) is None,
          "a stale retirement is still rejected atomically")

    # 8 — close() closes every connection it owns; never modifies state
    check(store.connection_count() >= 2,
          f"multiple thread-local connections were opened ({store.connection_count()})")
    store.close()
    store.close()  # idempotent
    check(store.connection_count() == 0, "close() closed every connection it owns")
    store2 = MemoryStore(os.path.join(tmp, "memory.db"))
    check(len(store2.list_as_of("semantic", utcnow())) == N + 2,
          "reopen reconstructs identical state (close wrote nothing)")
    check(store2.get("semantic", "fact.0").status == "retired",
          "retirement persisted across reopen")

    print("\nPASS: MemoryStore holds AD-027 concurrency — one connection per thread, "
          "atomic CAS, deterministic temporal projection, observably-neutral shutdown.")


if __name__ == "__main__":
    main()
