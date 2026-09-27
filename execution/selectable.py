"""SelectableModel — a switchable reasoning backend behind the Executor protocol.

Pure composition: it holds the available model executors and the current
selection, and routes `run_model` to the active one. Selection is durable (a
small JSON file in the workspace) so a console selection reaches the worker
process that actually runs the model — the orchestrator never knows which
backend it is using, and no authority is added.
"""
from __future__ import annotations

import json
import os

from core.contracts import ModelRequest, ModelResponse


class SelectableModel:
    """Routes `run_model` to the selected backend; selection persists to a file."""

    def __init__(self, models: dict, active: str = "", selection_file: str | None = None):
        # models: {name: (executor, meta)} where meta = {provider, family, version}
        self._models = {k: v[0] for k, v in models.items()}
        self._meta = {k: v[1] for k, v in models.items()}
        self._selection_file = selection_file
        self._active = active if active in self._models else (next(iter(self._models), ""))
        self._load()

    def _load(self):
        if not self._selection_file:
            return
        try:
            with open(self._selection_file, "r", encoding="utf-8") as f:
                name = json.load(f).get("active", "")
            if name in self._models:
                self._active = name
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
                json.dump({"active": self._active}, f)
        except OSError:
            pass

    def select(self, name: str) -> bool:
        if name not in self._models:
            return False
        self._active = name
        self._persist()
        return True

    def list(self) -> list[dict]:
        return [{"name": n, "provider": self._meta[n].get("provider", ""),
                 "family": self._meta[n].get("family", ""),
                 "version": self._meta[n].get("version", ""),
                 "active": n == self._active} for n in self._models]

    def active_name(self) -> str:
        return self._active

    def run_model(self, request: ModelRequest) -> ModelResponse:
        self._load()  # honor a cross-process selection before each call
        return self._models[self._active].run_model(request)
