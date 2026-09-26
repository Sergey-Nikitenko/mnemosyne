"""Filesystem tool executor — bounded file read/write/list/run behind the Executor.

Every path is resolved inside a fixed project root; a path that escapes the root is
REJECTED as a ToolResult failure (never a write outside the sandbox). This is the
"bounded file-write capability": the model proposes a path, the tool enforces the
boundary, and the operator's project directory is the only thing it can touch.

stdlib-only (pathlib/subprocess), so `execution/` keeps importing core only.
"""
from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

from core.contracts import ToolCall, ToolResult

MAX_READ_BYTES = 256 * 1024
MAX_OUTPUT_BYTES = 64 * 1024


class FilesystemToolExecutor:
    """Executes list_dir / read_file / write_file / run_command inside one root."""

    def __init__(self, root: str) -> None:
        self._root = Path(root).resolve()

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
            if call.tool_name == "write_file":
                return self._write(call, args)
            if call.tool_name == "run_command":
                return self._run(call, args)
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
        data = target.read_bytes()[:MAX_READ_BYTES]
        return ToolResult(tool_call=call, success=True,
                          output={"path": str(target.relative_to(self._root)),
                                  "content": data.decode("utf-8", errors="replace")})

    def _write(self, call, args):
        rel = args.get("path", "")
        content = args.get("content", "")
        if not rel:
            return ToolResult(tool_call=call, success=False, output={},
                              error="write_file requires a path")
        target = self._resolve(rel)
        if target is None:
            return ToolResult(tool_call=call, success=False, output={},
                              error="path outside project")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(content), encoding="utf-8")
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
