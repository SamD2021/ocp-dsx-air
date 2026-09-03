"""Synchronous deployment reconciliation."""

from ipaddress import IPv4Address, ip_network
from pathlib import Path

from ocp_dsx_air.adapters.air.artifacts import (
    ensure_blank_disk,
    inspect_local_artifact,
)
from ocp_dsx_air.core.builders import (
    build_blank_image_intent,
    build_discovery_image_intent,
    build_infraenv_intent,
    build_simulation_intent,
)
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
    DeployIntent,
    DeploymentEvent,
    DeploymentPhase,
    DeploymentResult,
    DeployNodeIntent,
    InfraEnvAction,
    JumpHostSnapshot,
    Severity,
)
from ocp_dsx_air.core.decisions import (
    ACTION_CLUSTER_STATUSES,
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
from ocp_dsx_air.core.iso import (
    air_discovery_image_name,
    discovery_infraenv_name,
    discovery_iso_is_cached,
    discovery_iso_path,
)
from ocp_dsx_air.core.polling import find_poll_issues, poll_interval_seconds
from ocp_dsx_air.core.ports.air import AirPort
from ocp_dsx_air.core.ports.assisted import AssistedInstallerPort
from ocp_dsx_air.core.ports.jump_host import JumpHostPort
from ocp_dsx_air.core.runtime import Clock, DeploymentReporter
from ocp_dsx_air.models.runtime import ClusterNetworkConfig, ResolvedCredentials


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
    cache_root: Path,
    replace: bool,
    timeout_seconds: float,
    poll_interval_seconds: float,
) -> tuple[AssistedInfraEnvSnapshot, Path]:
    """Return a compatible InfraEnv and downloaded discovery ISO."""
    deadline = clock.monotonic() + timeout_seconds
    replace_pending = replace
    while True:
        observed = assisted.find_infraenv(intent.name)
        iso_path = (
            discovery_iso_path(cache_root, observed.id)
            if observed is not None
            else cache_root / ".pending-discovery.iso"
        )
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
        if host.status.value == "unknown":
            raise ClusterInstallError(
                f"Assisted returned an unknown status for host {host.id}"
            )
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
    if len(claimed) != len(hosts):
        return None
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
        current_cluster = assisted.find_cluster(cluster.name)
        if current_cluster is None or current_cluster.id != cluster.id:
            raise ClusterInstallError("Assisted cluster changed during host discovery")
        if current_cluster.status in ACTION_CLUSTER_STATUSES:
            raise ClusterInstallError(
                f"Cluster entered {current_cluster.status.value!r}: "
                f"{current_cluster.status_info}"
            )
        if current_cluster.status.value in {"unknown", "adding-hosts", "unmonitored"}:
            raise ClusterInstallError(
                f"Cluster entered unsupported state {current_cluster.status.value!r}"
            )
        accepted_statuses = {"known", "ready"}
        if current_cluster.install_started:
            accepted_statuses.update(
                {"installing", "installing-in-progress", "installed"}
            )
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
                try:
                    assisted.update_host_role(host.infraenv_id, host.id, node.role)
                except AssistedError as exc:
                    if exc.status_code in {400, 401, 403, 404, 422}:
                        raise ClusterInstallError(
                            f"Assisted refused the role assignment for {node.name!r}"
                        ) from exc
                    _emit(
                        reporter,
                        DeploymentPhase.HOST_DISCOVERY,
                        f"Retrying role assignment for host {node.name!r}",
                        action="retry-role-update",
                        resource_id=host.id,
                    )
                else:
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
                host.status.value in accepted_statuses for _, host in matches
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


def _sleep_for_replacement(
    clock: Clock,
    deadline: float,
    interval: float,
    resource: str,
) -> None:
    _wait_or_timeout(
        clock=clock,
        deadline=deadline,
        interval=interval,
        resource=resource,
        error_type=AirSimError,
    )


def _teardown_managed_stack(
    intent: DeployIntent,
    *,
    assisted: AssistedInstallerPort,
    air: AirPort,
    reporter: DeploymentReporter,
    clock: Clock,
) -> None:
    """Delete the replaceable stack in dependency order, preserving blank media."""
    cluster = assisted.find_cluster(intent.cluster.name)
    infraenv_name = discovery_infraenv_name(intent.cluster.name)
    infraenv = assisted.find_infraenv(infraenv_name)
    simulation = air.find_simulation(intent.simulation_name)
    discovery_image_name = (
        air_discovery_image_name(infraenv.id) if infraenv is not None else None
    )
    discovery_image = (
        air.find_image(discovery_image_name)
        if discovery_image_name is not None
        else None
    )
    if simulation is not None and not simulation.managed_by_us:
        raise AirSimError("Refusing to replace an unmanaged Air simulation")
    if discovery_image is not None and not discovery_image.owned_by_client:
        raise AirImageError("Refusing to replace an unmanaged Air image")
    deadline = clock.monotonic() + intent.timeouts.resource_seconds

    while simulation is not None:
        if simulation.status.value == "ACTIVE":
            air.shutdown_simulation(simulation.id, create_checkpoint=False)
            _emit(
                reporter,
                DeploymentPhase.SIMULATION,
                "Stopped simulation for full replacement",
                action="shutdown-for-replacement",
                resource_id=simulation.id,
            )
        elif simulation.status.value in {"INACTIVE", "INVALID"}:
            air.delete_simulation(simulation.id)
            _emit(
                reporter,
                DeploymentPhase.SIMULATION,
                "Deleted simulation for full replacement",
                action="delete-for-replacement",
                resource_id=simulation.id,
            )
        elif simulation.status.value in {
            "CLONING",
            "CREATING",
            "IMPORTING",
            "REQUESTING",
            "PROVISIONING",
            "PREPARE_BOOT",
            "BOOTING",
            "PREPARE_SHUTDOWN",
            "SHUTTING_DOWN",
            "SAVING",
            "PREPARE_TEARDOWN",
            "TEARING_DOWN",
            "DELETING",
            "PREPARE_PURGE",
            "PURGING",
        }:
            pass
        else:
            raise AirSimError(
                "Air simulation cannot be safely removed from its current state"
            )
        _sleep_for_replacement(
            clock,
            deadline,
            intent.timeouts.normal_poll_seconds,
            "Air simulation replacement",
        )
        simulation = air.find_simulation(intent.simulation_name)

    if discovery_image is not None:
        air.delete_image(discovery_image.id)
        _emit(
            reporter,
            DeploymentPhase.AIR_IMAGES,
            "Deleted discovery image for full replacement",
            action="delete-for-replacement",
            resource_id=discovery_image.id,
        )
        while air.find_image(discovery_image.name) is not None:
            _sleep_for_replacement(
                clock,
                deadline,
                intent.timeouts.normal_poll_seconds,
                "Air discovery image deletion",
            )

    if infraenv is not None:
        assisted.delete_infraenv(infraenv.id)
        _emit(
            reporter,
            DeploymentPhase.INFRAENV,
            "Deleted InfraEnv for full replacement",
            action="delete-for-replacement",
            resource_id=infraenv.id,
        )
        while assisted.find_infraenv(infraenv.name) is not None:
            _wait_or_timeout(
                clock=clock,
                deadline=deadline,
                interval=intent.timeouts.normal_poll_seconds,
                resource="InfraEnv deletion",
                error_type=AssistedError,
            )

    if cluster is not None:
        assisted.delete_cluster(cluster.id)
        _emit(
            reporter,
            DeploymentPhase.CLUSTER,
            "Deleted cluster for full replacement",
            action="delete-for-replacement",
            resource_id=cluster.id,
        )
        while assisted.find_cluster(cluster.name) is not None:
            _wait_or_timeout(
                clock=clock,
                deadline=deadline,
                interval=intent.timeouts.normal_poll_seconds,
                resource="Assisted cluster deletion",
                error_type=AssistedError,
            )


def _preflight_air_ownership(
    intent: DeployIntent,
    *,
    assisted: AssistedInstallerPort,
    air: AirPort,
) -> None:
    """Reject colliding unmanaged Air resources before creating anything."""
    simulation = air.find_simulation(intent.simulation_name)
    if simulation is not None and not simulation.managed_by_us:
        raise AirSimError("Refusing an unmanaged same-name Air simulation")
    infraenv = assisted.find_infraenv(discovery_infraenv_name(intent.cluster.name))
    if infraenv is None:
        return
    image = air.find_image(air_discovery_image_name(infraenv.id))
    if image is not None and not image.owned_by_client:
        raise AirImageError("Refusing an unmanaged same-name Air image")


def _cluster_network_config(
    intent: DeployIntent,
    hosts: tuple[AssistedHostSnapshot, ...],
) -> ClusterNetworkConfig:
    if intent.cluster.api_vips and intent.cluster.ingress_vips:
        api_address = intent.cluster.api_vips[0]
        ingress_address = intent.cluster.ingress_vips[0]
    else:
        machine_networks = tuple(
            ip_network(cidr, strict=True) for cidr in intent.cluster.machine_networks
        )
        master_hosts = {
            node.name
            for node in intent.nodes
            if node.role.value == "master"
        }
        master = next(
            (
                host
                for host in hosts
                if (host.requested_hostname or host.inventory_hostname) in master_hosts
            ),
            None,
        )
        if master is None:
            raise ClusterInstallError("Could not identify the single-node host")
        address = next(
            (
                address
                for address in master.ipv4_addresses
                if any(address in network for network in machine_networks)
            ),
            None,
        )
        if not isinstance(address, IPv4Address):
            raise ClusterInstallError(
                "The single-node host has no address in the machine network"
            )
        api_address = ingress_address = str(address)
    return ClusterNetworkConfig(
        cluster_name=intent.cluster.name,
        base_dns_domain=intent.cluster.base_dns_domain,
        api_vip=api_address,
        ingress_vip=ingress_address,
    )


def deploy_lab(
    intent: DeployIntent,
    *,
    credentials: ResolvedCredentials,
    assisted: AssistedInstallerPort,
    air: AirPort,
    jump_host: JumpHostPort,
    reporter: DeploymentReporter,
    clock: Clock,
    replace: bool = False,
) -> DeploymentResult:
    """Reconcile and install one complete managed OpenShift-on-Air lab."""
    if replace:
        _teardown_managed_stack(
            intent,
            assisted=assisted,
            air=air,
            reporter=reporter,
            clock=clock,
        )
    else:
        _preflight_air_ownership(intent, assisted=assisted, air=air)

    cluster = _reconcile_cluster(
        intent.cluster,
        assisted=assisted,
        reporter=reporter,
        clock=clock,
        pull_secret=credentials.pull_secret,
        ssh_public_key=credentials.ssh_public_key,
        replace=False,
        timeout_seconds=intent.timeouts.resource_seconds,
        poll_interval_seconds=intent.timeouts.normal_poll_seconds,
    )
    infraenv_intent = build_infraenv_intent(
        intent,
        cluster,
        ssh_authorized_key=credentials.ssh_public_key,
    )
    infraenv, iso_path = _reconcile_infraenv(
        infraenv_intent,
        assisted=assisted,
        reporter=reporter,
        clock=clock,
        pull_secret=credentials.pull_secret,
        cache_root=intent.cache_root,
        replace=False,
        timeout_seconds=intent.timeouts.resource_seconds,
        poll_interval_seconds=intent.timeouts.normal_poll_seconds,
    )
    discovery_artifact = inspect_local_artifact(iso_path)
    blank_artifact = ensure_blank_disk(intent.cache_root, intent.blank_disk)
    discovery_image_intent = build_discovery_image_intent(
        intent,
        infraenv.id,
        discovery_artifact,
    )
    blank_image_intent = build_blank_image_intent(intent.blank_disk, blank_artifact)
    existing_blank = air.find_image(blank_image_intent.name)
    blank_decision = decide_air_image_action(
        blank_image_intent,
        existing_blank,
        replace=False,
    )
    replace_blank = replace and blank_decision.action is AirImageAction.REFUSE_DRIFT
    blank_image = _reconcile_air_image(
        blank_image_intent,
        source=blank_artifact.path,
        air=air,
        reporter=reporter,
        clock=clock,
        replace=replace_blank,
        timeout_seconds=intent.timeouts.resource_seconds,
        poll_interval_seconds=intent.timeouts.normal_poll_seconds,
    )
    discovery_image = _reconcile_air_image(
        discovery_image_intent,
        source=discovery_artifact.path,
        air=air,
        reporter=reporter,
        clock=clock,
        replace=False,
        timeout_seconds=intent.timeouts.resource_seconds,
        poll_interval_seconds=intent.timeouts.normal_poll_seconds,
    )
    simulation_intent = build_simulation_intent(
        intent,
        blank_image=blank_image,
        discovery_image=discovery_image,
    )
    observed_simulation = air.find_simulation(intent.simulation_name)
    if cluster.install_started and observed_simulation is None:
        raise AirSimError(
            "The Air simulation is missing after installation started; "
            "use full replacement to recreate the lab"
        )
    simulation = _reconcile_simulation(
        simulation_intent,
        air=air,
        reporter=reporter,
        clock=clock,
        replace=False,
        timeout_seconds=intent.timeouts.resource_seconds,
        poll_interval_seconds=intent.timeouts.normal_poll_seconds,
    )
    hosts = _reconcile_hosts(
        cluster,
        intent.nodes,
        assisted=assisted,
        reporter=reporter,
        clock=clock,
        timeout_seconds=intent.timeouts.discovery_seconds,
        normal_poll_seconds=intent.timeouts.normal_poll_seconds,
        fast_poll_seconds=intent.timeouts.fast_poll_seconds,
    )
    network = _cluster_network_config(intent, hosts)
    jump_host_snapshot: JumpHostSnapshot = jump_host.ensure_jump_host(
        simulation.id,
        network,
        new_password=credentials.jump_host_password,
        timeout_seconds=intent.timeouts.jump_host_seconds,
    )
    _emit(
        reporter,
        DeploymentPhase.JUMP_HOST,
        "Jump host is ready",
        action="ensure-ready",
        resource_id=jump_host_snapshot.service_id,
    )
    final_cluster, credential_paths = _reconcile_installation(
        intent.cluster,
        assisted=assisted,
        reporter=reporter,
        clock=clock,
        credentials_dir=intent.cache_root / "credentials",
        timeout_seconds=intent.timeouts.installation_seconds,
        normal_poll_seconds=intent.timeouts.normal_poll_seconds,
        fast_poll_seconds=intent.timeouts.fast_poll_seconds,
    )
    final_hosts = assisted.list_hosts(final_cluster.id)
    return DeploymentResult(
        cluster=final_cluster,
        infraenv=infraenv,
        discovery_image=discovery_image,
        blank_image=blank_image,
        simulation=simulation,
        hosts=final_hosts,
        jump_host=jump_host_snapshot,
        credentials=credential_paths,
    )
