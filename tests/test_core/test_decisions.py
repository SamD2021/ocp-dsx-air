from dataclasses import replace
from ipaddress import IPv4Address
from uuid import UUID

import pytest

from ocp_dsx_air.core.contracts import (
    AirBootDevice,
    AirCpuMode,
    AirImageAction,
    AirImageDecision,
    AirImageIntent,
    AirImagePurpose,
    AirImageSnapshot,
    AirImageUploadStatus,
    AirLinkEndpoint,
    AirLinkIntent,
    AirLinkSnapshot,
    AirNetworkPciEmulationType,
    AirNetworkPciIntent,
    AirNetworkPciSnapshot,
    AirNodeEmulationType,
    AirNodeHardwareIntent,
    AirNodeHardwareSnapshot,
    AirNodeIntent,
    AirNodeSnapshot,
    AirSimulationAction,
    AirSimulationDecision,
    AirSimulationIntent,
    AirSimulationSnapshot,
    AirSimulationStatus,
    AssistedClusterIntent,
    AssistedClusterNetwork,
    AssistedClusterSnapshot,
    AssistedInfraEnvIntent,
    AssistedInfraEnvSnapshot,
    ClusterAction,
    ClusterStatus,
    CpuArchitecture,
    InfraEnvAction,
    InfraEnvDecision,
    InfraEnvImageType,
)
from ocp_dsx_air.core.decisions import (
    decide_air_image_action,
    decide_air_simulation_action,
    decide_cluster_action,
    decide_infraenv_action,
)

CLUSTER_ID = UUID("5ad7357e-6c65-46e2-bad8-cd796cc82070")
INFRAENV_ID = UUID("7a0ddc45-ce1a-4d8d-ab9f-0be5fbe98d27")
AIR_IMAGE_ID = UUID("62f3d8c7-7257-4ce4-a94a-cdcf052ccf3f")
AIR_SIMULATION_ID = UUID("1d798d44-9b22-4ec6-b9a1-d1f194294f95")
AIR_NODE_ID = UUID("ee9fa020-7b15-4d9d-a131-785daf83e978")
BLANK_IMAGE_ID = UUID("c966e5dc-d40e-41b9-b29e-7bf99d187793")


def _intent() -> AssistedClusterIntent:
    return AssistedClusterIntent(
        name="ocp",
        ocp_version="4.19",
        base_dns_domain="dsx.air.local",
        architecture=CpuArchitecture.X86_64,
        ntp_sources=("192.168.200.1",),
        high_availability=True,
        control_plane_count=3,
        user_managed_networking=False,
        machine_networks=("192.168.200.0/24",),
        cluster_networks=(AssistedClusterNetwork("10.128.0.0/14", 23),),
        service_networks=("172.30.0.0/16",),
        api_vips=("192.168.200.10",),
        ingress_vips=("192.168.200.11",),
    )


def _observed(
    *,
    status: ClusterStatus = ClusterStatus.PENDING_FOR_INPUT,
    install_started: bool = False,
    install_completed: bool = False,
) -> AssistedClusterSnapshot:
    return AssistedClusterSnapshot(
        id=CLUSTER_ID,
        name="ocp",
        status=status,
        status_info="",
        ocp_version="4.19",
        base_dns_domain="dsx.air.local",
        architecture=CpuArchitecture.X86_64,
        ntp_sources=("192.168.200.1",),
        high_availability_mode="Full",
        control_plane_count=3,
        user_managed_networking=False,
        machine_networks=("192.168.200.0/24",),
        cluster_networks=(AssistedClusterNetwork("10.128.0.0/14", 23),),
        service_networks=("172.30.0.0/16",),
        api_vips=("192.168.200.10",),
        ingress_vips=("192.168.200.11",),
        install_started=install_started,
        install_completed=install_completed,
    )


def _assert_action(decision, expected: ClusterAction) -> None:
    assert decision.action is expected
    assert decision.reason.strip()


@pytest.mark.parametrize("replace", [False, True])
def test_absent_cluster_is_created(replace: bool) -> None:
    decision = decide_cluster_action(_intent(), None, replace=replace)

    _assert_action(decision, ClusterAction.CREATE)
    assert decision.drift == ()


@pytest.mark.parametrize("status", list(ClusterStatus))
def test_replace_authorization_replaces_any_existing_cluster(status: ClusterStatus) -> None:
    decision = decide_cluster_action(_intent(), _observed(status=status), replace=True)

    _assert_action(decision, ClusterAction.REPLACE)


@pytest.mark.parametrize(
    ("status", "install_started", "install_completed", "expected"),
    [
        (ClusterStatus.INSUFFICIENT, False, False, ClusterAction.WAIT_FOR_HOSTS),
        (ClusterStatus.PENDING_FOR_INPUT, False, False, ClusterAction.WAIT_FOR_HOSTS),
        (ClusterStatus.READY, False, False, ClusterAction.START_INSTALL),
        (
            ClusterStatus.PREPARING_FOR_INSTALLATION,
            True,
            False,
            ClusterAction.WAIT_FOR_INSTALL,
        ),
        (ClusterStatus.INSTALLING, True, False, ClusterAction.WAIT_FOR_INSTALL),
        (ClusterStatus.FINALIZING, True, False, ClusterAction.WAIT_FOR_INSTALL),
        (ClusterStatus.INSTALLED, True, True, ClusterAction.DOWNLOAD_CREDENTIALS),
        # Status alone is sufficient because Assisted can report installed before
        # its completion timestamp has propagated to a subsequent observation.
        (ClusterStatus.INSTALLED, True, False, ClusterAction.DOWNLOAD_CREDENTIALS),
    ],
)
def test_compatible_cluster_lifecycle_selects_next_action(
    status: ClusterStatus,
    install_started: bool,
    install_completed: bool,
    expected: ClusterAction,
) -> None:
    decision = decide_cluster_action(
        _intent(),
        _observed(
            status=status,
            install_started=install_started,
            install_completed=install_completed,
        ),
        replace=False,
    )

    _assert_action(decision, expected)
    assert decision.drift == ()


@pytest.mark.parametrize(
    "status",
    [ClusterStatus.ADDING_HOSTS, ClusterStatus.UNMONITORED],
)
def test_known_but_unsupported_cluster_status_is_refused(
    status: ClusterStatus,
) -> None:
    decision = decide_cluster_action(_intent(), _observed(status=status), replace=False)

    _assert_action(decision, ClusterAction.REFUSE_UNSUPPORTED)
    assert decision.drift == ()


def test_completion_marker_wins_over_stale_terminal_status() -> None:
    observed = _observed(
        status=ClusterStatus.ERROR,
        install_started=True,
        install_completed=True,
    )

    decision = decide_cluster_action(_intent(), observed, replace=False)

    _assert_action(decision, ClusterAction.DOWNLOAD_CREDENTIALS)


def test_install_started_marker_prevents_duplicate_start() -> None:
    observed = _observed(
        status=ClusterStatus.READY,
        install_started=True,
        install_completed=False,
    )

    decision = decide_cluster_action(_intent(), observed, replace=False)

    _assert_action(decision, ClusterAction.WAIT_FOR_INSTALL)


@pytest.mark.parametrize(
    "status",
    [
        ClusterStatus.ERROR,
        ClusterStatus.CANCELLED,
        ClusterStatus.INSTALLING_PENDING_USER_ACTION,
    ],
)
def test_terminal_cluster_is_refused(status: ClusterStatus) -> None:
    decision = decide_cluster_action(_intent(), _observed(status=status), replace=False)

    _assert_action(decision, ClusterAction.REFUSE_TERMINAL)
    assert decision.drift == ()


def test_unknown_cluster_status_is_refused() -> None:
    decision = decide_cluster_action(
        _intent(),
        _observed(status=ClusterStatus.UNKNOWN),
        replace=False,
    )

    _assert_action(decision, ClusterAction.REFUSE_UNKNOWN)
    assert decision.drift == ()


@pytest.mark.parametrize(
    ("change", "expected_field"),
    [
        ({"ocp_version": "4.20"}, "ocp_version"),
        ({"base_dns_domain": "example.com"}, "base_dns_domain"),
        ({"architecture": CpuArchitecture.ARM64}, "architecture"),
        ({"ntp_sources": ("192.168.200.2",)}, "ntp_sources"),
        ({"high_availability_mode": "None"}, "high_availability"),
        ({"control_plane_count": 1}, "control_plane_count"),
        ({"user_managed_networking": True}, "user_managed_networking"),
        ({"machine_networks": ["10.0.0.0/24"]}, "machine_networks"),
        (
            {"cluster_networks": [AssistedClusterNetwork("10.132.0.0/14", 23)]},
            "cluster_networks",
        ),
        ({"service_networks": ["172.31.0.0/16"]}, "service_networks"),
        ({"api_vips": ["192.168.200.20"]}, "api_vips"),
        ({"ingress_vips": ["192.168.200.21"]}, "ingress_vips"),
    ],
)
def test_material_drift_is_refused(change: dict[str, object], expected_field: str) -> None:
    observed = replace(_observed(status=ClusterStatus.INSTALLED), **change)

    decision = decide_cluster_action(_intent(), observed, replace=False)

    _assert_action(decision, ClusterAction.REFUSE_DRIFT)
    assert expected_field in decision.drift


def test_decision_reports_all_material_drift() -> None:
    observed = replace(
        _observed(status=ClusterStatus.READY),
        ocp_version="4.20",
        control_plane_count=1,
        machine_networks=["10.0.0.0/24"],
    )

    decision = decide_cluster_action(_intent(), observed, replace=False)

    _assert_action(decision, ClusterAction.REFUSE_DRIFT)
    assert set(decision.drift) == {
        "ocp_version",
        "control_plane_count",
        "machine_networks",
    }


def _infraenv_intent() -> AssistedInfraEnvIntent:
    return AssistedInfraEnvIntent(
        name="ocp-discovery",
        cluster_id=CLUSTER_ID,
        ocp_version="4.19",
        architecture=CpuArchitecture.X86_64,
        image_type=InfraEnvImageType.MINIMAL_ISO,
        ntp_sources=("192.168.200.1",),
        ssh_authorized_key="ssh-ed25519 public-key",
    )


def _infraenv_observed(
    *,
    iso_available: bool = True,
) -> AssistedInfraEnvSnapshot:
    return AssistedInfraEnvSnapshot(
        id=INFRAENV_ID,
        name="ocp-discovery",
        cluster_id=CLUSTER_ID,
        ocp_version="4.19",
        architecture=CpuArchitecture.X86_64,
        image_type=InfraEnvImageType.MINIMAL_ISO,
        ntp_sources=("192.168.200.1",),
        ssh_authorized_key="ssh-ed25519 public-key",
        pull_secret_set=True,
        iso_available=iso_available,
    )


def _assert_infraenv_action(
    decision: InfraEnvDecision,
    expected: InfraEnvAction,
) -> None:
    assert decision.action is expected
    assert decision.reason.strip()


@pytest.mark.parametrize("replace", [False, True])
def test_absent_infraenv_is_created(replace: bool) -> None:
    decision = decide_infraenv_action(
        _infraenv_intent(),
        None,
        replace=replace,
        iso_cached=False,
    )

    _assert_infraenv_action(decision, InfraEnvAction.CREATE)
    assert decision.drift == ()


def test_replace_authorization_precedes_unknown_drift_and_iso_state() -> None:
    observed = replace(
        _infraenv_observed(iso_available=False),
        architecture=CpuArchitecture.UNKNOWN,
        ocp_version="4.20",
    )

    decision = decide_infraenv_action(
        _infraenv_intent(),
        observed,
        replace=True,
        iso_cached=False,
    )

    _assert_infraenv_action(decision, InfraEnvAction.REPLACE)
    assert decision.drift == ()


@pytest.mark.parametrize(
    "change",
    [
        {"architecture": CpuArchitecture.UNKNOWN},
        {"image_type": InfraEnvImageType.UNKNOWN},
    ],
)
def test_unknown_infraenv_configuration_is_refused_before_drift(
    change: dict[str, object],
) -> None:
    observed = replace(_infraenv_observed(), ocp_version="4.20", **change)

    decision = decide_infraenv_action(
        _infraenv_intent(),
        observed,
        replace=False,
        iso_cached=False,
    )

    _assert_infraenv_action(decision, InfraEnvAction.REFUSE_UNKNOWN)
    assert decision.drift == ()


@pytest.mark.parametrize(
    ("change", "expected_field"),
    [
        (
            {"cluster_id": UUID("f926699c-77d9-472e-aa40-ccf70e98681b")},
            "cluster_id",
        ),
        ({"ocp_version": "4.20"}, "ocp_version"),
        ({"architecture": CpuArchitecture.ARM64}, "architecture"),
        ({"ntp_sources": ("time.google.com",)}, "ntp_sources"),
        (
            {"ssh_authorized_key": "ssh-ed25519 other-key"},
            "ssh_authorized_key",
        ),
        ({"pull_secret_set": False}, "pull_secret"),
    ],
)
def test_infraenv_material_drift_is_refused(
    change: dict[str, object],
    expected_field: str,
) -> None:
    observed = replace(_infraenv_observed(), **change)

    decision = decide_infraenv_action(
        _infraenv_intent(),
        observed,
        replace=False,
        iso_cached=True,
    )

    _assert_infraenv_action(decision, InfraEnvAction.REFUSE_DRIFT)
    assert decision.drift == (expected_field,)


def test_infraenv_decision_reports_all_material_drift() -> None:
    observed = replace(
        _infraenv_observed(),
        ocp_version="4.20",
        ntp_sources=("time.google.com",),
        pull_secret_set=False,
    )

    decision = decide_infraenv_action(
        _infraenv_intent(),
        observed,
        replace=False,
        iso_cached=True,
    )

    _assert_infraenv_action(decision, InfraEnvAction.REFUSE_DRIFT)
    assert decision.drift == ("ocp_version", "ntp_sources", "pull_secret")


@pytest.mark.parametrize(
    ("iso_available", "iso_cached", "expected"),
    [
        (False, False, InfraEnvAction.WAIT_FOR_ISO),
        (False, True, InfraEnvAction.WAIT_FOR_ISO),
        (True, False, InfraEnvAction.DOWNLOAD_ISO),
        (True, True, InfraEnvAction.READY),
    ],
)
def test_compatible_infraenv_selects_next_iso_action(
    iso_available: bool,
    iso_cached: bool,
    expected: InfraEnvAction,
) -> None:
    decision = decide_infraenv_action(
        _infraenv_intent(),
        _infraenv_observed(iso_available=iso_available),
        replace=False,
        iso_cached=iso_cached,
    )

    _assert_infraenv_action(decision, expected)
    assert decision.drift == ()


def test_infraenv_drift_precedes_iso_and_cache_state() -> None:
    observed = replace(
        _infraenv_observed(iso_available=False),
        ocp_version="4.20",
    )

    decision = decide_infraenv_action(
        _infraenv_intent(),
        observed,
        replace=False,
        iso_cached=False,
    )

    _assert_infraenv_action(decision, InfraEnvAction.REFUSE_DRIFT)
    assert decision.drift == ("ocp_version",)


def _air_image_intent() -> AirImageIntent:
    return AirImageIntent(
        name="ocp-dsx-air-discovery-7a0ddc45",
        purpose=AirImagePurpose.DISCOVERY_ISO,
        version="1",
        architecture=CpuArchitecture.X86_64,
        provider="VM",
        source_size_bytes=4096,
        source_sha256="c5f67d4563c93f8080b9f11f80fa3a152c958232ec2e747d23a75b669afc3ce9",
    )


def _air_image_observed(
    status: AirImageUploadStatus = AirImageUploadStatus.COMPLETE,
) -> AirImageSnapshot:
    intent = _air_image_intent()
    return AirImageSnapshot(
        id=AIR_IMAGE_ID,
        name=intent.name,
        version=intent.version,
        architecture=intent.architecture,
        provider=intent.provider,
        upload_status=status,
        size_bytes=intent.source_size_bytes,
        sha256=intent.source_sha256,
        owned_by_client=True,
    )


def _assert_air_image_action(
    decision: AirImageDecision,
    expected: AirImageAction,
) -> None:
    assert decision.action is expected
    assert decision.reason.strip()


@pytest.mark.parametrize("replace", [False, True])
def test_absent_air_image_creates_a_record(replace: bool) -> None:
    decision = decide_air_image_action(
        _air_image_intent(),
        None,
        replace=replace,
    )

    _assert_air_image_action(decision, AirImageAction.CREATE)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (AirImageUploadStatus.READY, AirImageAction.UPLOAD),
        (AirImageUploadStatus.UPLOADING, AirImageAction.WAIT_FOR_UPLOAD),
        (AirImageUploadStatus.VALIDATING, AirImageAction.WAIT_FOR_UPLOAD),
        (AirImageUploadStatus.COMPLETE, AirImageAction.READY),
    ],
)
def test_compatible_air_image_lifecycle_selects_next_action(
    status: AirImageUploadStatus,
    expected: AirImageAction,
) -> None:
    observed = _air_image_observed(status)
    if status is AirImageUploadStatus.READY:
        observed = replace(observed, size_bytes=0, sha256="")

    decision = decide_air_image_action(
        _air_image_intent(),
        observed,
        replace=False,
    )

    _assert_air_image_action(decision, expected)
    assert decision.drift == ()


@pytest.mark.parametrize(
    "status",
    [
        AirImageUploadStatus.PUBLISHING,
        AirImageUploadStatus.UNPUBLISHING,
        AirImageUploadStatus.COPYING_FROM_IMAGE_SHARE,
        AirImageUploadStatus.PENDING_PUBLISH,
        AirImageUploadStatus.PENDING_UNPUBLISH,
    ],
)
def test_unmanaged_air_image_lifecycle_is_refused(
    status: AirImageUploadStatus,
) -> None:
    decision = decide_air_image_action(
        _air_image_intent(),
        _air_image_observed(status),
        replace=False,
    )

    _assert_air_image_action(decision, AirImageAction.REFUSE_UNSUPPORTED)


@pytest.mark.parametrize(
    "change",
    [
        {"architecture": CpuArchitecture.UNKNOWN},
        {"upload_status": AirImageUploadStatus.UNKNOWN},
    ],
)
def test_unknown_air_image_configuration_is_refused(
    change: dict[str, object],
) -> None:
    decision = decide_air_image_action(
        _air_image_intent(),
        replace(_air_image_observed(), **change),
        replace=False,
    )

    _assert_air_image_action(decision, AirImageAction.REFUSE_UNKNOWN)


@pytest.mark.parametrize(
    ("change", "expected_field"),
    [
        ({"version": "2"}, "version"),
        ({"architecture": CpuArchitecture.ARM64}, "architecture"),
        ({"provider": "CONTAINER"}, "provider"),
        ({"size_bytes": 8192}, "source_size_bytes"),
        ({"sha256": "different"}, "source_sha256"),
        ({"owned_by_client": False}, "owned_by_client"),
    ],
)
def test_completed_air_image_material_drift_is_refused(
    change: dict[str, object],
    expected_field: str,
) -> None:
    decision = decide_air_image_action(
        _air_image_intent(),
        replace(_air_image_observed(), **change),
        replace=False,
    )

    _assert_air_image_action(decision, AirImageAction.REFUSE_DRIFT)
    assert decision.drift == (expected_field,)


def test_unuploaded_air_image_ignores_absent_content_metadata() -> None:
    observed = replace(
        _air_image_observed(AirImageUploadStatus.READY),
        size_bytes=0,
        sha256="",
    )

    decision = decide_air_image_action(
        _air_image_intent(),
        observed,
        replace=False,
    )

    _assert_air_image_action(decision, AirImageAction.UPLOAD)
    assert decision.drift == ()


def test_replace_authorization_replaces_existing_air_image() -> None:
    observed = replace(
        _air_image_observed(),
        architecture=CpuArchitecture.UNKNOWN,
        sha256="different",
    )

    decision = decide_air_image_action(
        _air_image_intent(),
        observed,
        replace=True,
    )

    _assert_air_image_action(decision, AirImageAction.REPLACE)
    assert decision.drift == ()


def _air_node_intent() -> AirNodeIntent:
    return AirNodeIntent(
        name="ocp-cp-0",
        cpu=16,
        memory_mib=65536,
        storage_gib=100,
        base_image_id=BLANK_IMAGE_ID,
        base_image_name="ocp-dsx-air-blank-x86_64-100g-qcow2-v1",
        discovery_image_id=AIR_IMAGE_ID,
        discovery_image_name="ocp-dsx-air-discovery-7a0ddc45",
        hardware=AirNodeHardwareIntent(
            boot_order=(AirBootDevice.HARD_DISK, AirBootDevice.CDROM),
            cpu_mode=AirCpuMode.HOST_PASSTHROUGH,
            nic_model="virtio",
            uefi=False,
            secureboot=False,
        ),
    )


def _air_node_observed() -> AirNodeSnapshot:
    intent = _air_node_intent()
    return AirNodeSnapshot(
        id=AIR_NODE_ID,
        name=intent.name,
        state="RUNNING",
        worker_status="",
        cpu=intent.cpu,
        memory_mib=intent.memory_mib,
        storage_gib=intent.storage_gib,
        base_image_id=intent.base_image_id,
        base_image_name=intent.base_image_name,
        discovery_image_id=intent.discovery_image_id,
        discovery_image_name=intent.discovery_image_name,
        hardware=AirNodeHardwareSnapshot(
            boot_order=intent.hardware.boot_order,
            cpu_mode=intent.hardware.cpu_mode,
            nic_model=intent.hardware.nic_model,
            uefi=intent.hardware.uefi,
            secureboot=intent.hardware.secureboot,
        ),
        management_ipv4s=(IPv4Address("192.168.200.10"),),
    )


def _air_simulation_intent() -> AirSimulationIntent:
    return AirSimulationIntent(
        name="ocp-lab",
        nodes=(_air_node_intent(),),
        auto_oob_enabled=True,
        enable_dhcp=True,
        topology_sha256="f40e225d44d3bb853e2644abc35e129c0513216ea33223373680166b0ed00e1d",
    )


def _air_simulation_observed(
    status: AirSimulationStatus = AirSimulationStatus.ACTIVE,
) -> AirSimulationSnapshot:
    intent = _air_simulation_intent()
    return AirSimulationSnapshot(
        id=AIR_SIMULATION_ID,
        name=intent.name,
        status=status,
        auto_oob_enabled=True,
        enable_dhcp=True,
        nodes=(_air_node_observed(),),
        complete_checkpoint_count=0,
        managed_by_us=True,
        metadata_schema=1,
        topology_sha256=intent.topology_sha256,
        managed_node_names=("ocp-cp-0",),
        topology_observed=True,
    )


def _assert_air_simulation_action(
    decision: AirSimulationDecision,
    expected: AirSimulationAction,
) -> None:
    assert decision.action is expected
    assert decision.reason.strip()


@pytest.mark.parametrize("replace", [False, True])
def test_absent_air_simulation_is_imported(replace: bool) -> None:
    decision = decide_air_simulation_action(
        _air_simulation_intent(),
        None,
        replace=replace,
    )

    _assert_air_simulation_action(decision, AirSimulationAction.IMPORT)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (AirSimulationStatus.CREATING, AirSimulationAction.WAIT_FOR_CREATION),
        (AirSimulationStatus.IMPORTING, AirSimulationAction.WAIT_FOR_CREATION),
        (AirSimulationStatus.INACTIVE, AirSimulationAction.START),
        (AirSimulationStatus.PROVISIONING, AirSimulationAction.WAIT_FOR_ACTIVE),
        (AirSimulationStatus.BOOTING, AirSimulationAction.WAIT_FOR_ACTIVE),
        (AirSimulationStatus.ACTIVE, AirSimulationAction.READY),
        (
            AirSimulationStatus.SHUTTING_DOWN,
            AirSimulationAction.WAIT_FOR_INACTIVE,
        ),
        (AirSimulationStatus.SAVING, AirSimulationAction.WAIT_FOR_INACTIVE),
        (AirSimulationStatus.DELETING, AirSimulationAction.WAIT_FOR_DELETION),
        (AirSimulationStatus.PURGING, AirSimulationAction.WAIT_FOR_DELETION),
    ],
)
def test_compatible_air_simulation_lifecycle_selects_next_action(
    status: AirSimulationStatus,
    expected: AirSimulationAction,
) -> None:
    decision = decide_air_simulation_action(
        _air_simulation_intent(),
        _air_simulation_observed(status),
        replace=False,
    )

    _assert_air_simulation_action(decision, expected)


def test_invalid_air_simulation_is_refused() -> None:
    decision = decide_air_simulation_action(
        _air_simulation_intent(),
        _air_simulation_observed(AirSimulationStatus.INVALID),
        replace=False,
    )

    _assert_air_simulation_action(decision, AirSimulationAction.REFUSE_TERMINAL)


@pytest.mark.parametrize(
    "status",
    [AirSimulationStatus.DEMO, AirSimulationStatus.TRAINING],
)
def test_non_lab_air_simulation_is_refused(status: AirSimulationStatus) -> None:
    decision = decide_air_simulation_action(
        _air_simulation_intent(),
        _air_simulation_observed(status),
        replace=False,
    )

    _assert_air_simulation_action(decision, AirSimulationAction.REFUSE_UNSUPPORTED)


def test_unknown_air_simulation_status_is_refused() -> None:
    decision = decide_air_simulation_action(
        _air_simulation_intent(),
        _air_simulation_observed(AirSimulationStatus.UNKNOWN),
        replace=False,
    )

    _assert_air_simulation_action(decision, AirSimulationAction.REFUSE_UNKNOWN)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (AirSimulationStatus.CREATING, AirSimulationAction.WAIT_FOR_CREATION),
        (AirSimulationStatus.BOOTING, AirSimulationAction.WAIT_FOR_ACTIVE),
        (
            AirSimulationStatus.SHUTTING_DOWN,
            AirSimulationAction.WAIT_FOR_INACTIVE,
        ),
        (AirSimulationStatus.ACTIVE, AirSimulationAction.SHUTDOWN_FOR_REPLACEMENT),
        (AirSimulationStatus.INACTIVE, AirSimulationAction.DELETE_FOR_REPLACEMENT),
        (AirSimulationStatus.INVALID, AirSimulationAction.DELETE_FOR_REPLACEMENT),
        (AirSimulationStatus.DELETING, AirSimulationAction.WAIT_FOR_DELETION),
    ],
)
def test_air_simulation_replacement_respects_lifecycle_state(
    status: AirSimulationStatus,
    expected: AirSimulationAction,
) -> None:
    decision = decide_air_simulation_action(
        _air_simulation_intent(),
        _air_simulation_observed(status),
        replace=True,
    )

    _assert_air_simulation_action(decision, expected)
    assert decision.drift == ()


@pytest.mark.parametrize(
    ("change", "expected_field"),
    [
        ({"auto_oob_enabled": False}, "auto_oob_enabled"),
        ({"enable_dhcp": False}, "enable_dhcp"),
        ({"managed_by_us": False}, "managed_by_us"),
        ({"metadata_schema": 2}, "metadata_schema"),
        ({"topology_sha256": "different"}, "topology_sha256"),
        ({"managed_node_names": ("unexpected",)}, "managed_node_names"),
    ],
)
def test_air_simulation_material_drift_is_refused(
    change: dict[str, object],
    expected_field: str,
) -> None:
    decision = decide_air_simulation_action(
        _air_simulation_intent(),
        replace(_air_simulation_observed(), **change),
        replace=False,
    )

    _assert_air_simulation_action(decision, AirSimulationAction.REFUSE_DRIFT)
    assert decision.drift == (expected_field,)


@pytest.mark.parametrize(
    ("change", "expected_field"),
    [
        ({"cpu": 8}, "cpu"),
        ({"memory_mib": 32768}, "memory_mib"),
        ({"storage_gib": 120}, "storage_gib"),
        ({"base_image_id": UUID(int=1)}, "base_image_id"),
        ({"discovery_image_id": UUID(int=2)}, "discovery_image_id"),
    ],
)
def test_air_node_material_drift_is_refused(
    change: dict[str, object],
    expected_field: str,
) -> None:
    observed = replace(
        _air_simulation_observed(),
        nodes=(replace(_air_node_observed(), **change),),
    )

    decision = decide_air_simulation_action(
        _air_simulation_intent(),
        observed,
        replace=False,
    )

    _assert_air_simulation_action(decision, AirSimulationAction.REFUSE_DRIFT)
    assert decision.drift == (f"nodes.ocp-cp-0.{expected_field}",)


@pytest.mark.parametrize(
    ("change", "expected_field"),
    [
        ({"boot_order": (AirBootDevice.CDROM,)}, "boot_order"),
        ({"cpu_mode": AirCpuMode.HOST_MODEL}, "cpu_mode"),
        ({"nic_model": "e1000"}, "nic_model"),
        ({"uefi": True}, "uefi"),
        ({"secureboot": True}, "secureboot"),
    ],
)
def test_air_node_hardware_material_drift_is_refused(
    change: dict[str, object],
    expected_field: str,
) -> None:
    node = _air_node_observed()
    observed = replace(
        _air_simulation_observed(),
        nodes=(replace(node, hardware=replace(node.hardware, **change)),),
    )

    decision = decide_air_simulation_action(
        _air_simulation_intent(),
        observed,
        replace=False,
    )

    _assert_air_simulation_action(decision, AirSimulationAction.REFUSE_DRIFT)
    assert decision.drift == (f"nodes.ocp-cp-0.hardware.{expected_field}",)


def test_emulated_pci_and_links_participate_in_simulation_drift() -> None:
    pci = AirNetworkPciIntent(
        name="nic1",
        emulation_type=AirNetworkPciEmulationType.NIC_ETHERNET,
        model="connectx7",
    )
    intended_node = replace(
        _air_node_intent(),
        hardware=replace(
            _air_node_intent().hardware,
            emulation_type=AirNodeEmulationType.HOST,
            network_pci=(pci,),
        ),
    )
    endpoint = AirLinkEndpoint("ocp-cp-0", "p0", "nic1")
    other = AirLinkEndpoint("ocp-cp-0", "p1", "nic1")
    intent = replace(
        _air_simulation_intent(),
        nodes=(intended_node,),
        links=(AirLinkIntent((endpoint, other)),),
    )
    observed_node = replace(
        _air_node_observed(),
        hardware=replace(
            _air_node_observed().hardware,
            emulation_type=AirNodeEmulationType.HOST,
            network_pci=(
                AirNetworkPciSnapshot(
                    name="nic1",
                    emulation_type=AirNetworkPciEmulationType.NIC_ETHERNET,
                    model="connectx6",
                ),
            ),
        ),
    )
    observed = replace(
        _air_simulation_observed(),
        nodes=(observed_node,),
        links=(AirLinkSnapshot((endpoint, other)),),
        topology_sha256=intent.topology_sha256,
    )

    decision = decide_air_simulation_action(intent, observed, replace=False)

    _assert_air_simulation_action(decision, AirSimulationAction.REFUSE_DRIFT)
    assert decision.drift == ("nodes.ocp-cp-0.hardware.network_pci",)


@pytest.mark.parametrize(
    "change",
    [
        {"boot_order": (AirBootDevice.UNKNOWN,)},
        {"cpu_mode": AirCpuMode.UNKNOWN},
    ],
)
def test_unknown_air_node_configuration_is_refused(
    change: dict[str, object],
) -> None:
    node = _air_node_observed()
    observed = replace(
        _air_simulation_observed(),
        nodes=(replace(node, hardware=replace(node.hardware, **change)),),
    )

    decision = decide_air_simulation_action(
        _air_simulation_intent(),
        observed,
        replace=False,
    )

    _assert_air_simulation_action(decision, AirSimulationAction.REFUSE_UNKNOWN)


def test_air_managed_oob_nodes_do_not_cause_material_drift() -> None:
    oob_node = replace(
        _air_node_observed(),
        id=UUID(int=3),
        name="oob-mgmt-server",
    )
    observed = replace(
        _air_simulation_observed(),
        nodes=(oob_node, _air_node_observed()),
    )

    decision = decide_air_simulation_action(
        _air_simulation_intent(),
        observed,
        replace=False,
    )

    _assert_air_simulation_action(decision, AirSimulationAction.READY)
