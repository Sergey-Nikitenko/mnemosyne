"""EXPERIMENTAL capability layer — speculative, recoverable, NOT part of the trusted
core invariants.

The boundary (frozen):

    Capability Layer (this package)  -- proposes candidate evidence, bundles, tiers,
                                        routing, editing, path/symbol intelligence.
                                              |
                                              | "proposes" — never "decides"
                                              v
    Trusted Core (core/ control/ execution/ apps/)  -- Authority, Freshness, Provenance,
                                        Policy, Stop/Revocation, Obligations,
                                        Verification truth, Event truth.

A speculative Context Router choosing the wrong file is recoverable — the model can
request another one. A speculative mechanism deciding that stale evidence is current,
granting authority, suppressing an operator stop, approving a consequential action,
or declaring verification successful is NOT acceptable. Nothing in this package may
write to authoritative state, re-authorize, or alter a trusted verdict.

Delete any module here and every Mnemosyne semantic is intact.
"""
