from pathlib import Path
from typing import Protocol
from uuid import UUID

from ocp_dsx_air.core.contracts import (
    AssistedClusterIntent,
    AssistedClusterSnapshot,
    AssistedHostSnapshot,
    AssistedInfraEnvIntent,
    AssistedInfraEnvSnapshot,
    CredentialPaths,
    OpenShiftNodeRole,
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

    def find_infraenv(
        self,
        name: str,
    ) -> AssistedInfraEnvSnapshot | None:
        """Return the exact-name InfraEnv, or None when it does not exist.

        Raise AssistedError if lookup fails or multiple exact-name InfraEnvs exist.
        """
        ...

    def create_infraenv(
        self,
        intent: AssistedInfraEnvIntent,
        *,
        pull_secret: str,
    ) -> AssistedInfraEnvSnapshot:
        """Create an InfraEnv from validated intent and return its observation."""
        ...

    def delete_infraenv(self, infraenv_id: UUID) -> None:
        """Request InfraEnv deletion."""
        ...

    def download_discovery_iso(
        self,
        infraenv_id: UUID,
        destination: Path,
    ) -> Path:
        """Atomically write a non-empty discovery ISO with owner-only access."""
        ...

    def list_hosts(
        self,
        cluster_id: UUID,
    ) -> tuple[AssistedHostSnapshot, ...]:
        """Return normalized snapshots for hosts bound to the cluster."""
        ...

    def update_host_role(
        self,
        infraenv_id: UUID,
        host_id: UUID,
        role: OpenShiftNodeRole,
    ) -> AssistedHostSnapshot:
        """Assign one discovered host role by InfraEnv and host UUID."""
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
