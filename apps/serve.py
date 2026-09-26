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
    parser.add_argument("--host", default="127.0.0.1",
                        help="bind host (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000,
                        help="bind port (default 8000)")
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


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "serve":
        argv = argv[1:]  # accept the optional 'serve' subcommand
    args = _build_parser().parse_args(argv)
    serve(build_config(args), host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
