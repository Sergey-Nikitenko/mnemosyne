"""Conformance: `mnemosyne serve` is a thin, disposable lifecycle surface (4.10).

The CLI translates operator configuration into MnemosyneConfig, delegates
construction to CompositionRoot, serves the EXISTING console_app, and owns
shutdown via finally. It never constructs a component or authorizes/mutates/
reinterprets anything. This test proves the surface is deletable without touching
any Mnemosyne semantic.

Run:  py tests/conformance/test_serve_surface.py
"""
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.contracts import Event, utcnow  # noqa: E402
from core.events import EventType  # noqa: E402
from core.state import action_attempted, reconstruct_action  # noqa: E402
from apps.composition import CompositionRoot, MnemosyneConfig, MnemosyneSystem  # noqa: E402
import apps.serve as serve_mod  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


def main():
    print("Conformance: mnemosyne serve is a thin lifecycle surface")
    tmp = tempfile.mkdtemp()

    # -- 10. help is sufficient for discovery (a real invocation) ---------------
    r = subprocess.run([sys.executable, "-m", "apps.serve", "--help"],
                       capture_output=True, text=True, cwd=ROOT)
    check(r.returncode == 0 and "--workspace" in r.stdout and "--model-id" in r.stdout,
          "`python -m apps.serve --help` is a single supported entry point with discoverable flags")

    # -- 2. thin translation: CLI -> MnemosyneConfig, never a component ---------
    args = serve_mod._build_parser().parse_args(
        ["--workspace", tmp, "--user-id", "alice", "--agent-id", "mnemosyne",
         "--model-id", "fake"])
    cfg = serve_mod.build_config(args)
    check(isinstance(cfg, MnemosyneConfig), "build_config returns MnemosyneConfig (data, not a system)")
    check(cfg.user.key == "user/alice" and cfg.agent.key == "agent/mnemosyne@1"
          and cfg.model.key == "model/fake@1",
          "each CLI flag maps 1:1 onto a MnemosyneConfig field")

    # -- 5. configuration remains configuration ---------------------------------
    args2 = serve_mod._build_parser().parse_args(
        ["--workspace", tmp, "--user-id", "alice", "--agent-id", "mnemosyne",
         "--model-id", "fake-v2"])
    cfg2 = serve_mod.build_config(args2)
    check(cfg2.model.key == "model/fake-v2@1" and cfg2.user.key == cfg.user.key
          and cfg2.workspace == cfg.workspace,
          "changing --model-id is an argument change, not a code/topology change")

    # -- spies: exactly one build, exactly one close ----------------------------
    real_build = CompositionRoot.build
    build_count = [0]

    def spy_build(self, c):
        build_count[0] += 1
        return real_build(self, c)
    CompositionRoot.build = spy_build

    real_close = MnemosyneSystem.close
    close_count = [0]

    def spy_close(self):
        close_count[0] += 1
        return real_close(self)
    MnemosyneSystem.close = spy_close

    captured = {}

    def noop_run(app, **kw):
        captured["app"] = app

    # -- 3 + 4 + 6: canonical composition, existing console, normal shutdown ----
    serve_mod.serve(cfg, _run=noop_run)
    check(build_count[0] == 1, "exactly one CompositionRoot.build(config)")
    check(close_count[0] == 1, "exactly one system.close() on normal shutdown")
    paths = {getattr(r, "path", None) for r in captured["app"].routes}
    check("/api/ncs" in paths and "/" in paths and "/ask" in paths,
          "serves the existing console_app (no second application)")

    # -- 8 + 6: no startup side effects; workspace reconstructs after close -----
    sys2 = CompositionRoot().build(cfg)
    check(len(sys2.runtime.events()) == 0
          and sys2.memory.list_as_of("semantic", utcnow()) == [],
          "serve() produced no startup side effects (no events, no memory)")
    sys2.close()

    # -- 7. exceptional shutdown still closes (finally) -------------------------
    def raising_run(app, **kw):
        raise RuntimeError("boom")
    before = close_count[0]
    try:
        serve_mod.serve(cfg, _run=raising_run)
    except RuntimeError:
        pass
    check(close_count[0] == before + 1,
          "system.close() still runs on exceptional shutdown (finally)")

    # -- 9. AD-050 survives the serve lifecycle ---------------------------------
    sys_a = CompositionRoot().build(cfg)
    sys_a.runtime.event_bus.publish(Event(
        event_id="evt-crash", event_type=EventType.ACTION_REQUESTED,
        timestamp=utcnow(), run_id="r-crash", task_id="t-crash", component="action",
        status="running", payload={"action_id": "act.crash",
                                   "capability": "github.read_file",
                                   "parameters": {}, "scope": "",
                                   "requested_by": "agent/mnemosyne@1"}))
    sys_a.close()
    serve_mod.serve(cfg, _run=noop_run)  # full serve lifecycle on the same workspace
    sys_c = CompositionRoot().build(cfg)
    evs = sys_c.runtime.events()
    check(action_attempted("act.crash", evs) and reconstruct_action("act.crash", evs) is None,
          "AD-050: attempted/unknown survives the serve lifecycle")
    check(not any(e.event_type == EventType.ACTION_FAILED
                  and e.payload.get("action_id") == "act.crash" for e in evs),
          "no action.failed was fabricated through serve")
    sys_c.close()

    CompositionRoot.build = real_build
    MnemosyneSystem.close = real_close

    print("\nPASS: mnemosyne serve is a disposable lifecycle surface — it translates "
          "config, delegates to the root, serves the existing console, and owns shutdown.")


if __name__ == "__main__":
    main()
