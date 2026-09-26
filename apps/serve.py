"""`mnemosyne serve` — a lifecycle surface, not a runtime or authority layer (4.10).

    mnemosyne serve -> CLI args -> MnemosyneConfig -> CompositionRoot.build(config)
                    -> system.console_app -> HTTP/WebSocket server
                    -> (shutdown) -> system.close()

The CLI translates explicit operator configuration into `MnemosyneConfig`,
delegates construction to `CompositionRoot`, serves the EXISTING `console_app`
(no second application), and guarantees closure of the composed system via
`finally`. It never constructs a store, authority, runner, projector, learning, or
federation component itself; it never authorizes, mutates memory, reinterprets
events, or manufactures outcomes. Deleting this module leaves every Mnemosyne
semantic intact — it is a disposable lifecycle surface.

Run (from a repository checkout, no packaging required):

    python -m apps.serve serve --workspace ./workspace --user-id user/alice \
        --agent-id agent/mnemosyne --model-id model/fake

The leading `serve` is optional (so the same code works behind a future
`mnemosyne` console-script entry point). Configuration is deliberately primitive:
one flag per required `MnemosyneConfig` field. If that becomes ugly in repeated
use, that is the operational evidence that earns a serializable config file — it
is not hidden here.
"""
from __future__ import annotations

import argparse
import sys

from core.contracts import AgentIdentity, ModelIdentity, UserIdentity
from apps.composition import CompositionRoot, MnemosyneConfig


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mnemosyne",
        description="Serve the Mnemosyne Operator Console (a projection of authoritative state).",
    )
    parser.add_argument("--workspace", required=True, metavar="DIR",
                        help="workspace directory — the persistence root (required)")
    parser.add_argument("--user-id", default="", metavar="ID",
                        help="user identity, e.g. user/alice (who)")
    parser.add_argument("--agent-id", default="", metavar="ID",
                        help="agent identity, e.g. agent/mnemosyne (which role)")
    parser.add_argument("--model-id", default="unset", metavar="ID",
                        help="model identity, e.g. model/fake (which reasoning backend)")
    parser.add_argument("--model-backend", default="fake", choices=["fake", "deepseek"],
                        help="reasoning backend: fake (deterministic echo) or deepseek (real cloud model)")
    parser.add_argument("--model-api-key-file", default="", metavar="PATH",
                        help="path to the DeepSeek API key file (secret, never logged)")
    parser.add_argument("--model-name", default="deepseek-chat", metavar="NAME",
                        help="provider model name for the deepseek backend")
    parser.add_argument("--project-dir", default="", metavar="DIR",
                        help="bounded filesystem root for the file tools (empty = fake tools)")
    parser.add_argument("--max-tool-rounds", type=int, default=8, metavar="N",
                        help="max model->tools cycles per attempt before a terminal answer (default 8)")
    parser.add_argument("--host", default="127.0.0.1",
                        help="bind host (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000,
                        help="bind port (default 8000)")
    parser.add_argument("--worker-id", default="worker-1", metavar="ID",
                        help="worker identity stamped on claims (default worker-1)")
    parser.add_argument("--lease-seconds", type=float, default=30.0, metavar="SECS",
                        help="worker recovery lease: a CLAIMED task older than this is stale (default 30)")
    return parser


def build_config(args) -> MnemosyneConfig:
    """Translate parsed CLI arguments into MnemosyneConfig — data, never components.

    This is the thin-translation boundary: CLI value -> MnemosyneConfig field.
    It instantiates no store, authority, runner, projector, learning, or federation
    component."""
    return MnemosyneConfig(
        workspace=args.workspace,
        user=UserIdentity(user_id=args.user_id),
        agent=AgentIdentity(agent_id=args.agent_id, role="operator"),
        model=ModelIdentity(model_id=args.model_id, family="", version="1"),
        model_backend=args.model_backend,
        model_api_key_file=args.model_api_key_file,
        model_name=args.model_name,
        project_dir=args.project_dir,
        max_tool_rounds=args.max_tool_rounds,
        worker_id=args.worker_id,
        lease_seconds=args.lease_seconds,
    )


def serve(config: MnemosyneConfig, *, host: str = "127.0.0.1", port: int = 8000,
          _run=None) -> None:
    """Build the system, serve the existing console_app, guarantee closure.

    `_run` is a test seam: it defaults to `uvicorn.run`; a test may inject a
    no-op or raising callable to observe lifecycle ownership without binding a
    real socket."""
    import uvicorn
    run = _run or uvicorn.run
    system = CompositionRoot().build(config)
    print(f"Serving Mnemosyne Operator Console at http://{host}:{port} (Ctrl+C to stop)")
    try:
        run(system.console_app, host=host, port=port)
    finally:
        system.close()  # exactly one close; guaranteed on normal and exceptional exit


def worker(config: MnemosyneConfig, *, lease_seconds: float | None = None,
           poll_seconds: float = 1.0, stop=None, _run_one=None) -> None:
    """Run a supported worker (OBS-007 / Phase 4.11).

    ONE startup recovery pass (recover stale work using the configured lease), then a
    bounded drain loop whose ONLY execution primitive is `runtime.run_one()`
    (claim → run → complete/fail). Idle waiting = a short interruptible sleep when the
    queue is empty. Graceful termination = `stop` is set: the in-flight `run_one()`
    completes and no new task is claimed. `close()` writes nothing, so shutdown is
    observably neutral (an `action.requested` with no terminal survives as
    attempted/unknown).

    `_run_one` is a test seam (defaults to `system.runtime.run_one`). The worker adds no
    authority, no heartbeat, no scheduler, and no new execution semantic — recovery policy
    already exists (AD-019); the worker only decides WHEN to invoke it (once, at startup)."""
    import threading
    import time

    from execution.recovery import RecoveryManager

    system = CompositionRoot().build(config)
    run_one = _run_one or system.runtime.run_one
    stop = stop if stop is not None else threading.Event()
    lease = lease_seconds if lease_seconds is not None else config.lease_seconds
    try:
        recovered = RecoveryManager(system.runtime.queue, lease).recover()
        if recovered:
            print(f"worker: recovered {len(recovered)} stale task(s)")
        while not stop.is_set():
            outcome = run_one()
            if outcome is None:
                # queue empty -> idle wait (interruptible by the stop signal)
                stop.wait(poll_seconds)
        # loop exited via stop: the in-flight run_one() already completed; claim no new task
    finally:
        system.close()


def main(argv=None) -> int:
    import signal
    import threading

    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "worker":
        argv = argv[1:]
        args = _build_parser().parse_args(argv)
        stop = threading.Event()

        def _handle(signum, frame):
            stop.set()

        signal.signal(signal.SIGINT, _handle)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, _handle)
        worker(build_config(args), lease_seconds=args.lease_seconds, stop=stop)
        return 0
    if argv and argv[0] == "serve":
        argv = argv[1:]  # accept the optional 'serve' subcommand
    args = _build_parser().parse_args(argv)
    serve(build_config(args), host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
