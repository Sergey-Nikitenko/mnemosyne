"""EXPERIMENTAL scratch store — capability-owned, workspace-local, deletable.

The write-side toys (Decision Registry, ChangeSet, Work Graph, verification
debt, shadow workspaces) need somewhere to put their bookkeeping. That state is
MODEL-AUTHORED and CHALLENGEABLE — it is NOT authoritative Nexus state (memory,
events, policy, verification truth), so the law ("capability may not write
authoritative state") is preserved: this is a separate workspace directory, and
deleting it loses only experimental bookkeeping, never a Mnemosyne semantic.

Plain JSON files, atomic replace, no ORM, no authority.
"""
from __future__ import annotations

import json
import os


class CapabilityStore:
    def __init__(self, state_dir: str | None) -> None:
        self.dir = state_dir
        if state_dir:
            os.makedirs(state_dir, exist_ok=True)

    def _path(self, name: str) -> str | None:
        return os.path.join(self.dir, name + ".json") if self.dir else None

    def load(self, name: str, default=None):
        p = self._path(name)
        if not p or not os.path.isfile(p):
            return default if default is not None else {}
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return default if default is not None else {}

    def save(self, name: str, obj) -> bool:
        p = self._path(name)
        if not p:
            return False
        tmp = p + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(obj, f, indent=2)
            os.replace(tmp, p)
            return True
        except OSError:
            return False

    # -- collection helpers over a dict-keyed JSON object --------------------
    def get(self, name: str, key: str):
        return self.load(name, {}).get(key)

    def put(self, name: str, key: str, value) -> bool:
        obj = self.load(name, {})
        obj[key] = value
        return self.save(name, obj)

    def remove(self, name: str, key: str) -> bool:
        obj = self.load(name, {})
        if key not in obj:
            return False
        del obj[key]
        return self.save(name, obj)
