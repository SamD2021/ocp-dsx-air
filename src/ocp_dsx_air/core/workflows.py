"""Deployment orchestration entry points and policy design scaffold.

Design checkpoints before replacing the transitional ``deploy_lab`` below:

* define the static deploy intent and the staged builders that add vendor UUIDs;
* match Assisted hosts to intended nodes by stable identity and assign roles;
* decide whether replacement is global authorization or a per-resource request;
* order dependent-resource cleanup without losing installed disk state;
* isolate Air service discovery and jump-host DNS behind a port;
* define readiness at each phase and divide the overall timeout budget;
* define the snapshots and credential paths returned by a successful deployment.

The private reconciliation functions intentionally contain no policy. Their
signatures provide implementation slots around contracts that already exist.
"""

from pathlib import Path

from ocp_dsx_air.core.contracts import (
    AirImageIntent,
    AirImageSnapshot,
    AirSimulationIntent,
    AirSimulationSnapshot,
    AssistedClusterIntent,
    AssistedClusterSnapshot,
    AssistedInfraEnvIntent,
    AssistedInfraEnvSnapshot,
)
from ocp_dsx_air.core.ports.air import AirPort
from ocp_dsx_air.core.ports.assisted import AssistedInstallerPort
from ocp_dsx_air.core.runtime import Clock, DeploymentReporter
from ocp_dsx_air.models.runtime import DeployContext


def _reconcile_cluster(
    intent: AssistedClusterIntent,
    *,
    assisted: AssistedInstallerPort,
    reporter: DeploymentReporter,
    clock: Clock,
    pull_secret: str,
    ssh_public_key: str,
    replace: bool,
    timeout_seconds: float,
    poll_interval_seconds: float,
) -> AssistedClusterSnapshot:
    """Advance cluster reconciliation without choosing its phase boundary.

    The implementation should repeatedly observe, decide, emit, perform at most
    one mutation, and observe again until the caller's completion condition is
    met or the monotonic deadline expires. If replacement is requested, consume
    that request after deletion so the replacement is not deleted again.
    """
    raise NotImplementedError("cluster reconciliation policy is not designed yet")


def _reconcile_infraenv(
    intent: AssistedInfraEnvIntent,
    *,
    assisted: AssistedInstallerPort,
    reporter: DeploymentReporter,
    clock: Clock,
    pull_secret: str,
    iso_path: Path,
    replace: bool,
    timeout_seconds: float,
    poll_interval_seconds: float,
) -> tuple[AssistedInfraEnvSnapshot, Path]:
    """Reconcile an InfraEnv and its UUID-owned local discovery ISO.

    The implementation should use the same observe-decide-emit-mutate loop,
    rechecking both remote ISO availability and local cache validity. Forced
    replacement must be consumed after the old InfraEnv is deleted.
    """
    raise NotImplementedError("InfraEnv reconciliation policy is not designed yet")


def _reconcile_air_image(
    intent: AirImageIntent,
    *,
    source: Path,
    air: AirPort,
    reporter: DeploymentReporter,
    clock: Clock,
    replace: bool,
    timeout_seconds: float,
    poll_interval_seconds: float,
) -> AirImageSnapshot:
    """Reconcile one content-addressed Air image from a verified local file.

    The implementation should observe, decide, emit, execute one image action,
    and observe again. It must consume replacement after deletion and must not
    treat an upload request as proof that Air has finished validating content.
    """
    raise NotImplementedError("Air image reconciliation policy is not designed yet")


def _reconcile_simulation(
    intent: AirSimulationIntent,
    *,
    air: AirPort,
    reporter: DeploymentReporter,
    clock: Clock,
    replace: bool,
    timeout_seconds: float,
    poll_interval_seconds: float,
) -> AirSimulationSnapshot:
    """Reconcile the managed Air simulation through one safe action at a time.

    The implementation should preserve Air's normal resume behavior, wait for
    lifecycle transitions, and consume replacement only after shutdown and
    deletion complete. Checkpoint and disk-state policy remains a design choice.
    """
    raise NotImplementedError("Air simulation reconciliation policy is not designed yet")


def deploy_lab(ctx: DeployContext, replace: bool = False) -> None:
    """The master orchestrator for a greenfield lab deployment."""

    print(f"Starting deployment for simulation {ctx.sim_name!r}...")

    # 1. Existing Simulation Check
    # (Uses your new simulation.py functions, passing explicit strings)
    # existing = simulation.check_existing(ctx.sim_name)

    # 2. ISO & Images
    # (Delegates to iso.py and images.py, passing explicit paths/creds)
    # iso_path = ctx.cache_dir / "discovery.iso"
    # iso.create_discovery_iso(iso_path, ctx.network, ctx.creds)
    # images.upload_discovery_iso(iso_path, ctx.cdrom_image_name, ctx.creds.air_api_key)
    # images.upload_blank_disk(ctx.cache_dir / "blank-100g.qcow2")

    # 3. Create Simulation
    # simulation.create_from_manifest(...)

    # 4. Jump Host & Network Prep
    # jumphost.ensure_ready(...)

    # 5. Cluster Installation Wait
    # cluster.wait_for_discovery(ctx.expected_hosts, ctx.discovery_timeout_s)

    print(f"Deployment complete. Kubeconfig saved to {ctx.cache_dir}")
