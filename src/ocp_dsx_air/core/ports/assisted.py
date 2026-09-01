from pathlib import Path
from typing import Protocol
from uuid import UUID

from ocp_dsx_air.core.contracts import (
    AssistedClusterIntent,
    AssistedClusterSnapshot,
    AssistedHostSnapshot,
    CredentialPaths,
)


class AssistedInstallerPort(Protocol):
    def find_cluster(
        self,
        name: str,
    ) -> AssistedClusterSnapshot | None:
        """Return the exact-name cluster, or None when it does not exist.

        Raise AssistedError if lookup fails or multiple exact-name clusters exist.
        """
        ...

    def create_cluster(
        self,
        intent: AssistedClusterIntent,
        *,
        pull_secret: str,
        ssh_public_key: str,
    ) -> AssistedClusterSnapshot:
        """Create a cluster from validated intent."""
        ...

    def delete_cluster(self, cluster_id: UUID) -> None:
        """Request cluster deletion."""
        ...

    def list_hosts(
        self,
        cluster_id: UUID,
    ) -> tuple[AssistedHostSnapshot, ...]:
        """Return normalized snapshots for hosts bound to the cluster."""
        ...

    def start_installation(self, cluster_id: UUID) -> None:
        """Request installation start."""
        ...

    def download_credentials(
        self,
        cluster_id: UUID,
        destination_dir: Path,
    ) -> CredentialPaths:
        """Atomically write credentials with owner-only permissions."""
        ...
