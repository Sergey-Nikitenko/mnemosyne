"""StopSignal — a durable, cross-process operator interrupt.

The console (serve process) writes a stop request for a task; the worker's
orchestrator checks it between rounds and aborts. File-backed like the model/
project selectors, so the two OS processes share it without a bus.
"""
from __future__ import annotations

import json
import os


class StopSignal:
    def __init__(self, workspace: str | None):
        self._file = os.path.join(workspace, "stop.json") if workspace else ""

    def request(self, task_id: str) -> None:
        if not self._file:
            return
        try:
            data = self._load()
            data[task_id] = True
            self._write(data)
        except OSError:
            pass

    def clear(self, task_id: str) -> None:
        if not self._file:
            return
        try:
            data = self._load()
            data.pop(task_id, None)
            self._write(data)
        except OSError:
            pass

    def is_requested(self, task_id: str) -> bool:
        if not self._file:
            return False
        try:
            return bool(self._load().get(task_id))
        except OSError:
            return False

    def _load(self) -> dict:
        try:
            with open(self._file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _write(self, data: dict) -> None:
        with open(self._file, "w", encoding="utf-8") as f:
            json.dump(data, f)
