from dataclasses import dataclass
from enum import StrEnum
from ipaddress import IPv4Address
from pathlib import Path
from uuid import UUID


class HostStatus(StrEnum):
    DISCOVERING = "discovering"
    KNOWN = "known"
    READY = "ready"
    INSUFFICIENT = "insufficient"
    INSTALLING = "installing"
    INSTALLING_IN_PROGRESS = "installing-in-progress"
    INSTALLING_PENDING_USER_ACTION = "installing-pending-user-action"
    RESETTING_PENDING_USER_ACTION = "resetting-pending-user-action"
    UNBINDING_PENDING_USER_ACTION = "unbinding-pending-user-action"
    INSTALLED = "installed"
    ERROR = "error"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class ClusterStatus(StrEnum):
    INSUFFICIENT = "insufficient"
    READY = "ready"
    ERROR = "error"
    PREPARING_FOR_INSTALLATION = "preparing-for-installation"
    PENDING_FOR_INPUT = "pending-for-input"
    INSTALLING = "installing"
    FINALIZING = "finalizing"
    INSTALLED = "installed"
    ADDING_HOSTS = "adding-hosts"
    CANCELLED = "cancelled"
    INSTALLING_PENDING_USER_ACTION = "installing-pending-user-action"
    UNMONITORED = "unmonitored"
    UNKNOWN = "unknown"


class CpuArchitecture(StrEnum):
    X86_64 = "x86_64"
    ARM64 = "arm64"
    UNKNOWN = "unknown"


class ClusterAction(StrEnum):
    CREATE = "create"
    WAIT_FOR_HOSTS = "wait-for-hosts"
    START_INSTALL = "start-install"
    WAIT_FOR_INSTALL = "wait-for-install"
    DOWNLOAD_CREDENTIALS = "download-credentials"
    REPLACE = "replace"
    REFUSE_DRIFT = "refuse-drift"
    REFUSE_TERMINAL = "refuse-terminal"
    REFUSE_UNKNOWN = "refuse-unknown"
    REFUSE_UNSUPPORTED = "refuse-unsupported"


class InfraEnvAction(StrEnum):
    WAIT_FOR_ISO = "wait-for-iso"
    READY = "ready"
    CREATE = "create"
    DOWNLOAD_ISO = "download-iso"
    REPLACE = "replace"
    REFUSE_DRIFT = "refuse-drift"
    REFUSE_UNKNOWN = "refuse-unknown"


class InstallStage(StrEnum):
    WRITING_IMAGE = "Writing image to disk"
    REBOOTING = "Rebooting"
    WAITING_FOR_IGNITION = "Waiting for ignition"
    CONFIGURING = "Configuring"
    UNKNOWN = "unknown"


class IssueCode(StrEnum):
    CLUSTER_ERROR = "cluster-error"
    CLUSTER_CANCELLED = "cluster-cancelled"
    CLUSTER_INSTALLING_PENDING_USER_ACTION = "cluster-installing-pending-user-action"

    HOST_ERROR = "host-error"
    HOST_CANCELLED = "host-cancelled"
    HOST_INSTALLING_PENDING_USER_ACTION = "host-installing-pending-user-action"
    HOST_RESETTING_PENDING_USER_ACTION = "host-resetting-pending-user-action"
    HOST_UNBINDING_PENDING_USER_ACTION = "host-unbinding-pending-user-action"
    HOST_INSUFFICIENT = "host-insufficient"

    NO_OOB_AFTER_REBOOT = "no-oob-after-reboot"
    WRONG_DISCOVERY_BOOT = "wrong-discovery-boot"


class Severity(StrEnum):
    WARNING = "warning"
    ACTION_REQUIRED = "action-required"


class InfraEnvImageType(StrEnum):
    MINIMAL_ISO = "minimal-iso"
    UNKNOWN = "unknown"


class AirImagePurpose(StrEnum):
    DISCOVERY_ISO = "discovery-iso"
    BLANK_DISK = "blank-disk"


class AirImageUploadStatus(StrEnum):
    READY = "READY"
    UPLOADING = "UPLOADING"
    VALIDATING = "VALIDATING"
    COMPLETE = "COMPLETE"
    PUBLISHING = "PUBLISHING"
    UNPUBLISHING = "UNPUBLISHING"
    COPYING_FROM_IMAGE_SHARE = "COPYING_FROM_IMAGE_SHARE"
    PENDING_PUBLISH = "PENDING_PUBLISH"
    PENDING_UNPUBLISH = "PENDING_UNPUBLISH"
    UNKNOWN = "UNKNOWN"


class AirSimulationStatus(StrEnum):
    CLONING = "CLONING"
    CREATING = "CREATING"
    IMPORTING = "IMPORTING"
    INVALID = "INVALID"
    INACTIVE = "INACTIVE"
    REQUESTING = "REQUESTING"
    PROVISIONING = "PROVISIONING"
    PREPARE_BOOT = "PREPARE_BOOT"
    BOOTING = "BOOTING"
    ACTIVE = "ACTIVE"
    PREPARE_SHUTDOWN = "PREPARE_SHUTDOWN"
    SHUTTING_DOWN = "SHUTTING_DOWN"
    SAVING = "SAVING"
    PREPARE_TEARDOWN = "PREPARE_TEARDOWN"
    TEARING_DOWN = "TEARING_DOWN"
    PREPARE_REBUILD = "PREPARE_REBUILD"
    REBUILDING = "REBUILDING"
    DELETING = "DELETING"
    PREPARE_PURGE = "PREPARE_PURGE"
    PURGING = "PURGING"
    DEMO = "DEMO"
    TRAINING = "TRAINING"
    UNKNOWN = "UNKNOWN"


class AirBootDevice(StrEnum):
    HARD_DISK = "hd"
    CDROM = "cdrom"
    NETWORK = "network"
    UNKNOWN = "unknown"


class AirCpuMode(StrEnum):
    HOST_PASSTHROUGH = "host-passthrough"
    HOST_MODEL = "host-model"
    CUSTOM = "custom"
    UNKNOWN = "unknown"


class AirImageFormat(StrEnum):
    QCOW2 = "qcow2"


class AirImageAction(StrEnum):
    CREATE = "create"
    UPLOAD = "upload"
    WAIT_FOR_UPLOAD = "wait-for-upload"
    READY = "ready"
    REPLACE = "replace"
    REFUSE_DRIFT = "refuse-drift"
    REFUSE_UNSUPPORTED = "refuse-unsupported"
    REFUSE_UNKNOWN = "refuse-unknown"


class AirSimulationAction(StrEnum):
    IMPORT = "import"
    WAIT_FOR_CREATION = "wait-for-creation"
    START = "start"
    WAIT_FOR_ACTIVE = "wait-for-active"
    READY = "ready"
    SHUTDOWN_FOR_REPLACEMENT = "shutdown-for-replacement"
    WAIT_FOR_INACTIVE = "wait-for-inactive"
    DELETE_FOR_REPLACEMENT = "delete-for-replacement"
    WAIT_FOR_DELETION = "wait-for-deletion"
    REFUSE_DRIFT = "refuse-drift"
    REFUSE_TERMINAL = "refuse-terminal"
    REFUSE_UNSUPPORTED = "refuse-unsupported"
    REFUSE_UNKNOWN = "refuse-unknown"


class DeploymentPhase(StrEnum):
    CLUSTER = "cluster"
    INFRAENV = "infraenv"
    AIR_IMAGES = "air-images"
    SIMULATION = "simulation"
    HOST_DISCOVERY = "host-discovery"
    JUMP_HOST = "jump-host"
    INSTALLATION = "installation"
    CREDENTIALS = "credentials"


@dataclass(frozen=True, slots=True)
class AssistedClusterSnapshot:
    id: UUID
    name: str
    status: ClusterStatus
    status_info: str
    ocp_version: str
    base_dns_domain: str
    architecture: CpuArchitecture
    ntp_sources: tuple[str, ...]
    high_availability_mode: str
    control_plane_count: int
    user_managed_networking: bool
    machine_networks: tuple[str, ...]
    api_vips: tuple[str, ...]
    ingress_vips: tuple[str, ...]
    install_started: bool
    install_completed: bool


@dataclass(frozen=True, slots=True)
class AssistedClusterIntent:
    name: str
    ocp_version: str
    base_dns_domain: str
    architecture: CpuArchitecture
    ntp_sources: tuple[str, ...]
    high_availability: bool
    control_plane_count: int
    user_managed_networking: bool
    machine_networks: tuple[str, ...]
    api_vips: tuple[str, ...]
    ingress_vips: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AssistedHostSnapshot:
    id: UUID
    requested_hostname: str | None
    inventory_hostname: str | None
    status: HostStatus
    status_info: str
    role: str | None
    ipv4_addresses: tuple[IPv4Address, ...]
    install_stage: InstallStage
    progress_info: str


@dataclass(frozen=True, slots=True)
class AssistedInfraEnvIntent:
    name: str
    cluster_id: UUID
    ocp_version: str
    architecture: CpuArchitecture
    image_type: InfraEnvImageType
    ntp_sources: tuple[str, ...]
    ssh_authorized_key: str


@dataclass(frozen=True, slots=True)
class AssistedInfraEnvSnapshot:
    id: UUID
    name: str
    cluster_id: UUID
    ocp_version: str
    architecture: CpuArchitecture
    image_type: InfraEnvImageType
    ntp_sources: tuple[str, ...]
    ssh_authorized_key: str
    pull_secret_set: bool
    iso_available: bool


@dataclass(frozen=True, slots=True)
class AirImageIntent:
    name: str
    purpose: AirImagePurpose
    version: str
    architecture: CpuArchitecture
    provider: str
    source_size_bytes: int
    source_sha256: str


@dataclass(frozen=True, slots=True)
class AirImageSnapshot:
    id: UUID
    name: str
    version: str
    architecture: CpuArchitecture
    provider: str
    upload_status: AirImageUploadStatus
    size_bytes: int
    sha256: str
    owned_by_client: bool


@dataclass(frozen=True, slots=True)
class AirNodeIntent:
    name: str
    cpu: int
    memory_mib: int
    storage_gib: int
    base_image_id: UUID
    base_image_name: str
    discovery_image_id: UUID
    discovery_image_name: str
    boot_order: tuple[AirBootDevice, ...]
    cpu_mode: AirCpuMode
    nic_model: str
    uefi: bool
    secureboot: bool


@dataclass(frozen=True, slots=True)
class AirNodeSnapshot:
    id: UUID
    name: str
    state: str
    worker_status: str
    cpu: int
    memory_mib: int
    storage_gib: int
    base_image_id: UUID
    base_image_name: str
    discovery_image_id: UUID | None
    discovery_image_name: str | None
    boot_order: tuple[AirBootDevice, ...]
    cpu_mode: AirCpuMode
    nic_model: str
    uefi: bool
    secureboot: bool
    management_ipv4s: tuple[IPv4Address, ...]


@dataclass(frozen=True, slots=True)
class AirSimulationIntent:
    name: str
    nodes: tuple[AirNodeIntent, ...]
    auto_oob_enabled: bool
    enable_dhcp: bool
    topology_sha256: str
    metadata_schema: int = 1


@dataclass(frozen=True, slots=True)
class AirSimulationSnapshot:
    id: UUID
    name: str
    status: AirSimulationStatus
    auto_oob_enabled: bool | None
    enable_dhcp: bool | None
    nodes: tuple[AirNodeSnapshot, ...]
    complete_checkpoint_count: int
    managed_by_us: bool
    metadata_schema: int | None
    topology_sha256: str | None
    managed_node_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BlankDiskIntent:
    architecture: CpuArchitecture
    virtual_size_gib: int
    image_format: AirImageFormat = AirImageFormat.QCOW2
    schema_version: int = 1


@dataclass(frozen=True, slots=True)
class LocalImageArtifact:
    path: Path
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class DeploymentEvent:
    phase: DeploymentPhase
    message: str
    action: str | None = None
    resource_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class ClusterDecision:
    action: ClusterAction
    reason: str
    drift: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class InfraEnvDecision:
    action: InfraEnvAction
    reason: str
    drift: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AirImageDecision:
    action: AirImageAction
    reason: str
    drift: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AirSimulationDecision:
    action: AirSimulationAction
    reason: str
    drift: tuple[str, ...] = ()


@dataclass
class PollIssue:
    severity: Severity
    code: IssueCode
    detail: str


@dataclass(frozen=True, slots=True)
class CredentialPaths:
    kubeconfig: Path
    kubeadmin_password: Path
