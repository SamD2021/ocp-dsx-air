from ocp_dsx_air.core.contracts import (
    AssistedClusterIntent,
    AssistedClusterSnapshot,
    ClusterAction,
    ClusterDecision,
    ClusterStatus,
    HostStatus,
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

    if intent.high_availability != (
        observed.high_availability_mode == "Full"
    ):
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
