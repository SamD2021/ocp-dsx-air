import time
from pathlib import Path

from ocp_dsx_air.core.common import cache_dir
from ocp_dsx_air.core.workflows import deploy_lab
from ocp_dsx_air.models.runtime import ClusterNetworkConfig, DeployContext, ResolvedCredentials
from ocp_dsx_air.models.spec import load_spec


def _read_secret(path_str: str | None) -> str:
    """Helper to read the actual file contents specified in the YAML."""
    if not path_str:
        raise ValueError("Missing required auth file in spec.")
    path = Path(path_str).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"Auth file not found: {path}")
    return path.read_text().strip()


def run_deploy(
    spec_path: Path,
    replace: bool = False,
    discovery_timeout: int | None = None,
) -> None:
    # 1. Parse the pure intent
    spec = load_spec(spec_path)

    # 2. Resolve the secrets into memory
    creds = ResolvedCredentials(
        air_api_key=_read_secret(spec.auth.air_api_key_file),
        ai_offline_token=_read_secret(spec.auth.ai_offlinetoken_file),
        pull_secret=_read_secret(spec.auth.pull_secret_file),
        ssh_public_key=_read_secret(spec.auth.ssh_public_key_file),
    )

    # 3. Build the Network Config (Hardcoded defaults here, or add to LabSpec later)
    network = ClusterNetworkConfig(
        cluster_name=spec.cluster.name,
        base_dns_domain="dsx.air.local",
        api_vip="192.168.200.10",
        ingress_vip="192.168.200.11",
    )

    # 4. Build the final context
    expected_hosts = spec.expected_hosts
    timeout_s = discovery_timeout if discovery_timeout else max(20 * 60, 8 * 60 * max(expected_hosts, 1))

    ctx = DeployContext(
        sim_name=spec.simulation.name,
        profile=spec.profile,
        expected_hosts=expected_hosts,
        topology_node_names=[],  # TODO: Extract from topology logic
        discovery_timeout_s=timeout_s,
        cdrom_image_name=f"dsxair-discovery-{int(time.time())}",
        network=network,
        creds=creds,
        cache_dir=cache_dir(),
    )

    # 5. Hand off to the pure Core Orchestrator
    deploy_lab(ctx, replace=replace)
