"""Conformance: the supported worker surface is startup recovery + run_one loop + clean
termination (Phase 4.11 / AD-053).

Pins the SURFACE, not the agency semantics: the worker recovers stale work once at
startup, drains via `run_one()` (the only execution primitive), idle-waits when empty,
terminates cleanly on stop, and `close()` fabricates no terminal event.
"""
import os
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.events import EventType  # noqa: E402
from apps.composition import CompositionRoot, MnemosyneConfig  # noqa: E402
from apps.serve import worker  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


def main():
    print("Conformance: worker surface = startup recovery + run_one loop + clean termination")
    ws = tempfile.mkdtemp()
    config = MnemosyneConfig(workspace=ws, worker_id="w-test", lease_seconds=0.0)

    # seed a stale task: a "doomed" worker claims it and dies (no complete/fail)
    s = CompositionRoot().build(config)
    tid = s.runtime.ask("recover me", user="alice", agent="mnemosyne")
    s.runtime.queue.claim("doomed")  # generation 1, no terminal
    s.close()

    # run the worker surface in a thread: it recovers at startup, drains, then we stop it
    stop = threading.Event()
    t = threading.Thread(target=worker, args=(config,),
                         kwargs={"lease_seconds": 0.0, "poll_seconds": 0.01, "stop": stop})
    t.start()
    deadline = time.time() + 5
    status = None
    while time.time() < deadline:
        c = CompositionRoot().build(config)
        status = c.runtime.task(tid).status.value
        c.close()
        if status == "done":
            break
        time.sleep(0.05)
    stop.set()
    t.join(timeout=5)
    check(status == "done", "startup recovery + drain completed the stale task")
    check(not t.is_alive(), "worker terminated cleanly after stop")

    # clean termination fabricated nothing: exactly one task.completed (from run_one, not close)
    c = CompositionRoot().build(config)
    comp = [e for e in c.runtime.events(task_id=tid) if e.event_type == EventType.TASK_COMPLETED]
    check(len(comp) == 1, "exactly one task.completed (run_one, never close())")
    c.close()

    print("\nPASS: worker surface owns recovery + drain + clean termination, fabricates nothing.")


if __name__ == "__main__":
    main()
