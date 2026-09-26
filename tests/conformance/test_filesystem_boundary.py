"""Conformance: the filesystem tool executor is bounded to its project root.

The model proposes a path; the tool enforces the boundary. A path that escapes the
root (absolute, traversal, or sibling escape) is REJECTED as a defined ToolResult
failure — never a write outside the sandbox. This is the security half of the
real-backend integration: the model is more capable than the fake, not more
authoritative.

Run:  py tests/conformance/test_filesystem_boundary.py
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
    print("Conformance: filesystem tools are bounded to the project root")
    tmp = tempfile.mkdtemp()
    fs = FilesystemToolExecutor(tmp)

    # write + read + list round-trip inside the root
    w = fs.execute_tool(ToolCall(tool_name="write_file",
                                 arguments={"path": "docs/a.txt", "content": "hello"}))
    check(w.success and os.path.exists(os.path.join(tmp, "docs", "a.txt")),
          "write_file creates the file inside the root")
    r = fs.execute_tool(ToolCall(tool_name="read_file", arguments={"path": "docs/a.txt"}))
    check(r.success and r.output["content"] == "hello", "read_file returns the content")
    l = fs.execute_tool(ToolCall(tool_name="list_dir", arguments={"path": "docs"}))
    check(l.success and any(e["name"] == "a.txt" for e in l.output["entries"]),
          "list_dir lists the created file")

    # the boundary: every escape is rejected, never written
    for bad in ("../escape.txt", "..\\..\\outside.txt", "C:\\Windows\\Temp\\escape.txt",
                "/etc/escape.txt"):
        r = fs.execute_tool(ToolCall(tool_name="write_file",
                                     arguments={"path": bad, "content": "pwned"}))
        check(r.success is False and "outside project" in (r.error or ""),
              f"escape path {bad!r} is rejected, never written")
        check(not os.path.exists(os.path.join(tmp, "escape.txt")),
              "no escape file was created")

    # read outside is also rejected (no exfiltration)
    r = fs.execute_tool(ToolCall(tool_name="read_file", arguments={"path": "../secret.txt"}))
    check(r.success is False, "reading outside the root is rejected")

    print("\nPASS: filesystem tools are bounded — the model proposes, the tool enforces the boundary.")


if __name__ == "__main__":
    main()
