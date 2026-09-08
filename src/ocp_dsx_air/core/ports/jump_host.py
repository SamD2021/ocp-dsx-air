from typing import Protocol
from uuid import UUID

from ocp_dsx_air.core.contracts import JumpHostSnapshot
from ocp_dsx_air.models.runtime import ClusterNetworkConfig


class JumpHostPort(Protocol):
    def find_jump_host(self, simulation_id: UUID) -> JumpHostSnapshot | None:
        """Return the ready managed SSH service, or None while it is unavailable."""
        ...

    def ensure_jump_host(
        self,
        simulation_id: UUID,
        network: ClusterNetworkConfig,
        *,
        new_password: str,
        timeout_seconds: float,
    ) -> JumpHostSnapshot:
        """Expose SSH, rotate the factory password, and configure cluster DNS."""
        ...
