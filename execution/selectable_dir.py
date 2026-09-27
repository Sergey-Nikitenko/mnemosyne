"""SelectableProjectDir — a runtime-changeable bounded filesystem root.

Mirrors `SelectableModel`: the project directory the tool executor is bound to is
persisted to a small workspace file, so a console selection reaches the worker
process that actually runs the tools. The bounded-root invariant is unchanged —
every path is still resolved inside the CURRENT root, and a path outside it is
still rejected. Pure composition: no authority, no new side effect.
"""
from __future__ import annotations

import json
import os

from core.contracts import ToolCall, ToolResult
from execution.filesystem import FilesystemToolExecutor


class SelectableProjectDir:
    def __init__(self, root: str, selection_file: str | None = None):
        self._selection_file = selection_file
        self._root = root
        self._executor = FilesystemToolExecutor(root)
        self._load()

    def _load(self):
        if not self._selection_file:
            return
        try:
            with open(self._selection_file, "r", encoding="utf-8") as f:
                root = json.load(f).get("project_dir", "")
            if root and root != self._root:
                self._root = root
                self._executor = FilesystemToolExecutor(root)
        except (OSError, ValueError, AttributeError):
            pass

    def _persist(self):
        if not self._selection_file:
            return
        try:
            d = os.path.dirname(self._selection_file)
            if d:
                os.makedirs(d, exist_ok=True)
            with open(self._selection_file, "w", encoding="utf-8") as f:
                json.dump({"project_dir": self._root}, f)
        except OSError:
            pass

    def select(self, root: str) -> bool:
        if not root or not os.path.isdir(root):
            return False
        self._root = root
        self._executor = FilesystemToolExecutor(root)
        self._persist()
        return True

    def current(self) -> str:
        return self._root

    def refresh(self) -> str:
        """Re-read the persisted cross-process selection and return the current
        root. The capability executor calls this so its read-side evidence tools
        observe the same root a cross-process selection just switched to."""
        self._load()
        return self._root

    def _registry_file(self):
        if not self._selection_file:
            return ""
        return os.path.join(os.path.dirname(self._selection_file), "projects.json")

    def _load_registry(self):
        f = self._registry_file()
        if not f:
            return {}
        try:
            with open(f, "r", encoding="utf-8") as fh:
                return json.load(fh).get("projects", {})
        except (OSError, ValueError):
            return {}

    def list_projects(self):
        projects = self._load_registry()
        return [{"name": n, "path": p, "active": p.rstrip("\\/") == self._root.rstrip("\\/")}
                for n, p in projects.items()]

    def select_project(self, name: str) -> bool:
        projects = self._load_registry()
        if name not in projects:
            return False
        return self.select(projects[name])

    def execute_tool(self, call: ToolCall) -> ToolResult:
        args = dict(call.arguments or {})
        if call.tool_name == "list_projects":
            return ToolResult(tool_call=call, success=True, output={"projects": self.list_projects()})
        if call.tool_name == "select_project":
            name = str(args.get("name", ""))
            if not self.select_project(name):
                return ToolResult(tool_call=call, success=False, output={},
                                  error=f"unknown project: {name!r}")
            return ToolResult(tool_call=call, success=True, output={"project_dir": self._root})
        self._load()  # honor a cross-process selection before each call
        return self._executor.execute_tool(call)
