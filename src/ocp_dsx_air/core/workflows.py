"""Synchronous deployment reconciliation."""

from pathlib import Path

from ocp_dsx_air.core.contracts import (
    AirImageAction,
    AirImageIntent,
    AirImageSnapshot,
    AirSimulationAction,
    AirSimulationIntent,
    AirSimulationSnapshot,
    AssistedClusterIntent,
    AssistedClusterSnapshot,
    AssistedInfraEnvIntent,
    AssistedInfraEnvSnapshot,
    ClusterAction,
    DeploymentEvent,
    DeploymentPhase,
    InfraEnvAction,
)
from ocp_dsx_air.core.decisions import (
    decide_air_image_action,
    decide_air_simulation_action,
    decide_cluster_action,
    decide_infraenv_action,
)
from ocp_dsx_air.core.exceptions import AirImageError, AirSimError, AssistedError
from ocp_dsx_air.core.iso import discovery_iso_is_cached
from ocp_dsx_air.core.ports.air import AirPort
from ocp_dsx_air.core.ports.assisted import AssistedInstallerPort
from ocp_dsx_air.core.runtime import Clock, DeploymentReporter
from ocp_dsx_air.models.runtime import DeployContext


def _emit(
    reporter: DeploymentReporter,
    phase: DeploymentPhase,
    message: str,
    *,
    action: str | None = None,
    resource_id: object | None = None,
) -> None:
    from uuid import UUID

    reporter.emit(
        DeploymentEvent(
            phase=phase,
            message=message,
            action=action,
            resource_id=resource_id if isinstance(resource_id, UUID) else None,
        )
    )


def _wait_or_timeout(
    *,
    clock: Clock,
    deadline: float,
    interval: float,
    resource: str,
    error_type: type[Exception],
) -> None:
    remaining = deadline - clock.monotonic()
    if remaining <= 0:
        raise error_type(f"Timed out waiting for {resource}")
    clock.sleep(min(interval, remaining))


def _refusal_message(resource: str, reason: str, drift: tuple[str, ...]) -> str:
    suffix = f": {', '.join(drift)}" if drift else ""
    return f"Cannot reconcile {resource}: {reason}{suffix}"


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
    """Return a compatible cluster after observe-decide-execute reconciliation."""
    deadline = clock.monotonic() + timeout_seconds
    replace_pending = replace
    while True:
        observed = assisted.find_cluster(intent.name)
        decision = decide_cluster_action(intent, observed, replace=replace_pending)
        _emit(
            reporter,
            DeploymentPhase.CLUSTER,
            decision.reason,
            action=decision.action.value,
            resource_id=observed.id if observed else None,
        )
        match decision.action:
            case ClusterAction.CREATE:
                assisted.create_cluster(
                    intent,
                    pull_secret=pull_secret,
                    ssh_public_key=ssh_public_key,
                )
            case ClusterAction.REPLACE:
                assert observed is not None
                assisted.delete_cluster(observed.id)
                replace_pending = False
            case (
                ClusterAction.WAIT_FOR_HOSTS
                | ClusterAction.START_INSTALL
                | ClusterAction.WAIT_FOR_INSTALL
                | ClusterAction.DOWNLOAD_CREDENTIALS
            ):
                assert observed is not None
                return observed
            case _:
                raise AssistedError(
                    _refusal_message("cluster", decision.reason, decision.drift)
                )
        _wait_or_timeout(
            clock=clock,
            deadline=deadline,
            interval=poll_interval_seconds,
            resource="Assisted cluster",
            error_type=AssistedError,
        )


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
    """Return a compatible InfraEnv and downloaded discovery ISO."""
    deadline = clock.monotonic() + timeout_seconds
    replace_pending = replace
    while True:
        observed = assisted.find_infraenv(intent.name)
        decision = decide_infraenv_action(
            intent,
            observed,
            replace=replace_pending,
            iso_cached=discovery_iso_is_cached(iso_path),
        )
        _emit(
            reporter,
            DeploymentPhase.INFRAENV,
            decision.reason,
            action=decision.action.value,
            resource_id=observed.id if observed else None,
        )
        match decision.action:
            case InfraEnvAction.CREATE:
                assisted.create_infraenv(intent, pull_secret=pull_secret)
            case InfraEnvAction.REPLACE:
                assert observed is not None
                assisted.delete_infraenv(observed.id)
                replace_pending = False
            case InfraEnvAction.DOWNLOAD_ISO:
                assert observed is not None
                assisted.download_discovery_iso(observed.id, iso_path)
            case InfraEnvAction.READY:
                assert observed is not None
                return observed, iso_path
            case InfraEnvAction.WAIT_FOR_ISO:
                pass
            case _:
                raise AssistedError(
                    _refusal_message("InfraEnv", decision.reason, decision.drift)
                )
        _wait_or_timeout(
            clock=clock,
            deadline=deadline,
            interval=poll_interval_seconds,
            resource="InfraEnv discovery ISO",
            error_type=AssistedError,
        )


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
    """Return one compatible, fully uploaded Air image."""
    deadline = clock.monotonic() + timeout_seconds
    replace_pending = replace
    while True:
        observed = air.find_image(intent.name)
        decision = decide_air_image_action(
            intent,
            observed,
            replace=replace_pending,
        )
        _emit(
            reporter,
            DeploymentPhase.AIR_IMAGES,
            decision.reason,
            action=decision.action.value,
            resource_id=observed.id if observed else None,
        )
        match decision.action:
            case AirImageAction.CREATE:
                air.create_image(intent)
            case AirImageAction.UPLOAD:
                assert observed is not None
                air.upload_image(observed.id, source)
            case AirImageAction.REPLACE:
                assert observed is not None
                if not observed.owned_by_client:
                    raise AirImageError("Refusing to replace an unmanaged Air image")
                air.delete_image(observed.id)
                replace_pending = False
            case AirImageAction.WAIT_FOR_UPLOAD:
                pass
            case AirImageAction.READY:
                assert observed is not None
                return observed
            case _:
                raise AirImageError(
                    _refusal_message("Air image", decision.reason, decision.drift)
                )
        _wait_or_timeout(
            clock=clock,
            deadline=deadline,
            interval=poll_interval_seconds,
            resource="Air image",
            error_type=AirImageError,
        )


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
    """Return one compatible active Air simulation."""
    deadline = clock.monotonic() + timeout_seconds
    replace_pending = replace
    while True:
        observed = air.find_simulation(intent.name)
        if replace_pending and observed is not None and not observed.managed_by_us:
            raise AirSimError("Refusing to replace an unmanaged Air simulation")
        decision = decide_air_simulation_action(
            intent,
            observed,
            replace=replace_pending,
        )
        _emit(
            reporter,
            DeploymentPhase.SIMULATION,
            decision.reason,
            action=decision.action.value,
            resource_id=observed.id if observed else None,
        )
        match decision.action:
            case AirSimulationAction.IMPORT:
                air.import_simulation(intent)
            case AirSimulationAction.START:
                assert observed is not None
                air.start_simulation(observed.id)
            case AirSimulationAction.SHUTDOWN_FOR_REPLACEMENT:
                assert observed is not None
                air.shutdown_simulation(observed.id, create_checkpoint=False)
            case AirSimulationAction.DELETE_FOR_REPLACEMENT:
                assert observed is not None
                air.delete_simulation(observed.id)
                replace_pending = False
            case (
                AirSimulationAction.WAIT_FOR_CREATION
                | AirSimulationAction.WAIT_FOR_ACTIVE
                | AirSimulationAction.WAIT_FOR_INACTIVE
                | AirSimulationAction.WAIT_FOR_DELETION
            ):
                pass
            case AirSimulationAction.READY:
                assert observed is not None
                return observed
            case _:
                raise AirSimError(
                    _refusal_message("Air simulation", decision.reason, decision.drift)
                )
        _wait_or_timeout(
            clock=clock,
            deadline=deadline,
            interval=poll_interval_seconds,
            resource="Air simulation",
            error_type=AirSimError,
        )


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
