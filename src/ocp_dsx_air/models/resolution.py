"""Resolve validated user specifications into static deployment intent."""

from pathlib import Path

from ocp_dsx_air.core.contracts import (
    AirLinkEndpoint,
    AirLinkIntent,
    AirNetworkPciIntent,
    AirNodeHardwareIntent,
    AssistedClusterIntent,
    AssistedClusterNetwork,
    BlankDiskIntent,
    DeployIntent,
    DeploymentTimeouts,
    DeployNodeIntent,
    OpenShiftNodeRole,
)
from ocp_dsx_air.models.spec import LabSpec, NodeHardwareSpec, NodePool


def _hardware_intent(spec: NodeHardwareSpec) -> AirNodeHardwareIntent:
    return AirNodeHardwareIntent(
        boot_order=tuple(spec.boot_order),
        cpu_mode=spec.cpu_mode,
        nic_model=spec.nic_model,
        uefi=spec.uefi,
        secureboot=spec.secureboot,
        emulation_type=spec.emulation_type,
        network_pci=tuple(
            AirNetworkPciIntent(
                name=device.name,
                emulation_type=device.emulation_type,
                model=device.model,
            )
            for device in sorted(spec.network_pci, key=lambda item: item.name)
        ),
    )


def _pool_nodes(
    cluster_name: str,
    pool: NodePool,
    *,
    label: str,
    role: OpenShiftNodeRole,
) -> tuple[DeployNodeIntent, ...]:
    names = (
        tuple(pool.names)
        if pool.names is not None
        else tuple(f"{cluster_name}-{label}-{index}" for index in range(pool.count))
    )
    hardware = _hardware_intent(pool.hardware)
    return tuple(
        DeployNodeIntent(
            name=name,
            role=role,
            cpu=pool.cpu,
            memory_mib=pool.memory_mb,
            storage_gib=pool.disk_gb,
            hardware=hardware,
        )
        for name in names
    )


def resolve_deploy_intent(
    spec: LabSpec,
    *,
    cache_root: Path,
    discovery_timeout_seconds: float | None = None,
) -> DeployIntent:
    """Build complete static intent without inventing remote resource IDs."""
    control_plane = _pool_nodes(
        spec.cluster.name,
        spec.cluster.control_plane,
        label="control-plane",
        role=OpenShiftNodeRole.MASTER,
    )
    workers = _pool_nodes(
        spec.cluster.name,
        spec.cluster.workers,
        label="worker",
        role=OpenShiftNodeRole.WORKER,
    )
    nodes = (*control_plane, *workers)

    links = tuple(
        AirLinkIntent(
            endpoints=(
                AirLinkEndpoint(
                    node_name=link.endpoints[0].node,
                    interface=link.endpoints[0].interface,
                    network_pci_name=link.endpoints[0].network_pci,
                ),
                AirLinkEndpoint(
                    node_name=link.endpoints[1].node,
                    interface=link.endpoints[1].interface,
                    network_pci_name=link.endpoints[1].network_pci,
                ),
            )
        )
        for link in spec.simulation.links
    )
    high_availability = spec.cluster.control_plane.count == 3
    default_discovery_timeout = max(20 * 60, 8 * 60 * len(nodes))

    return DeployIntent(
        simulation_name=spec.simulation.name,
        cluster=AssistedClusterIntent(
            name=spec.cluster.name,
            ocp_version=spec.cluster.version,
            base_dns_domain=spec.cluster.base_dns_domain,
            architecture=spec.cluster.architecture,
            ntp_sources=tuple(spec.cluster.ntp_sources),
            high_availability=high_availability,
            control_plane_count=spec.cluster.control_plane.count,
            user_managed_networking=False,
            machine_networks=tuple(spec.cluster.machine_networks),
            cluster_networks=tuple(
                AssistedClusterNetwork(network.cidr, network.host_prefix)
                for network in spec.cluster.cluster_networks
            ),
            service_networks=tuple(spec.cluster.service_networks),
            api_vips=tuple(spec.cluster.api_vips) if high_availability else (),
            ingress_vips=(
                tuple(spec.cluster.ingress_vips) if high_availability else ()
            ),
        ),
        nodes=nodes,
        links=links,
        blank_disk=BlankDiskIntent(
            architecture=spec.cluster.architecture,
            virtual_size_gib=min(node.storage_gib for node in nodes),
        ),
        cache_root=cache_root,
        timeouts=DeploymentTimeouts(
            discovery_seconds=(
                discovery_timeout_seconds
                if discovery_timeout_seconds is not None
                else default_discovery_timeout
            )
        ),
    )
