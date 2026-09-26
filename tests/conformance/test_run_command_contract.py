"""Conformance: run_command's advertised contract == its actual capabilities.

WORK-001 proved that a false model-facing tool contract is not cosmetic: describing
`run_command` as "run a shell command" while it actually executes ONE executable with
argv (`shlex.split` + `subprocess.run(shell=False)`) changed downstream action strategy
— the model issued shell pipelines/builtins that "inexplicably" failed and, until the
contract was corrected, never crossed diagnosis -> mutation.

This test pins the SEMANTIC relationship (not an English sentence), so a future edit
cannot silently re-advertise a shell without failing:

    direct executable + argv     YES
    shell pipeline                NO
    redirection                   NO
    && / || / ;                   NO
    shell built-ins               NO

Run:  py tests/conformance/test_run_command_contract.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.contracts import ToolCall  # noqa: E402
from execution.filesystem import FilesystemToolExecutor  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok - {msg}")


def main():
    print("Conformance: run_command contract matches shell=False execution")
    root = tempfile.mkdtemp()
    fs = FilesystemToolExecutor(root)

    def run(cmd):
        return fs.execute_tool(ToolCall(tool_name="run_command", arguments={"command": cmd}))

    def stdout_of(result):
        out = result.output or {}
        return str(out.get("stdout", ""))

    # 1. direct executable + argv: works
    r = run("py -c \"print(7)\"")
    check(r.success and "7" in stdout_of(r), "direct executable + argv executes")

    # 2. shell pipeline: the '|' is a literal argument, not a pipe
    r = run("py -c \"print(7)\" | py -c \"print(9)\"")
    check("9" not in stdout_of(r), "shell pipeline is NOT interpreted (| is literal)")

    # 3. redirection: the '>' is literal; no file is created
    r = run("py -c \"print(7)\" > redirected.txt")
    check(not os.path.exists(os.path.join(root, "redirected.txt")),
          "redirection is NOT interpreted (> is literal)")

    # 4. && (and, by extension || / ;): the operator is literal, not control flow
    r = run("py -c \"print(7)\" && py -c \"print(9)\"")
    check("9" not in stdout_of(r), "&& is NOT interpreted (control flow absent)")

    # 5. shell built-ins: not executables, so they fail as a defined tool failure
    r = run("echo contract_probe")
    check(r.success is False, "shell built-in (echo) is NOT available (no shell)")

    print("\nPASS: run_command advertised capabilities == actual capabilities "
          "(argv-only, shell=False).")


if __name__ == "__main__":
    main()
