from pathlib import Path
from typing import Protocol
from uuid import UUID

from ocp_dsx_air.core.contracts import (
    AirImageIntent,
    AirImageSnapshot,
    AirSimulationIntent,
    AirSimulationSnapshot,
)


class AirPort(Protocol):
    def find_image(self, name: str) -> AirImageSnapshot | None:
        """Return the exact-name image, or None when it does not exist."""
        ...

    def create_image(self, intent: AirImageIntent) -> AirImageSnapshot:
        """Create an image record without uploading its content."""
        ...

    def upload_image(self, image_id: UUID, source: Path) -> AirImageSnapshot:
        """Upload source into an existing image record and return its observation."""
        ...

    def delete_image(self, image_id: UUID) -> None:
        """Request image deletion."""
        ...

    def find_simulation(self, name: str) -> AirSimulationSnapshot | None:
        """Return the exact-name simulation, or None when it does not exist."""
        ...

    def import_simulation(
        self,
        intent: AirSimulationIntent,
    ) -> AirSimulationSnapshot:
        """Import a stopped simulation from validated intent."""
        ...

    def start_simulation(self, simulation_id: UUID) -> None:
        """Request a normal simulation start using Air's resume semantics."""
        ...

    def shutdown_simulation(
        self,
        simulation_id: UUID,
        *,
        create_checkpoint: bool,
    ) -> None:
        """Request simulation shutdown with explicit checkpoint policy."""
        ...

    def delete_simulation(self, simulation_id: UUID) -> None:
        """Request simulation deletion."""
        ...
