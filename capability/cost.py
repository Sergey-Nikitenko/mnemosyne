"""CapabilityCost — the economic profile of each capability, not a call counter.

After abolishing the universal tool-call cap, the governance question becomes:
what is ACTUALLY scarce or consequential about a given tool? This module
answers it deterministically.

    FREE_LOCAL_READ    symbol lookup, workspace map, outline, signature …  unmetered
    CONTEXT_BEARING    full file contents …  unmetered calls, PRESENTATION governed
    COMPUTE_HEAVY      test suite, builds, subprocess …  resource/latency governed
    WORLD_MUTATING     file writes …  authority + freshness + verification debt
    EXTERNAL_PAID      paid APIs …  monetary budget
    IRREVERSIBLE       deploy, send, delete …  strongest authority

The rule (codified in OBSERVATIONS.md): local deterministic capability invocation
is UNMETERED by default; resource policy governs actual scarce resources and
consequential effects, never invocation count. The framework must never make
ignorance cheaper than asking the house.

Delete this module and every Mnemosyne semantic is intact — it is a read-only
cost classification rendered by `capability_explain`.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum


class EconomicClass(str, Enum):
    FREE_LOCAL_READ = "FREE_LOCAL_READ"
    CONTEXT_BEARING = "CONTEXT_BEARING"
    COMPUTE_HEAVY = "COMPUTE_HEAVY"
    WORLD_MUTATING = "WORLD_MUTATING"
    EXTERNAL_PAID = "EXTERNAL_PAID"
    IRREVERSIBLE = "IRREVERSIBLE"


_GOVERNANCE = {
    EconomicClass.FREE_LOCAL_READ: "unmetered",
    EconomicClass.CONTEXT_BEARING: "unmetered calls; presentation governed",
    EconomicClass.COMPUTE_HEAVY: "resource/latency governed",
    EconomicClass.WORLD_MUTATING: "authority + freshness + verification debt",
    EconomicClass.EXTERNAL_PAID: "monetary budget",
    EconomicClass.IRREVERSIBLE: "strongest authority",
}


@dataclass(frozen=True)
class CapabilityCost:
    economic_class: EconomicClass = EconomicClass.FREE_LOCAL_READ
    monetary: str = "~0"
    compute: str = "trivial"
    latency: str = "low"
    world_effect: str = "none"
    presentation: str = "low"
    verification_debt: bool = False
    external_effect: str = "none"

    def as_dict(self) -> dict:
        d = asdict(self)
        d["economic_class"] = self.economic_class.value
        d["governance"] = _GOVERNANCE[self.economic_class]
        return d


# Per-tool profiles for anything that is NOT the FREE_LOCAL_READ default.
_TOOL_COST = {
    "read_file": CapabilityCost(EconomicClass.CONTEXT_BEARING,
                                presentation="potentially high"),
    "get_symbol_source": CapabilityCost(EconomicClass.CONTEXT_BEARING,
                                        presentation="potentially high"),
    "evidence_bundle": CapabilityCost(EconomicClass.CONTEXT_BEARING,
                                      presentation="potentially high"),
    "context_need": CapabilityCost(EconomicClass.CONTEXT_BEARING,
                                   presentation="potentially high"),
    "run_verification": CapabilityCost(EconomicClass.COMPUTE_HEAVY,
                                       compute="potentially high", latency="potentially high"),
    "verify_fabric": CapabilityCost(EconomicClass.COMPUTE_HEAVY,
                                    compute="potentially high", latency="potentially high"),
    "run_command": CapabilityCost(EconomicClass.COMPUTE_HEAVY,
                                  compute="potentially high", latency="potentially high"),
    "run_elevated": CapabilityCost(EconomicClass.WORLD_MUTATING,
                                   world_effect="WRITE (OS-elevated)", verification_debt=True),
    "edit_file": CapabilityCost(EconomicClass.WORLD_MUTATING, world_effect="WRITE",
                                verification_debt=True),
    "write_file": CapabilityCost(EconomicClass.WORLD_MUTATING, world_effect="WRITE",
                                 verification_debt=True),
    "patch_symbol": CapabilityCost(EconomicClass.WORLD_MUTATING, world_effect="WRITE",
                                   verification_debt=True),
    "execute_intent": CapabilityCost(EconomicClass.WORLD_MUTATING, world_effect="WRITE",
                                     verification_debt=True),
    "record_decision": CapabilityCost(EconomicClass.WORLD_MUTATING,
                                      world_effect="framework_state"),
    "reaffirm_decision": CapabilityCost(EconomicClass.WORLD_MUTATING,
                                        world_effect="framework_state"),
    "retire_decision": CapabilityCost(EconomicClass.WORLD_MUTATING,
                                      world_effect="framework_state"),
}


def capability_cost(tool: str) -> CapabilityCost:
    return _TOOL_COST.get(tool, CapabilityCost())


def cost_profile(tool: str) -> dict:
    return capability_cost(tool).as_dict()
