"""Phase 7.2 golden task — memory taxonomy contracts (event-sourced).

Memory objects are projections of authoritative events; the model cannot directly
rewrite authoritative memory. This single test proves the whole invariant, not
just object construction:

    source event -> project memory -> verify the three kinds
    -> attempt a model-style direct mutation -> authoritative state unchanged
    -> emit a legitimate update -> new version
    -> verify provenance + version history (v1 never silently mutated)

Run:  py tests/golden/test_phase7_memory.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.contracts import Event, Preference, Procedure, SemanticMemory, new_id, utcnow  # noqa: E402
from memory.memory import MemoryStore  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


def main():
    print("Phase 7.2 golden task: memory taxonomy contracts")
    store = MemoryStore(os.path.join(tempfile.mkdtemp(), "memory.db"))

    # 1. a source event — the origin the memory is derived from --------------
    source = Event(event_id=new_id("evt"), event_type="run.completed",
                   timestamp=utcnow(), run_id="run/xyz", task_id="task/xyz",
                   component="orchestrator", status="success", payload={"answer": "done"})

    # 2. project the three kinds from that source ----------------------------
    proc = store.record("procedure", "coloring_book.production",
                        content={"name": "coloring_book.production",
                                 "steps": ["define_concept", "generate_illustrations", "publish"],
                                 "constraints": ["brand_rules"]},
                        scope="project/xyz",
                        provenance={"source": "run/xyz", "origin_event": source.event_id})
    sem = store.record("semantic", "brand",
                       content={"subject": "AVIWYN", "predicate": "is",
                                "object": "the publishing brand"},
                       scope="project/xyz", provenance={"source": "run/xyz"})
    pref = store.record("preference", "art_style",
                        content={"key": "art_style", "value": "clean line-art"},
                        scope="user/alice", provenance={"source": "run/xyz"})

    # 3. the contracts are typed and carry identity/version/provenance --------
    check(isinstance(proc, Procedure) and proc.version == 1 and len(proc.steps) == 3,
          "Procedure is a typed contract with explicit version 1")
    check(isinstance(sem, SemanticMemory) and sem.subject == "AVIWYN",
          "SemanticMemory is a typed contract (subject/predicate/object)")
    check(isinstance(pref, Preference) and pref.value == "clean line-art",
          "Preference is a typed contract (key/value)")
    check(proc.provenance.get("origin_event") == source.event_id,
          "provenance chains back to the source event")

    # 4. a model-style direct mutation must NOT alter authoritative state -----
    proc.steps.append("hack_step")   # mutate the projected object in place
    proc.version = 99
    again = store.get("procedure", "coloring_book.production")
    check(again.version == 1 and "hack_step" not in again.steps,
          "mutating a projected object does not alter authoritative memory")

    # 5. a legitimate update emits a NEW version (no silent mutation of v1) ---
    v2 = store.record("procedure", "coloring_book.production",
                      content={"name": "coloring_book.production",
                               "steps": ["define_concept", "generate_illustrations",
                                         "preflight", "publish"],
                               "constraints": ["brand_rules"]},
                      scope="project/xyz", provenance={"source": "run/xyz2"})
    check(v2.version == 2, "a legitimate update produces version 2")
    check(store.get("procedure", "coloring_book.production").version == 2,
          "the current version is 2")

    # 6. version history preserves v1 unchanged -------------------------------
    history = store.history("procedure", "coloring_book.production")
    check([h.version for h in history] == [1, 2],
          "version history holds [v1, v2]")
    check(history[0].steps == ["define_concept", "generate_illustrations", "publish"],
          "v1 is immutable — the update did not silently rewrite it")
    check(history[1].provenance.get("source") == "run/xyz2",
          "v2 carries its own provenance")

    store.close()
    print("\nPASS: Phase 7.2 memory taxonomy contracts hold (event-sourced, versioned, provenanced).")


if __name__ == "__main__":
    main()
