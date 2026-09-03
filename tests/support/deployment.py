"""Deterministic stateful doubles for deployment orchestration tests."""

import hashlib
from dataclasses import dataclass, field, replace
from pathlib import Path
from uuid import UUID

from ocp_dsx_air.core.contracts import (
    AirBootDevice,
    AirCpuMode,
    AirImageIntent,
    AirImagePurpose,
    AirImageSnapshot,
    AirImageUploadStatus,
    AirLinkSnapshot,
    AirNetworkPciSnapshot,
    AirNodeHardwareIntent,
    AirNodeHardwareSnapshot,
    AirNodeIntent,
    AirNodeSnapshot,
    AirSimulationIntent,
    AirSimulationSnapshot,
    AirSimulationStatus,
    AssistedClusterIntent,
    AssistedClusterNetwork,
    AssistedClusterSnapshot,
    AssistedHostSnapshot,
    AssistedInfraEnvIntent,
    AssistedInfraEnvSnapshot,
    ClusterStatus,
    CpuArchitecture,
    CredentialPaths,
    DeploymentEvent,
    HostStatus,
    InfraEnvImageType,
    InstallStage,
    JumpHostSnapshot,
    OpenShiftNodeRole,
)
from ocp_dsx_air.core.exceptions import AirImageError, AirSimError, AssistedError
from ocp_dsx_air.models.runtime import ClusterNetworkConfig

CLUSTER_ID = UUID("11111111-1111-4111-8111-111111111111")
HOST_ID = UUID("33333333-3333-4333-8333-333333333333")
AIR_IMAGE_ID = UUID("44444444-4444-4444-8444-111111111111")


@dataclass(frozen=True, slots=True)
class FakeCall:
    operation: str
    args: tuple[object, ...] = ()
    kwargs: dict[str, object] | None = None


def assisted_cluster_intent() -> AssistedClusterIntent:
    return AssistedClusterIntent(
        name="ocp",
        ocp_version="4.19",
        base_dns_domain="dsx.air.local",
        architecture=CpuArchitecture.X86_64,
        ntp_sources=("192.168.200.1",),
        high_availability=False,
        control_plane_count=1,
        user_managed_networking=False,
        machine_networks=("192.168.200.0/24",),
        cluster_networks=(AssistedClusterNetwork("10.128.0.0/14", 23),),
        service_networks=("172.30.0.0/16",),
        api_vips=("192.168.200.10",),
        ingress_vips=("192.168.200.11",),
    )


def assisted_cluster_snapshot(
    *,
    cluster_id: UUID = CLUSTER_ID,
) -> AssistedClusterSnapshot:
    intent = assisted_cluster_intent()
    return AssistedClusterSnapshot(
        id=cluster_id,
        name=intent.name,
        status=ClusterStatus.PENDING_FOR_INPUT,
        status_info="Waiting for hosts",
        ocp_version=intent.ocp_version,
        base_dns_domain=intent.base_dns_domain,
        architecture=intent.architecture,
        ntp_sources=intent.ntp_sources,
        high_availability_mode="None",
        control_plane_count=intent.control_plane_count,
        user_managed_networking=intent.user_managed_networking,
        machine_networks=intent.machine_networks,
        cluster_networks=intent.cluster_networks,
        service_networks=intent.service_networks,
        api_vips=intent.api_vips,
        ingress_vips=intent.ingress_vips,
        install_started=False,
        install_completed=False,
    )


def assisted_infraenv_intent(*, cluster_id: UUID) -> AssistedInfraEnvIntent:
    return AssistedInfraEnvIntent(
        name="ocp-discovery",
        cluster_id=cluster_id,
        ocp_version="4.19",
        architecture=CpuArchitecture.X86_64,
        image_type=InfraEnvImageType.MINIMAL_ISO,
        ntp_sources=("192.168.200.1",),
        ssh_authorized_key="ssh-ed25519 public-key",
    )


def assisted_host_snapshot(
    *,
    host_id: UUID = HOST_ID,
) -> AssistedHostSnapshot:
    return AssistedHostSnapshot(
        id=host_id,
        infraenv_id=UUID("22222222-2222-4222-8222-111111111111"),
        requested_hostname="ocp-cp-0",
        inventory_hostname="ocp-cp-0",
        status=HostStatus.KNOWN,
        status_info="Host is ready",
        role=OpenShiftNodeRole.MASTER,
        ipv4_addresses=(),
        install_stage=InstallStage.UNKNOWN,
        progress_info="",
    )


def air_image_intent() -> AirImageIntent:
    content = b"image bytes"
    return AirImageIntent(
        name="ocp-dsx-air-discovery-test",
        purpose=AirImagePurpose.DISCOVERY_ISO,
        version="1",
        architecture=CpuArchitecture.X86_64,
        provider="VM",
        source_size_bytes=len(content),
        source_sha256=hashlib.sha256(content).hexdigest(),
    )


def air_image_snapshot(*, image_id: UUID = AIR_IMAGE_ID) -> AirImageSnapshot:
    intent = air_image_intent()
    return AirImageSnapshot(
        id=image_id,
        name=intent.name,
        version=intent.version,
        architecture=intent.architecture,
        provider=intent.provider,
        upload_status=AirImageUploadStatus.COMPLETE,
        size_bytes=intent.source_size_bytes,
        sha256=intent.source_sha256,
        owned_by_client=True,
    )


def air_node_intent() -> AirNodeIntent:
    return AirNodeIntent(
        name="ocp-cp-0",
        cpu=16,
        memory_mib=65536,
        storage_gib=100,
        base_image_id=UUID("44444444-4444-4444-8444-444444444444"),
        base_image_name="ocp-dsx-air-blank-test",
        discovery_image_id=UUID("55555555-5555-4555-8555-555555555555"),
        discovery_image_name="ocp-dsx-air-discovery-test",
        hardware=AirNodeHardwareIntent(
            boot_order=(AirBootDevice.HARD_DISK, AirBootDevice.CDROM),
            cpu_mode=AirCpuMode.HOST_PASSTHROUGH,
            nic_model="virtio",
            uefi=False,
            secureboot=False,
        ),
    )


def air_simulation_intent() -> AirSimulationIntent:
    return AirSimulationIntent(
        name="dsx-lab",
        nodes=(air_node_intent(),),
        auto_oob_enabled=True,
        enable_dhcp=True,
        topology_sha256="a" * 64,
    )


class _StatefulFake:
    def __init__(self) -> None:
        self.calls: list[FakeCall] = []
        self.failures: dict[str, Exception] = {}

    def _begin(
        self,
        operation: str,
        *args: object,
        **kwargs: object,
    ) -> None:
        self.calls.append(FakeCall(operation, args, kwargs or None))
        failure = self.failures.get(operation)
        if failure is not None:
            raise failure


class FakeAssistedInstaller(_StatefulFake):
    """In-memory implementation of the current Assisted Installer port."""

    CLUSTER_IDS = (
        UUID("11111111-1111-4111-8111-111111111111"),
        UUID("11111111-1111-4111-8111-222222222222"),
    )
    INFRAENV_IDS = (
        UUID("22222222-2222-4222-8222-111111111111"),
        UUID("22222222-2222-4222-8222-222222222222"),
    )

    def __init__(self) -> None:
        super().__init__()
        self.clusters: dict[UUID, AssistedClusterSnapshot] = {}
        self.infraenvs: dict[UUID, AssistedInfraEnvSnapshot] = {}
        self.hosts: dict[UUID, tuple[AssistedHostSnapshot, ...]] = {}
        self._next_cluster = 0
        self._next_infraenv = 0
        self.auto_progress_infraenv = False
        self.auto_ready_cluster = False
        self.auto_complete_installation = False

    def find_cluster(self, name: str) -> AssistedClusterSnapshot | None:
        self._begin("find_cluster", name)
        matches = [cluster for cluster in self.clusters.values() if cluster.name == name]
        if len(matches) > 1:
            raise AssistedError(f"Fake Assisted has multiple clusters named {name!r}")
        if not matches:
            return None
        cluster = matches[0]
        cluster_hosts = self.hosts.get(cluster.id, ())
        if (
            self.auto_ready_cluster
            and cluster.status in {ClusterStatus.PENDING_FOR_INPUT, ClusterStatus.INSUFFICIENT}
            and cluster_hosts
            and all(
                host.status in {HostStatus.KNOWN, HostStatus.READY}
                and host.role not in {None, OpenShiftNodeRole.UNKNOWN}
                for host in cluster_hosts
            )
        ):
            cluster = replace(
                cluster,
                status=ClusterStatus.READY,
                status_info="Ready to install",
            )
            self.clusters[cluster.id] = cluster
        if self.auto_complete_installation and cluster.install_started:
            cluster = replace(
                cluster,
                status=ClusterStatus.INSTALLED,
                status_info="Installation completed",
                install_completed=True,
            )
            self.clusters[cluster.id] = cluster
            self.hosts[cluster.id] = tuple(
                replace(host, status=HostStatus.INSTALLED)
                for host in self.hosts.get(cluster.id, ())
            )
        return cluster

    def create_cluster(
        self,
        intent: AssistedClusterIntent,
        *,
        pull_secret: str,
        ssh_public_key: str,
    ) -> AssistedClusterSnapshot:
        self._begin(
            "create_cluster",
            intent,
            pull_secret=pull_secret,
            ssh_public_key=ssh_public_key,
        )
        if any(cluster.name == intent.name for cluster in self.clusters.values()):
            raise AssistedError(f"Fake Assisted cluster {intent.name!r} already exists")
        try:
            cluster_id = self.CLUSTER_IDS[self._next_cluster]
        except IndexError as exc:
            raise AssistedError("Fake Assisted cluster ID supply is exhausted") from exc
        self._next_cluster += 1
        snapshot = AssistedClusterSnapshot(
            id=cluster_id,
            name=intent.name,
            status=ClusterStatus.PENDING_FOR_INPUT,
            status_info="Waiting for hosts",
            ocp_version=intent.ocp_version,
            base_dns_domain=intent.base_dns_domain,
            architecture=intent.architecture,
            ntp_sources=intent.ntp_sources,
            high_availability_mode="Full" if intent.high_availability else "None",
            control_plane_count=intent.control_plane_count,
            user_managed_networking=intent.user_managed_networking,
            machine_networks=intent.machine_networks,
            cluster_networks=intent.cluster_networks,
            service_networks=intent.service_networks,
            api_vips=intent.api_vips,
            ingress_vips=intent.ingress_vips,
            install_started=False,
            install_completed=False,
        )
        self.clusters[cluster_id] = snapshot
        return snapshot

    def delete_cluster(self, cluster_id: UUID) -> None:
        self._begin("delete_cluster", cluster_id)
        self.clusters.pop(cluster_id, None)

    def find_infraenv(self, name: str) -> AssistedInfraEnvSnapshot | None:
        self._begin("find_infraenv", name)
        matches = [infraenv for infraenv in self.infraenvs.values() if infraenv.name == name]
        if len(matches) > 1:
            raise AssistedError(f"Fake Assisted has multiple InfraEnvs named {name!r}")
        if not matches:
            return None
        infraenv = matches[0]
        if self.auto_progress_infraenv and not infraenv.iso_available:
            infraenv = replace(infraenv, iso_available=True)
            self.infraenvs[infraenv.id] = infraenv
        return infraenv

    def create_infraenv(
        self,
        intent: AssistedInfraEnvIntent,
        *,
        pull_secret: str,
    ) -> AssistedInfraEnvSnapshot:
        self._begin("create_infraenv", intent, pull_secret=pull_secret)
        if any(infraenv.name == intent.name for infraenv in self.infraenvs.values()):
            raise AssistedError(f"Fake Assisted InfraEnv {intent.name!r} already exists")
        try:
            infraenv_id = self.INFRAENV_IDS[self._next_infraenv]
        except IndexError as exc:
            raise AssistedError("Fake Assisted InfraEnv ID supply is exhausted") from exc
        self._next_infraenv += 1
        snapshot = AssistedInfraEnvSnapshot(
            id=infraenv_id,
            name=intent.name,
            cluster_id=intent.cluster_id,
            ocp_version=intent.ocp_version,
            architecture=intent.architecture,
            image_type=intent.image_type,
            ntp_sources=intent.ntp_sources,
            ssh_authorized_key=intent.ssh_authorized_key,
            pull_secret_set=bool(pull_secret),
            iso_available=False,
        )
        self.infraenvs[infraenv_id] = snapshot
        return snapshot

    def delete_infraenv(self, infraenv_id: UUID) -> None:
        self._begin("delete_infraenv", infraenv_id)
        self.infraenvs.pop(infraenv_id, None)

    def download_discovery_iso(
        self,
        infraenv_id: UUID,
        destination: Path,
    ) -> Path:
        self._begin("download_discovery_iso", infraenv_id, destination)
        if infraenv_id not in self.infraenvs:
            raise AssistedError("Fake Assisted InfraEnv does not exist")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"fake assisted discovery iso\n")
        self.infraenvs[infraenv_id] = replace(
            self.infraenvs[infraenv_id],
            iso_available=True,
        )
        return destination

    def list_hosts(self, cluster_id: UUID) -> tuple[AssistedHostSnapshot, ...]:
        self._begin("list_hosts", cluster_id)
        return self.hosts.get(cluster_id, ())

    def update_host_role(
        self,
        infraenv_id: UUID,
        host_id: UUID,
        role: OpenShiftNodeRole,
    ) -> AssistedHostSnapshot:
        self._begin("update_host_role", infraenv_id, host_id, role)
        for cluster_id, hosts in self.hosts.items():
            for index, host in enumerate(hosts):
                if host.id == host_id and host.infraenv_id == infraenv_id:
                    updated = replace(host, role=role)
                    self.hosts[cluster_id] = (*hosts[:index], updated, *hosts[index + 1 :])
                    return updated
        raise AssistedError("Fake Assisted host does not exist")

    def start_installation(self, cluster_id: UUID) -> None:
        self._begin("start_installation", cluster_id)
        cluster = self.clusters.get(cluster_id)
        if cluster is None:
            raise AssistedError("Fake Assisted cluster does not exist")
        self.clusters[cluster_id] = replace(
            cluster,
            status=ClusterStatus.INSTALLING,
            status_info="Installation started",
            install_started=True,
        )

    def download_credentials(
        self,
        cluster_id: UUID,
        destination_dir: Path,
    ) -> CredentialPaths:
        self._begin("download_credentials", cluster_id, destination_dir)
        if cluster_id not in self.clusters:
            raise AssistedError("Fake Assisted cluster does not exist")
        destination_dir.mkdir(parents=True, exist_ok=True)
        kubeconfig = destination_dir / "kubeconfig"
        password = destination_dir / "kubeadmin-password"
        kubeconfig.write_bytes(b"fake kubeconfig\n")
        password.write_bytes(b"fake password\n")
        return CredentialPaths(kubeconfig, password)


class FakeAir(_StatefulFake):
    """In-memory implementation of the current NVIDIA Air port."""

    IMAGE_IDS = (
        UUID("44444444-4444-4444-8444-111111111111"),
        UUID("44444444-4444-4444-8444-222222222222"),
        UUID("44444444-4444-4444-8444-333333333333"),
    )
    SIMULATION_IDS = (
        UUID("66666666-6666-4666-8666-111111111111"),
        UUID("66666666-6666-4666-8666-222222222222"),
    )

    def __init__(self) -> None:
        super().__init__()
        self.images: dict[UUID, AirImageSnapshot] = {}
        self.simulations: dict[UUID, AirSimulationSnapshot] = {}
        self._next_image = 0
        self._next_simulation = 0

    def find_image(self, name: str) -> AirImageSnapshot | None:
        self._begin("find_image", name)
        matches = [image for image in self.images.values() if image.name == name]
        if len(matches) > 1:
            raise AirImageError(f"Fake Air has multiple images named {name!r}")
        return matches[0] if matches else None

    def create_image(self, intent: AirImageIntent) -> AirImageSnapshot:
        self._begin("create_image", intent)
        if any(image.name == intent.name for image in self.images.values()):
            raise AirImageError(f"Fake Air image {intent.name!r} already exists")
        try:
            image_id = self.IMAGE_IDS[self._next_image]
        except IndexError as exc:
            raise AirImageError("Fake Air image ID supply is exhausted") from exc
        self._next_image += 1
        snapshot = AirImageSnapshot(
            id=image_id,
            name=intent.name,
            version=intent.version,
            architecture=intent.architecture,
            provider=intent.provider,
            upload_status=AirImageUploadStatus.READY,
            size_bytes=0,
            sha256="",
            owned_by_client=True,
        )
        self.images[image_id] = snapshot
        return snapshot

    def upload_image(self, image_id: UUID, source: Path) -> AirImageSnapshot:
        self._begin("upload_image", image_id, source)
        image = self.images.get(image_id)
        if image is None:
            raise AirImageError("Fake Air image does not exist")
        content = source.read_bytes()
        snapshot = replace(
            image,
            upload_status=AirImageUploadStatus.COMPLETE,
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
        )
        self.images[image_id] = snapshot
        return snapshot

    def delete_image(self, image_id: UUID) -> None:
        self._begin("delete_image", image_id)
        self.images.pop(image_id, None)

    def find_simulation(self, name: str) -> AirSimulationSnapshot | None:
        self._begin("find_simulation", name)
        matches = [simulation for simulation in self.simulations.values() if simulation.name == name]
        if len(matches) > 1:
            raise AirSimError(f"Fake Air has multiple simulations named {name!r}")
        return matches[0] if matches else None

    def import_simulation(
        self,
        intent: AirSimulationIntent,
    ) -> AirSimulationSnapshot:
        self._begin("import_simulation", intent)
        if any(simulation.name == intent.name for simulation in self.simulations.values()):
            raise AirSimError(f"Fake Air simulation {intent.name!r} already exists")
        try:
            simulation_id = self.SIMULATION_IDS[self._next_simulation]
        except IndexError as exc:
            raise AirSimError("Fake Air simulation ID supply is exhausted") from exc
        self._next_simulation += 1
        nodes = tuple(
            AirNodeSnapshot(
                id=UUID(int=1000 + index),
                name=node.name,
                state="STOPPED",
                worker_status="",
                cpu=node.cpu,
                memory_mib=node.memory_mib,
                storage_gib=node.storage_gib,
                base_image_id=node.base_image_id,
                base_image_name=node.base_image_name,
                discovery_image_id=node.discovery_image_id,
                discovery_image_name=node.discovery_image_name,
                hardware=AirNodeHardwareSnapshot(
                    boot_order=node.hardware.boot_order,
                    cpu_mode=node.hardware.cpu_mode,
                    nic_model=node.hardware.nic_model,
                    uefi=node.hardware.uefi,
                    secureboot=node.hardware.secureboot,
                    emulation_type=node.hardware.emulation_type,
                    network_pci=tuple(
                        AirNetworkPciSnapshot(
                            name=device.name,
                            emulation_type=device.emulation_type,
                            model=device.model,
                        )
                        for device in node.hardware.network_pci
                    ),
                ),
                management_ipv4s=(),
            )
            for index, node in enumerate(intent.nodes)
        )
        snapshot = AirSimulationSnapshot(
            id=simulation_id,
            name=intent.name,
            status=AirSimulationStatus.INACTIVE,
            auto_oob_enabled=intent.auto_oob_enabled,
            enable_dhcp=intent.enable_dhcp,
            nodes=nodes,
            complete_checkpoint_count=0,
            managed_by_us=True,
            metadata_schema=intent.metadata_schema,
            topology_sha256=intent.topology_sha256,
            managed_node_names=tuple(sorted(node.name for node in intent.nodes)),
            links=tuple(AirLinkSnapshot(link.endpoints) for link in intent.links),
            topology_observed=True,
        )
        self.simulations[simulation_id] = snapshot
        return snapshot

    def start_simulation(self, simulation_id: UUID) -> None:
        self._begin("start_simulation", simulation_id)
        simulation = self.simulations.get(simulation_id)
        if simulation is None:
            raise AirSimError("Fake Air simulation does not exist")
        self.simulations[simulation_id] = replace(
            simulation,
            status=AirSimulationStatus.ACTIVE,
            nodes=tuple(replace(node, state="RUNNING") for node in simulation.nodes),
        )

    def shutdown_simulation(
        self,
        simulation_id: UUID,
        *,
        create_checkpoint: bool,
    ) -> None:
        self._begin(
            "shutdown_simulation",
            simulation_id,
            create_checkpoint=create_checkpoint,
        )
        simulation = self.simulations.get(simulation_id)
        if simulation is None:
            raise AirSimError("Fake Air simulation does not exist")
        self.simulations[simulation_id] = replace(
            simulation,
            status=AirSimulationStatus.INACTIVE,
            nodes=tuple(replace(node, state="STOPPED") for node in simulation.nodes),
            complete_checkpoint_count=(
                simulation.complete_checkpoint_count + int(create_checkpoint)
            ),
        )

    def delete_simulation(self, simulation_id: UUID) -> None:
        self._begin("delete_simulation", simulation_id)
        self.simulations.pop(simulation_id, None)

    def ensure_jump_host(
        self,
        simulation_id: UUID,
        network: ClusterNetworkConfig,
        *,
        new_password: str,
        timeout_seconds: float,
    ) -> JumpHostSnapshot:
        self._begin(
            "ensure_jump_host",
            simulation_id,
            network,
            new_password=new_password,
            timeout_seconds=timeout_seconds,
        )
        if simulation_id not in self.simulations:
            raise AirSimError("Fake Air simulation does not exist")
        return JumpHostSnapshot(
            service_id=UUID("77777777-7777-4777-8777-777777777777"),
            host="jump.example.test",
            port=22022,
            username="ubuntu",
        )


@dataclass(slots=True)
class FakeClock:
    current: float = 0.0
    sleeps: list[float] = field(default_factory=list)

    def monotonic(self) -> float:
        return self.current

    def sleep(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("sleep duration must be non-negative")
        self.current += seconds
        self.sleeps.append(seconds)


@dataclass(slots=True)
class RecordingReporter:
    events: list[DeploymentEvent] = field(default_factory=list)

    def emit(self, event: DeploymentEvent) -> None:
        self.events.append(event)
