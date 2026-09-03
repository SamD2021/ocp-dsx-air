from typing import Protocol
from uuid import UUID

from ocp_dsx_air.core.contracts import JumpHostSnapshot
from ocp_dsx_air.models.runtime import ClusterNetworkConfig


class JumpHostPort(Protocol):
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
