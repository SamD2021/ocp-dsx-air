from ocp_dsx_air.core.contracts import (
    AirImageAction,
    AirImageDecision,
    AirImageIntent,
    AirImageSnapshot,
    AirImageUploadStatus,
    AirNodeIntent,
    AirNodeSnapshot,
    AirSimulationAction,
    AirSimulationDecision,
    AirSimulationIntent,
    AirSimulationSnapshot,
    AirSimulationStatus,
    AssistedClusterIntent,
    AssistedClusterSnapshot,
    AssistedInfraEnvIntent,
    AssistedInfraEnvSnapshot,
    ClusterAction,
    ClusterDecision,
    ClusterStatus,
    CpuArchitecture,
    HostStatus,
    InfraEnvAction,
    InfraEnvDecision,
    InfraEnvImageType,
)

_AIR_IMAGE_UPLOAD_IN_PROGRESS = frozenset(
    {
        AirImageUploadStatus.UPLOADING,
        AirImageUploadStatus.VALIDATING,
    }
)

_AIR_IMAGE_UNSUPPORTED_STATUSES = frozenset(
    {
        AirImageUploadStatus.PUBLISHING,
        AirImageUploadStatus.UNPUBLISHING,
        AirImageUploadStatus.COPYING_FROM_IMAGE_SHARE,
        AirImageUploadStatus.PENDING_PUBLISH,
        AirImageUploadStatus.PENDING_UNPUBLISH,
    }
)

_AIR_SIMULATION_CREATING_STATUSES = frozenset(
    {
        AirSimulationStatus.CLONING,
        AirSimulationStatus.CREATING,
        AirSimulationStatus.IMPORTING,
        AirSimulationStatus.REQUESTING,
    }
)

_AIR_SIMULATION_STARTING_STATUSES = frozenset(
    {
        AirSimulationStatus.PROVISIONING,
        AirSimulationStatus.PREPARE_BOOT,
        AirSimulationStatus.BOOTING,
        AirSimulationStatus.PREPARE_REBUILD,
        AirSimulationStatus.REBUILDING,
    }
)

_AIR_SIMULATION_STOPPING_STATUSES = frozenset(
    {
        AirSimulationStatus.PREPARE_SHUTDOWN,
        AirSimulationStatus.SHUTTING_DOWN,
        AirSimulationStatus.SAVING,
    }
)

_AIR_SIMULATION_DELETING_STATUSES = frozenset(
    {
        AirSimulationStatus.PREPARE_TEARDOWN,
        AirSimulationStatus.TEARING_DOWN,
        AirSimulationStatus.DELETING,
        AirSimulationStatus.PREPARE_PURGE,
        AirSimulationStatus.PURGING,
    }
)

ACTION_HOST_STATUSES: frozenset[HostStatus] = frozenset(
    {
        HostStatus.ERROR,
        HostStatus.CANCELLED,
        HostStatus.INSTALLING_PENDING_USER_ACTION,
        HostStatus.RESETTING_PENDING_USER_ACTION,
        HostStatus.UNBINDING_PENDING_USER_ACTION,
    }
)

ACTION_CLUSTER_STATUSES: frozenset[ClusterStatus] = frozenset(
    {
        ClusterStatus.ERROR,
        ClusterStatus.CANCELLED,
        ClusterStatus.INSTALLING_PENDING_USER_ACTION,
    }
)


def decide_cluster_action(
    intent: AssistedClusterIntent,
    observed: AssistedClusterSnapshot | None,
    *,
    replace: bool,
) -> ClusterDecision:
    """Choose the next safe reconciliation action for an Assisted cluster.

    ``intent`` and ``observed`` must be validated, normalized domain objects.
    ``observed=None`` means no exact-name cluster exists. ``replace`` represents
    explicit authorization to recreate an existing cluster.

    The decision prioritizes explicit replacement, material drift, and durable
    installation markers before interpreting the reported lifecycle status. It
    performs no I/O or mutation; the caller must execute the returned action and
    observe the cluster again.
    """
    if observed is None:
        return ClusterDecision(action=ClusterAction.CREATE, reason="Cluster does not exist")

    if replace:
        return ClusterDecision(action=ClusterAction.REPLACE, reason="Replace the cluster")

    drift = find_material_drift(intent, observed)
    if drift:
        return ClusterDecision(action=ClusterAction.REFUSE_DRIFT, reason="Cluster is in a drifted state", drift=drift)

    if observed.install_completed:
        return ClusterDecision(action=ClusterAction.DOWNLOAD_CREDENTIALS, reason="Cluster is installed")

    match observed.status:
        case ClusterStatus.ERROR | ClusterStatus.CANCELLED | ClusterStatus.INSTALLING_PENDING_USER_ACTION:
            return ClusterDecision(action=ClusterAction.REFUSE_TERMINAL, reason="Cluster is in a terminal state")
        case ClusterStatus.READY if observed.install_started:
            return ClusterDecision(action=ClusterAction.WAIT_FOR_INSTALL, reason="Cluster is already installing")
        case ClusterStatus.READY:
            return ClusterDecision(action=ClusterAction.START_INSTALL, reason="Cluster is ready to start installation")
        case ClusterStatus.INSTALLING | ClusterStatus.FINALIZING | ClusterStatus.PREPARING_FOR_INSTALLATION:
            return ClusterDecision(action=ClusterAction.WAIT_FOR_INSTALL, reason="Cluster is installing")
        case ClusterStatus.INSTALLED:
            return ClusterDecision(action=ClusterAction.DOWNLOAD_CREDENTIALS, reason="Cluster is installed")
        case ClusterStatus.PENDING_FOR_INPUT | ClusterStatus.INSUFFICIENT:
            return ClusterDecision(action=ClusterAction.WAIT_FOR_HOSTS, reason="Cluster is pending for input")
        case ClusterStatus.ADDING_HOSTS | ClusterStatus.UNMONITORED:
            return ClusterDecision(action=ClusterAction.REFUSE_UNSUPPORTED, reason="Cluster is in an unsupported state")
        case ClusterStatus.UNKNOWN | _:
            return ClusterDecision(action=ClusterAction.REFUSE_UNKNOWN, reason="Cluster is in an unknown state")


def find_material_drift(
    intent: AssistedClusterIntent,
    observed: AssistedClusterSnapshot,
) -> tuple[str, ...]:
    """Find material drift between the intent and the observed cluster."""
    drift: list[str] = []

    if intent.ocp_version != observed.ocp_version:
        drift.append("ocp_version")

    if intent.base_dns_domain != observed.base_dns_domain:
        drift.append("base_dns_domain")

    if intent.architecture is not observed.architecture:
        drift.append("architecture")

    if intent.ntp_sources != observed.ntp_sources:
        drift.append("ntp_sources")

    if intent.high_availability != (observed.high_availability_mode == "Full"):
        drift.append("high_availability")

    if intent.control_plane_count != observed.control_plane_count:
        drift.append("control_plane_count")

    if intent.user_managed_networking != observed.user_managed_networking:
        drift.append("user_managed_networking")

    if intent.machine_networks != observed.machine_networks:
        drift.append("machine_networks")

    if intent.api_vips != observed.api_vips:
        drift.append("api_vips")

    if intent.ingress_vips != observed.ingress_vips:
        drift.append("ingress_vips")

    return tuple(drift)


def decide_infraenv_action(
    intent: AssistedInfraEnvIntent,
    observed: AssistedInfraEnvSnapshot | None,
    *,
    replace: bool,
    iso_cached: bool,
) -> InfraEnvDecision:
    """Choose the next safe reconciliation action for an InfraEnv."""
    if observed is None:
        return InfraEnvDecision(
            action=InfraEnvAction.CREATE,
            reason="InfraEnv does not exist",
        )

    if replace:
        return InfraEnvDecision(
            action=InfraEnvAction.REPLACE,
            reason="Replace the InfraEnv",
        )

    if observed.architecture is CpuArchitecture.UNKNOWN or observed.image_type is InfraEnvImageType.UNKNOWN:
        return InfraEnvDecision(
            action=InfraEnvAction.REFUSE_UNKNOWN,
            reason="InfraEnv contains unknown configuration",
        )

    drift = find_infraenv_material_drift(intent, observed)
    if drift:
        return InfraEnvDecision(
            action=InfraEnvAction.REFUSE_DRIFT,
            reason="InfraEnv is in a drifted state",
            drift=drift,
        )

    if not observed.iso_available:
        return InfraEnvDecision(
            action=InfraEnvAction.WAIT_FOR_ISO,
            reason="InfraEnv discovery ISO is not available yet",
        )

    if not iso_cached:
        return InfraEnvDecision(
            action=InfraEnvAction.DOWNLOAD_ISO,
            reason="Discovery ISO is available but not cached locally",
        )

    return InfraEnvDecision(
        action=InfraEnvAction.READY,
        reason="InfraEnv and cached discovery ISO are compatible",
    )


def find_infraenv_material_drift(
    intent: AssistedInfraEnvIntent,
    observed: AssistedInfraEnvSnapshot,
) -> tuple[str, ...]:
    drift: list[str] = []

    if intent.cluster_id != observed.cluster_id:
        drift.append("cluster_id")

    if intent.ocp_version != observed.ocp_version:
        drift.append("ocp_version")

    if intent.architecture is not observed.architecture:
        drift.append("architecture")

    if intent.image_type is not observed.image_type:
        drift.append("image_type")

    if intent.ntp_sources != observed.ntp_sources:
        drift.append("ntp_sources")

    if intent.ssh_authorized_key != observed.ssh_authorized_key:
        drift.append("ssh_authorized_key")

    if not observed.pull_secret_set:
        drift.append("pull_secret")

    return tuple(drift)


def decide_air_image_action(
    intent: AirImageIntent,
    observed: AirImageSnapshot | None,
    *,
    replace: bool,
) -> AirImageDecision:
    """Choose the next safe reconciliation action for a managed Air image."""
    if observed is None:
        return AirImageDecision(
            action=AirImageAction.CREATE,
            reason="Air image does not exist",
        )

    if replace:
        return AirImageDecision(
            action=AirImageAction.REPLACE,
            reason="Replace the Air image",
        )

    if observed.architecture is CpuArchitecture.UNKNOWN or observed.upload_status is AirImageUploadStatus.UNKNOWN:
        return AirImageDecision(
            action=AirImageAction.REFUSE_UNKNOWN,
            reason="Air image contains unknown configuration",
        )

    if observed.upload_status in _AIR_IMAGE_UNSUPPORTED_STATUSES:
        return AirImageDecision(
            action=AirImageAction.REFUSE_UNSUPPORTED,
            reason="Air image is in an unsupported lifecycle state",
        )

    drift = find_air_image_material_drift(intent, observed)
    if drift:
        return AirImageDecision(
            action=AirImageAction.REFUSE_DRIFT,
            reason="Air image is in a drifted state",
            drift=drift,
        )

    if observed.upload_status is AirImageUploadStatus.READY:
        return AirImageDecision(
            action=AirImageAction.UPLOAD,
            reason="Air image record is ready for content upload",
        )

    if observed.upload_status in _AIR_IMAGE_UPLOAD_IN_PROGRESS:
        return AirImageDecision(
            action=AirImageAction.WAIT_FOR_UPLOAD,
            reason="Air image upload is in progress",
        )

    if observed.upload_status is AirImageUploadStatus.COMPLETE:
        return AirImageDecision(
            action=AirImageAction.READY,
            reason="Air image content is complete and compatible",
        )

    return AirImageDecision(
        action=AirImageAction.REFUSE_UNKNOWN,
        reason="Air image is in an unknown lifecycle state",
    )


def find_air_image_material_drift(
    intent: AirImageIntent,
    observed: AirImageSnapshot,
) -> tuple[str, ...]:
    """Return incompatible Air image fields in deterministic order."""
    drift: list[str] = []

    if intent.version != observed.version:
        drift.append("version")
    if intent.architecture is not observed.architecture:
        drift.append("architecture")
    if intent.provider != observed.provider:
        drift.append("provider")
    if not observed.owned_by_client:
        drift.append("owned_by_client")

    if observed.upload_status is AirImageUploadStatus.COMPLETE:
        if intent.source_size_bytes != observed.size_bytes:
            drift.append("source_size_bytes")
        if intent.source_sha256 != observed.sha256:
            drift.append("source_sha256")

    return tuple(drift)


def decide_air_simulation_action(
    intent: AirSimulationIntent,
    observed: AirSimulationSnapshot | None,
    *,
    replace: bool,
) -> AirSimulationDecision:
    """Choose one safe next action for an Air simulation reconciliation."""
    if observed is None:
        return AirSimulationDecision(
            action=AirSimulationAction.IMPORT,
            reason="Air simulation does not exist",
        )

    status = observed.status
    if status is AirSimulationStatus.UNKNOWN:
        return AirSimulationDecision(
            action=AirSimulationAction.REFUSE_UNKNOWN,
            reason="Air simulation is in an unknown lifecycle state",
        )

    if status in _AIR_SIMULATION_DELETING_STATUSES:
        return AirSimulationDecision(
            action=AirSimulationAction.WAIT_FOR_DELETION,
            reason="Air simulation deletion is in progress",
        )

    if status in _AIR_SIMULATION_CREATING_STATUSES:
        return AirSimulationDecision(
            action=AirSimulationAction.WAIT_FOR_CREATION,
            reason="Air simulation creation is in progress",
        )

    if status in _AIR_SIMULATION_STARTING_STATUSES:
        return AirSimulationDecision(
            action=AirSimulationAction.WAIT_FOR_ACTIVE,
            reason="Air simulation is starting",
        )

    if status in _AIR_SIMULATION_STOPPING_STATUSES:
        return AirSimulationDecision(
            action=AirSimulationAction.WAIT_FOR_INACTIVE,
            reason="Air simulation is stopping",
        )

    if replace:
        if status is AirSimulationStatus.ACTIVE:
            return AirSimulationDecision(
                action=AirSimulationAction.SHUTDOWN_FOR_REPLACEMENT,
                reason="Stop the Air simulation before replacement",
            )
        return AirSimulationDecision(
            action=AirSimulationAction.DELETE_FOR_REPLACEMENT,
            reason="Delete the stopped Air simulation for replacement",
        )

    if status is AirSimulationStatus.INVALID:
        return AirSimulationDecision(
            action=AirSimulationAction.REFUSE_TERMINAL,
            reason="Air simulation is invalid",
        )

    if status in {AirSimulationStatus.DEMO, AirSimulationStatus.TRAINING}:
        return AirSimulationDecision(
            action=AirSimulationAction.REFUSE_UNSUPPORTED,
            reason="Air simulation is not a mutable lab simulation",
        )

    if _air_simulation_contains_unknown_configuration(intent, observed):
        return AirSimulationDecision(
            action=AirSimulationAction.REFUSE_UNKNOWN,
            reason="Air simulation contains unknown node configuration",
        )

    drift = find_air_simulation_material_drift(intent, observed)
    if drift:
        return AirSimulationDecision(
            action=AirSimulationAction.REFUSE_DRIFT,
            reason="Air simulation is in a drifted state",
            drift=drift,
        )

    if status is AirSimulationStatus.INACTIVE:
        return AirSimulationDecision(
            action=AirSimulationAction.START,
            reason="Air simulation is ready to start",
        )

    if status is AirSimulationStatus.ACTIVE:
        return AirSimulationDecision(
            action=AirSimulationAction.READY,
            reason="Air simulation is active and compatible",
        )

    return AirSimulationDecision(
        action=AirSimulationAction.REFUSE_UNKNOWN,
        reason="Air simulation has no safe reconciliation action",
    )


def _air_simulation_contains_unknown_configuration(
    intent: AirSimulationIntent,
    observed: AirSimulationSnapshot,
) -> bool:
    expected_names = {node.name for node in intent.nodes}
    return any(
        node.name in expected_names
        and (node.cpu_mode.value == "unknown" or any(device.value == "unknown" for device in node.boot_order))
        for node in observed.nodes
    )


def find_air_simulation_material_drift(
    intent: AirSimulationIntent,
    observed: AirSimulationSnapshot,
) -> tuple[str, ...]:
    """Return incompatible simulation and managed-node fields."""
    drift: list[str] = []

    if intent.auto_oob_enabled != observed.auto_oob_enabled:
        drift.append("auto_oob_enabled")
    if intent.enable_dhcp != observed.enable_dhcp:
        drift.append("enable_dhcp")
    if not observed.managed_by_us:
        drift.append("managed_by_us")
    if intent.metadata_schema != observed.metadata_schema:
        drift.append("metadata_schema")
    if intent.topology_sha256 != observed.topology_sha256:
        drift.append("topology_sha256")

    intended_names = tuple(sorted(node.name for node in intent.nodes))
    if intended_names != observed.managed_node_names:
        drift.append("managed_node_names")

    observed_by_name = {node.name: node for node in observed.nodes}
    for intended_node in sorted(intent.nodes, key=lambda node: node.name):
        observed_node = observed_by_name.get(intended_node.name)
        if observed_node is None:
            drift.append(f"nodes.{intended_node.name}")
            continue
        drift.extend(_find_air_node_material_drift(intended_node, observed_node))

    return tuple(drift)


def _find_air_node_material_drift(
    intent: AirNodeIntent,
    observed: AirNodeSnapshot,
) -> tuple[str, ...]:
    fields = (
        "cpu",
        "memory_mib",
        "storage_gib",
        "base_image_id",
        "discovery_image_id",
        "boot_order",
        "cpu_mode",
        "nic_model",
        "uefi",
        "secureboot",
    )
    return tuple(
        f"nodes.{intent.name}.{field}" for field in fields if getattr(intent, field) != getattr(observed, field)
    )
