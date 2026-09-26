"""Federation boundary — represent another authority domain (AD-047).

The Federation component represents a remote Nexus WITHOUT granting it local
authority. It is pure, transport-independent, and persists nothing:

    remote declaration -> Federation -> FederationPeer -> X (not trusted)

- a remote's self-declared capabilities and metadata are CLAIMS, never authority;
- the local peer_id is derived (namespaced), never the remote's arbitrary string;
- protocol compatibility is checked explicitly, never assumed.
"""
from __future__ import annotations

from core.contracts import FederationPeer


class Federation:
    """Represent a remote authority domain as a bounded FederationPeer."""

    def __init__(self, protocol_version: int = 1) -> None:
        self.protocol_version = protocol_version

    def protocol_compatible(self, remote_version: int) -> bool:
        return remote_version == self.protocol_version

    def represent(self, *, remote_id: str, protocol_version: int,
                  capabilities=(), metadata=None,
                  endpoint: str = "") -> FederationPeer | None:
        if not self.protocol_compatible(protocol_version):
            return None
        return FederationPeer(
            peer_id=f"peer/{remote_id}",
            protocol_version=protocol_version,
            capabilities=list(capabilities),
            metadata=dict(metadata or {}),
            endpoint=endpoint,
        )
