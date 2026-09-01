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
    CLUSTER_INSTALLING_PENDING_USER_ACTION = (
        "cluster-installing-pending-user-action"
    )

    HOST_ERROR = "host-error"
    HOST_CANCELLED = "host-cancelled"
    HOST_INSTALLING_PENDING_USER_ACTION = (
        "host-installing-pending-user-action"
    )
    HOST_RESETTING_PENDING_USER_ACTION = (
        "host-resetting-pending-user-action"
    )
    HOST_UNBINDING_PENDING_USER_ACTION = (
        "host-unbinding-pending-user-action"
    )
    HOST_INSUFFICIENT = "host-insufficient"

    NO_OOB_AFTER_REBOOT = "no-oob-after-reboot"
    WRONG_DISCOVERY_BOOT = "wrong-discovery-boot"

class Severity(StrEnum):
    WARNING = "warning"
    ACTION_REQUIRED = "action-required"

class InfraEnvImageType(StrEnum):
    MINIMAL_ISO = "minimal-iso"
    UNKNOWN = "unknown"



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
class ClusterDecision:
    action: ClusterAction
    reason: str
    drift: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class InfraEnvDecision:
    action: InfraEnvAction
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
