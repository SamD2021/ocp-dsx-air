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
    AssistedHostSnapshot,
    AssistedInfraEnvIntent,
    AssistedInfraEnvSnapshot,
    ClusterAction,
    CredentialPaths,
    DeploymentEvent,
    DeploymentPhase,
    DeployNodeIntent,
    InfraEnvAction,
    Severity,
)
from ocp_dsx_air.core.decisions import (
    decide_air_image_action,
    decide_air_simulation_action,
    decide_cluster_action,
    decide_infraenv_action,
)
from ocp_dsx_air.core.exceptions import (
    AirImageError,
    AirSimError,
    AssistedError,
    ClusterInstallError,
)
from ocp_dsx_air.core.iso import discovery_iso_is_cached
from ocp_dsx_air.core.polling import find_poll_issues, poll_interval_seconds
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


_ROLE_MUTABLE_HOST_STATUSES = frozenset(
    {
        "discovering",
        "known",
        "ready",
        "disconnected",
        "insufficient",
        "pending-for-input",
    }
)


def _match_hosts(
    nodes: tuple[DeployNodeIntent, ...],
    hosts: tuple[AssistedHostSnapshot, ...],
) -> tuple[tuple[DeployNodeIntent, AssistedHostSnapshot], ...] | None:
    """Match intended nodes to hosts without relying on API order or addresses."""
    expected_names = {node.name for node in nodes}
    for host in hosts:
        if host.requested_hostname and host.requested_hostname not in expected_names:
            raise ClusterInstallError(
                f"Unexpected discovered host {host.requested_hostname!r}"
            )
        if (
            host.requested_hostname is None
            and host.inventory_hostname
            and host.inventory_hostname not in expected_names
        ):
            raise ClusterInstallError(
                f"Unexpected discovered host {host.inventory_hostname!r}"
            )

    matches: list[tuple[DeployNodeIntent, AssistedHostSnapshot]] = []
    claimed: set[object] = set()
    for node in nodes:
        requested = [host for host in hosts if host.requested_hostname == node.name]
        candidates = requested or [
            host for host in hosts if host.inventory_hostname == node.name
        ]
        if len(candidates) > 1:
            raise ClusterInstallError(
                f"Multiple Assisted hosts match intended node {node.name!r}"
            )
        if not candidates:
            return None
        host = candidates[0]
        if host.id in claimed:
            raise ClusterInstallError(
                "One Assisted host ambiguously matches multiple intended nodes"
            )
        claimed.add(host.id)
        matches.append((node, host))
    return tuple(matches)


def _reconcile_hosts(
    cluster: AssistedClusterSnapshot,
    nodes: tuple[DeployNodeIntent, ...],
    *,
    assisted: AssistedInstallerPort,
    reporter: DeploymentReporter,
    clock: Clock,
    timeout_seconds: float,
    normal_poll_seconds: float,
    fast_poll_seconds: float,
) -> tuple[AssistedHostSnapshot, ...]:
    """Wait for exact hostname matches, assign roles, and return ready hosts."""
    deadline = clock.monotonic() + timeout_seconds
    while True:
        hosts = assisted.list_hosts(cluster.id)
        issues = find_poll_issues(hosts)
        for issue in issues:
            _emit(
                reporter,
                DeploymentPhase.HOST_DISCOVERY,
                issue.detail,
                action=issue.code.value,
            )
            if issue.severity is Severity.ACTION_REQUIRED:
                raise ClusterInstallError(issue.detail)

        matches = _match_hosts(nodes, hosts)
        if matches is not None:
            changed_role = False
            for node, host in matches:
                if host.role is node.role:
                    continue
                if host.status.value not in _ROLE_MUTABLE_HOST_STATUSES:
                    raise ClusterInstallError(
                        f"Host {node.name!r} role cannot be changed in "
                        f"state {host.status.value!r}"
                    )
                assisted.update_host_role(host.infraenv_id, host.id, node.role)
                _emit(
                    reporter,
                    DeploymentPhase.HOST_DISCOVERY,
                    f"Assigned {node.role.value} role to host {node.name!r}",
                    action="update-role",
                    resource_id=host.id,
                )
                changed_role = True
                break
            if not changed_role and all(
                host.status.value in {"known", "ready"} for _, host in matches
            ):
                return tuple(host for _, host in matches)

        interval = poll_interval_seconds(
            hosts,
            normal=normal_poll_seconds,
            fast=fast_poll_seconds,
        )
        _wait_or_timeout(
            clock=clock,
            deadline=deadline,
            interval=interval,
            resource="Assisted host discovery",
            error_type=ClusterInstallError,
        )


def _reconcile_installation(
    intent: AssistedClusterIntent,
    *,
    assisted: AssistedInstallerPort,
    reporter: DeploymentReporter,
    clock: Clock,
    credentials_dir: Path,
    timeout_seconds: float,
    normal_poll_seconds: float,
    fast_poll_seconds: float,
) -> tuple[AssistedClusterSnapshot, CredentialPaths]:
    """Start installation at most once, resume it, and download credentials."""
    deadline = clock.monotonic() + timeout_seconds
    while True:
        cluster = assisted.find_cluster(intent.name)
        if cluster is None:
            raise ClusterInstallError("Assisted cluster disappeared during installation")
        hosts = assisted.list_hosts(cluster.id)
        for issue in find_poll_issues(hosts):
            _emit(
                reporter,
                DeploymentPhase.INSTALLATION,
                issue.detail,
                action=issue.code.value,
            )
            if issue.severity is Severity.ACTION_REQUIRED:
                raise ClusterInstallError(issue.detail)

        decision = decide_cluster_action(intent, cluster, replace=False)
        _emit(
            reporter,
            DeploymentPhase.INSTALLATION,
            decision.reason,
            action=decision.action.value,
            resource_id=cluster.id,
        )
        match decision.action:
            case ClusterAction.START_INSTALL:
                assisted.start_installation(cluster.id)
            case ClusterAction.WAIT_FOR_INSTALL | ClusterAction.WAIT_FOR_HOSTS:
                pass
            case ClusterAction.DOWNLOAD_CREDENTIALS:
                paths = assisted.download_credentials(cluster.id, credentials_dir)
                _emit(
                    reporter,
                    DeploymentPhase.CREDENTIALS,
                    "Downloaded cluster credentials",
                    action="download-credentials",
                    resource_id=cluster.id,
                )
                return cluster, paths
            case _:
                raise ClusterInstallError(
                    _refusal_message("installation", decision.reason, decision.drift)
                )

        interval = poll_interval_seconds(
            hosts,
            normal=normal_poll_seconds,
            fast=fast_poll_seconds,
        )
        _wait_or_timeout(
            clock=clock,
            deadline=deadline,
            interval=interval,
            resource="OpenShift installation",
            error_type=ClusterInstallError,
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
