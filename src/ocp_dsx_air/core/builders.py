"""Pure staged builders that add observed resource identities to deploy intent."""

from uuid import UUID

from ocp_dsx_air.adapters.air.artifacts import blank_disk_air_image_name
from ocp_dsx_air.adapters.air.mapping import simulation_topology_sha256
from ocp_dsx_air.core.contracts import (
    AirImageIntent,
    AirImagePurpose,
    AirImageSnapshot,
    AirNodeIntent,
    AirSimulationIntent,
    AssistedClusterSnapshot,
    AssistedInfraEnvIntent,
    BlankDiskIntent,
    DeployIntent,
    InfraEnvImageType,
    LocalImageArtifact,
)
from ocp_dsx_air.core.iso import air_discovery_image_name, discovery_infraenv_name


def build_infraenv_intent(
    deploy: DeployIntent,
    cluster: AssistedClusterSnapshot,
    *,
    ssh_authorized_key: str,
) -> AssistedInfraEnvIntent:
    return AssistedInfraEnvIntent(
        name=discovery_infraenv_name(cluster.name),
        cluster_id=cluster.id,
        ocp_version=deploy.cluster.ocp_version,
        architecture=deploy.cluster.architecture,
        image_type=InfraEnvImageType.MINIMAL_ISO,
        ntp_sources=deploy.cluster.ntp_sources,
        ssh_authorized_key=ssh_authorized_key,
    )


def build_discovery_image_intent(
    deploy: DeployIntent,
    infraenv_id: UUID,
    artifact: LocalImageArtifact,
) -> AirImageIntent:
    return AirImageIntent(
        name=air_discovery_image_name(infraenv_id),
        purpose=AirImagePurpose.DISCOVERY_ISO,
        version=deploy.cluster.ocp_version,
        architecture=deploy.cluster.architecture,
        provider="VM",
        source_size_bytes=artifact.size_bytes,
        source_sha256=artifact.sha256,
    )


def build_blank_image_intent(
    blank: BlankDiskIntent,
    artifact: LocalImageArtifact,
) -> AirImageIntent:
    return AirImageIntent(
        name=blank_disk_air_image_name(blank, artifact.sha256),
        purpose=AirImagePurpose.BLANK_DISK,
        version=f"v{blank.schema_version}",
        architecture=blank.architecture,
        provider="VM",
        source_size_bytes=artifact.size_bytes,
        source_sha256=artifact.sha256,
    )


def build_simulation_intent(
    deploy: DeployIntent,
    *,
    blank_image: AirImageSnapshot,
    discovery_image: AirImageSnapshot,
) -> AirSimulationIntent:
    nodes = tuple(
        AirNodeIntent(
            name=node.name,
            cpu=node.cpu,
            memory_mib=node.memory_mib,
            storage_gib=node.storage_gib,
            base_image_id=blank_image.id,
            base_image_name=blank_image.name,
            discovery_image_id=discovery_image.id,
            discovery_image_name=discovery_image.name,
            hardware=node.hardware,
        )
        for node in deploy.nodes
    )
    provisional = AirSimulationIntent(
        name=deploy.simulation_name,
        nodes=nodes,
        links=deploy.links,
        auto_oob_enabled=deploy.auto_oob_enabled,
        enable_dhcp=deploy.enable_dhcp,
        topology_sha256="pending",
    )
    return AirSimulationIntent(
        name=provisional.name,
        nodes=provisional.nodes,
        links=provisional.links,
        auto_oob_enabled=provisional.auto_oob_enabled,
        enable_dhcp=provisional.enable_dhcp,
        topology_sha256=simulation_topology_sha256(provisional),
        metadata_schema=provisional.metadata_schema,
    )
