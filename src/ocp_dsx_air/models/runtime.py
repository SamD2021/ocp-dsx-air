from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ResolvedCredentials:
    """Holds the actual secret strings, not paths to files."""

    air_api_key: str
    ai_offline_token: str
    pull_secret: str
    ssh_public_key: str


@dataclass(frozen=True)
class ClusterNetworkConfig:
    cluster_name: str
    domain: str
    api_vip: str
    ingress_vip: str

    @property
    def base_domain(self) -> str:
        """The root DNS boundary for the cluster (e.g., ocp.dsx.air.local)."""
        return f"{self.cluster_name}.{self.domain}"

    @property
    def apps_domain(self) -> str:
        """The wildcard DNS boundary for all OpenShift routes."""
        return f"apps.{self.base_domain}"


@dataclass(frozen=True)
class DeployContext:
    """The complete, resolved intent passed from the CLI to the Workflow."""

    sim_name: str
    profile: str
    expected_hosts: int
    topology_node_names: list[str]
    discovery_timeout_s: int
    cdrom_image_name: str
    network: ClusterNetworkConfig
    creds: ResolvedCredentials
    cache_dir: Path
