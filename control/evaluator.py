"""Evaluator — deterministic verification, independent of the model's opinion.

The evaluator checks what ACTUALLY happened (did tests pass? did the tools
succeed? which files changed?) and returns an Evaluation (a Phase 0 contract).
It never asks the model "did it work" — that's the point: evidence over claims.

The evaluator OBSERVES and JUDGES; it never executes a capability or mutates
execution state (that is the orchestrator's job). It returns an Evaluation —
`passed` / `reason` / `replan_required` — and the orchestrator interprets it.
"""
from __future__ import annotations

from core.contracts import Evaluation, ToolResult


def _result_name(result) -> str:
    """The tool/operation name of a result — a ToolResult's tool_call, or an
    ActionResult's capability. Keeps the evaluator agnostic to which execution
    level produced the result."""
    tc = getattr(result, "tool_call", None)
    if tc is not None:
        return getattr(tc, "tool_name", "?")
    return getattr(result, "capability", "?")


def _is_verification(name: str, arguments=None) -> bool:
    """Whether a tool EXECUTES verification (runs the suite), vs. merely reads
    test-related metadata (related_tests / verification_candidates). A
    `run_command` that invokes a test runner also counts, so the evaluator sees
    the model's suite result regardless of which tool it used."""
    n = (name or "").lower()
    if "run_verification" in n or "run_tests" in n or "verify_fabric" in n:
        return True
    if "run_command" in n and arguments:
        cmd = str(arguments.get("command", "")).lower()
        if any(k in cmd for k in ("pytest", "check.py", "unittest", "test")):
            return True
    return False


class Evaluator:
    def evaluate(
        self,
        *,
        tests_passed: bool | None = None,
        tool_results: list[ToolResult] | None = None,
        files_changed: list[str] | None = None,
        groundedness: float | None = None,
    ) -> Evaluation:
        checks: dict = {}
        verification = None
        others: list = []
        if tool_results is not None:
            for r in tool_results:
                # verification tools are judged separately; the LAST one wins so
                # a recovered re-run (failed then green) counts as green.
                name = _result_name(r)
                args = getattr(getattr(r, "tool_call", None), "arguments", None) or {}
                if _is_verification(name, args):
                    verification = r
                else:
                    others.append(r)
            checks["tool_success"] = all(r.success for r in others)
            failed = [_result_name(r) for r in others if not r.success]
            if verification is not None and not verification.success:
                failed.append(_result_name(verification))
            if failed:
                checks["failed_tools"] = failed
        # derive tests_passed from the verification tool when the caller didn't
        # supply it — evidence over the model's claim.
        if tests_passed is None and verification is not None:
            tests_passed = bool(verification.success)
        verification_failed = verification is not None and not verification.success
        if tests_passed is not None:
            checks["tests"] = "pass" if tests_passed else "fail"
        if files_changed is not None:
            checks["files_changed"] = list(files_changed)
        if groundedness is not None:
            checks["groundedness"] = groundedness

        passed, reason = self._verdict(checks, verification_failed)
        return Evaluation(checks=checks, passed=passed, reason=reason)

    @staticmethod
    def _verdict(checks: dict, verification_failed: bool = False) -> tuple[bool, str]:
        """A run passes unless its verification failed — the suite ran red, or
        the verification tool itself errored. Intermediate tool failures during
        the loop are recoverable process noise, not a terminal failure: the
        FINAL verification is the authoritative gate. Soft scores (groundedness)
        never gate.
        """
        if verification_failed:
            return False, "verification tool failed"
        if checks.get("tests") == "fail":
            return False, "tests failed"
        return True, "checks passed"

    @staticmethod
    def passed(eval_: Evaluation) -> bool:
        return eval_.passed


class FakeEvaluator:
    """Deterministic scripted evaluator — the reference double for the loop.

    Pops the next scripted Evaluation per call (mirrors FakeExecutor's
    model_script), so a test can prove FAIL -> replan -> PASS without any real
    verification or an LLM judge.
    """

    def __init__(self, script: list[Evaluation] | None = None,
                 default: Evaluation | None = None) -> None:
        self._script = list(script or [])
        self._default = default

    def evaluate(self, **kwargs) -> Evaluation:
        if self._script:
            return self._script.pop(0)
        if self._default is not None:
            return self._default
        return Evaluation(passed=True, reason="script exhausted")
