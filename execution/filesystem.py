"""Filesystem tool executor — bounded file read/write/list/run behind the Executor.

Every path is resolved inside a fixed project root; a path that escapes the root is
REJECTED as a ToolResult failure (never a write outside the sandbox). This is the
"bounded file-write capability": the model proposes a path, the tool enforces the
boundary, and the operator's project directory is the only thing it can touch.

`run_command` executes ONE executable with argv (`shlex.split` + `subprocess.run` with
`shell=False`): it is NOT a shell. Pipelines, redirection, `&&`/`||`/`;`, command
substitution, and shell built-ins are not interpreted — they are passed as literal
arguments. The model-facing tool description MUST advertise exactly this (pinned by
tests/conformance/test_run_command_contract.py); advertising "run a shell command"
silently broke WORK-001's diagnosis -> mutation transition.

stdlib-only (pathlib/subprocess), so `execution/` keeps importing core only.
"""
from __future__ import annotations

import hashlib
import shlex
import subprocess
from pathlib import Path

from core.contracts import ToolCall, ToolResult
from execution import workspace_intel as WI
from execution.repetition import RepetitionGate

MAX_READ_BYTES = 64 * 1024
MAX_OUTPUT_BYTES = 64 * 1024


class FilesystemToolExecutor:
    """Executes list_dir / read_file / write_file / run_command inside one root."""

    def __init__(self, root: str) -> None:
        self._root = Path(root).resolve()
        self._index = None
        self._rep = RepetitionGate()
        self._failed_writes = {}  # (tool, path) -> consecutive failure count

    # a write that fails N times on an unchanged target is a mechanism failure, not
    # a task — re-reading the file resets it; retrying the same stale edit does not.
    WRITE_RETRY_LIMIT = 3

    def _write_retry_block(self, tool, path):
        if self._failed_writes.get((tool, path), 0) >= self.WRITE_RETRY_LIMIT:
            return ("Mechanism failure: this edit has failed repeatedly on an unchanged "
                    "target. Re-read the file first, then retry with current content.")
        return None

    def _record_write_failure(self, tool, path):
        key = (tool, path)
        self._failed_writes[key] = self._failed_writes.get(key, 0) + 1

    def _reset_write_failures(self, path):
        for key in list(self._failed_writes):
            if key[1] == path:
                del self._failed_writes[key]

    def _get_index(self):
        if self._index is None:
            self._index = WI.build_symbol_index(str(self._root))
        return self._index

    def _resolve(self, rel) -> Path | None:
        p = Path(rel)
        if p.is_absolute():
            return None  # absolute paths are never allowed to escape the root
        resolved = (self._root / p).resolve()
        if resolved != self._root and self._root not in resolved.parents:
            return None  # path traversal escapes the root
        return resolved

    def execute_tool(self, call: ToolCall) -> ToolResult:
        try:
            args = dict(call.arguments or {})
            if call.tool_name == "list_dir":
                return self._list(call, args)
            if call.tool_name == "read_file":
                return self._read(call, args)
            if call.tool_name == "edit_file":
                return self._edit(call, args)
            if call.tool_name == "write_file":
                return self._write(call, args)
            if call.tool_name == "run_command":
                return self._run(call, args)
            if call.tool_name == "run_elevated":
                return self._run_elevated(call, args)
            # ---- Workspace Intelligence (deterministic read-side + verification) ----
            root = str(self._root)
            if call.tool_name == "workspace_map":
                dirs, files = WI.workspace_map(root)
                return ToolResult(tool_call=call, success=True,
                                  output={"directories": dirs,
                                          "files": [f"{r}/{fn}".lstrip("./") for r, fn in files]})
            if call.tool_name == "find_symbol":
                hits = WI.find_symbol(self._get_index(), str(args.get("query", "")))
                return ToolResult(tool_call=call, success=True, output={"matches": hits})
            if call.tool_name == "find_references":
                return ToolResult(tool_call=call, success=True,
                                  output={"references": WI.find_references(root, str(args.get("symbol", "")))})
            if call.tool_name == "related_tests":
                return ToolResult(tool_call=call, success=True,
                                  output={"tests": WI.related_tests(root, symbol=args.get("symbol"), module=args.get("module"))})
            if call.tool_name == "get_symbol_source":
                src = WI.get_symbol_source(root, str(args.get("symbol", "")))
                return ToolResult(tool_call=call, success=src is not None,
                                  output=src or {}, error=None if src else "symbol not found")
            if call.tool_name == "dependency_neighborhood":
                nb = WI.dependency_neighborhood(root, str(args.get("symbol", "")))
                return ToolResult(tool_call=call, success=nb is not None,
                                  output=nb or {}, error=None if nb else "symbol not found")
            if call.tool_name == "verification_candidates":
                return ToolResult(tool_call=call, success=True,
                                  output=WI.verification_candidates(root, str(args.get("path_or_symbol", ""))))
            if call.tool_name == "changed_since":
                return ToolResult(tool_call=call, success=True,
                                  output=WI.changed_since(root, args.get("manifest") or {}))
            if call.tool_name == "patch_symbol":
                ok, msg = WI.patch_symbol(root, str(args.get("path", "")), str(args.get("symbol", "")),
                                          str(args.get("expected_hash", "")), str(args.get("replacement", "")))
                return ToolResult(tool_call=call, success=ok, output={"message": msg}, error=None if ok else msg)
            if call.tool_name == "run_verification":
                scope = args.get("scope") or "full"
                subject = args.get("symbol") or args.get("path_or_symbol") or ""
                if scope == "related" and subject:
                    vc = WI.verification_candidates(root, subject)
                    targets = vc.get("direct_tests") or ["tests"]
                else:
                    targets = ["tests"]
                res = WI.run_pytest(root, targets)
                passed = res.get("returncode") == 0
                return ToolResult(tool_call=call, success=passed, output=res,
                                  error=None if passed else f"{res.get('failed', 0)} tests failed")
            return ToolResult(tool_call=call, success=False, output={},
                              error=f"unknown tool: {call.tool_name}")
        except Exception as exc:  # an unexpected tool error is a defined failure
            return ToolResult(tool_call=call, success=False, output={}, error=str(exc))

    def _list(self, call, args):
        target = self._resolve(args.get("path", "."))
        if target is None:
            return ToolResult(tool_call=call, success=False, output={},
                              error="path outside project")
        if not target.is_dir():
            return ToolResult(tool_call=call, success=False, output={},
                              error=f"not a directory: {args.get('path')}")
        entries = [{"name": p.name, "type": "dir" if p.is_dir() else "file"}
                   for p in sorted(target.iterdir())]
        return ToolResult(tool_call=call, success=True, output={"entries": entries})

    def _read(self, call, args):
        target = self._resolve(args.get("path", ""))
        if target is None:
            return ToolResult(tool_call=call, success=False, output={},
                              error="path outside project")
        if not target.is_file():
            return ToolResult(tool_call=call, success=False, output={},
                              error=f"not a file: {args.get('path')}")
        rel = str(target.relative_to(self._root))
        raw = target.read_bytes()
        truncated = len(raw) > MAX_READ_BYTES
        # The gate fingerprint must be the bytes actually ADMITTED. Fingerprinting the
        # whole file would make any mutation beyond MAX_READ_BYTES look like "no state
        # change" and refuse a read whose served content is in fact different. Hashing
        # the admitted slice keeps discrimination faithful to what the model sees.
        admitted = raw[:MAX_READ_BYTES]
        content = admitted.decode("utf-8", errors="replace")
        h = hashlib.sha256(admitted).hexdigest()[:16]
        verdict = self._rep.verdict("read_file", rel, h)
        out = {"path": rel, "content": content, "hash": h}
        if truncated:
            out["truncated"] = True
            out["total_bytes"] = len(raw)
        if verdict == "flag":
            out["repetition"] = True  # flag, never reject — tool calls are never capped
        self._reset_write_failures(rel)  # re-acquisition resets the write-retry mechanism
        return ToolResult(tool_call=call, success=True, output=out)

    def _edit(self, call, args):
        rel = args.get("path", "")
        old = args.get("old_string", "")
        new = args.get("new_string", "")
        if not rel:
            return ToolResult(tool_call=call, success=False, output={},
                              error="edit_file requires a path")
        if not old:
            return ToolResult(tool_call=call, success=False, output={},
                              error="edit_file requires old_string")
        block = self._write_retry_block("edit_file", rel)
        if block:
            return ToolResult(tool_call=call, success=False, output={}, error=block)
        target = self._resolve(rel)
        if target is None:
            return ToolResult(tool_call=call, success=False, output={},
                              error="path outside project")
        if not target.is_file():
            return ToolResult(tool_call=call, success=False, output={},
                              error=f"not a file: {rel}")
        content = target.read_bytes().decode("utf-8", errors="replace")
        count = content.count(old)
        if count == 0:
            self._record_write_failure("edit_file", rel)
            return ToolResult(tool_call=call, success=False, output={},
                              error="old_string not found")
        if count > 1:
            self._record_write_failure("edit_file", rel)
            return ToolResult(tool_call=call, success=False, output={},
                              error=f"old_string appears {count} times; use a larger unique string")
        new_content = content.replace(old, new, 1)
        target.write_bytes(new_content.encode("utf-8"))
        self._rep.reset("read_file", rel)  # a mutation changes the target -> re-observation allowed
        self._reset_write_failures(rel)
        return ToolResult(tool_call=call, success=True,
                          output={"path": str(target.relative_to(self._root)),
                                  "bytes": len(new_content.encode("utf-8"))})

    def _write(self, call, args):
        rel = args.get("path", "")
        content = args.get("content", "")
        if not rel:
            return ToolResult(tool_call=call, success=False, output={},
                              error="write_file requires a path")
        block = self._write_retry_block("write_file", rel)
        if block:
            return ToolResult(tool_call=call, success=False, output={}, error=block)
        target = self._resolve(rel)
        if target is None:
            return ToolResult(tool_call=call, success=False, output={},
                              error="path outside project")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(content), encoding="utf-8")
        self._rep.reset("read_file", rel)  # a mutation changes the target -> re-observation allowed
        self._reset_write_failures(rel)
        return ToolResult(tool_call=call, success=True,
                          output={"path": str(target.relative_to(self._root)),
                                  "bytes": len(str(content).encode("utf-8"))})

    def _run(self, call, args):
        command = args.get("command", "")
        if not command:
            return ToolResult(tool_call=call, success=False, output={},
                              error="run_command requires a command")
        try:
            argv = shlex.split(str(command))
        except ValueError as exc:
            return ToolResult(tool_call=call, success=False, output={},
                              error=f"bad command: {exc}")
        try:
            proc = subprocess.run(argv, cwd=str(self._root), shell=False,
                                  capture_output=True, text=True, timeout=120.0)
        except subprocess.TimeoutExpired:
            return ToolResult(tool_call=call, success=False, output={},
                              error="timeout after 120s")
        out = proc.stdout[-MAX_OUTPUT_BYTES:]
        err = proc.stderr[-MAX_OUTPUT_BYTES:]
        return ToolResult(tool_call=call, success=proc.returncode == 0,
                          output={"stdout": out, "stderr": err},
                          error=None if proc.returncode == 0
                          else f"exit status {proc.returncode}")

    def _run_elevated(self, call, args):
        """Run a command with OS elevation (the UAC prompt) via PowerShell
        `Start-Process -Verb RunAs`. Output capture is limited (the elevated
        process runs in a separate session) — reported honestly, never guessed."""
        command = args.get("command", "")
        if not command:
            return ToolResult(tool_call=call, success=False, output={},
                              error="run_elevated requires a command")
        ps = f"Start-Process -FilePath 'cmd.exe' -ArgumentList '/c {command}' -Verb RunAs -Wait"
        try:
            proc = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                                  cwd=str(self._root), shell=False,
                                  capture_output=True, text=True, timeout=300.0)
        except subprocess.TimeoutExpired:
            return ToolResult(tool_call=call, success=False, output={},
                              error="elevated command timed out after 300s")
        out = (proc.stdout or "")[-MAX_OUTPUT_BYTES:]
        err = (proc.stderr or "")[-MAX_OUTPUT_BYTES:]
        return ToolResult(tool_call=call, success=proc.returncode == 0,
                          output={"elevated": True, "command": command,
                                  "stdout": out, "stderr": err},
                          error=None if proc.returncode == 0
                          else f"elevation exit status {proc.returncode}")
