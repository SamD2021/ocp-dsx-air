import json
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest
from assisted_service_client import models

from ocp_dsx_air.adapters.assisted.mapping import (
    cluster_create_params,
    cluster_to_snapshot,
    host_to_snapshot,
    infraenv_create_params,
    infraenv_to_snapshot,
)
from ocp_dsx_air.core.contracts import (
    AssistedClusterIntent,
    AssistedClusterNetwork,
    AssistedInfraEnvIntent,
    ClusterStatus,
    CpuArchitecture,
    HostStatus,
    InfraEnvImageType,
    InstallStage,
    OpenShiftNodeRole,
)
from ocp_dsx_air.core.exceptions import AssistedError

CLUSTER_ID = UUID("5ad7357e-6c65-46e2-bad8-cd796cc82070")
HOST_ID = UUID("18b86b2e-46a7-43af-8de8-1a482cd68eb6")
INFRAENV_ID = UUID("7a0ddc45-ce1a-4d8d-ab9f-0be5fbe98d27")


def _intent() -> AssistedClusterIntent:
    return AssistedClusterIntent(
        name="ocp",
        ocp_version="4.19",
        base_dns_domain="dsx.air.local",
        architecture=CpuArchitecture.X86_64,
        ntp_sources=("0.rhel.pool.ntp.org", "time.google.com"),
        high_availability=True,
        control_plane_count=3,
        user_managed_networking=False,
        machine_networks=("192.168.200.0/24", "192.168.201.0/24"),
        cluster_networks=(AssistedClusterNetwork("10.128.0.0/14", 23),),
        service_networks=("172.30.0.0/16",),
        api_vips=("192.168.200.10", "192.168.201.10"),
        ingress_vips=("192.168.200.11", "192.168.201.11"),
    )


def _cluster(**changes: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "id": str(CLUSTER_ID),
        "name": "ocp",
        "status": "installing",
        "status_info": "Installation in progress",
        "openshift_version": "4.19",
        "base_dns_domain": "dsx.air.local",
        "cpu_architecture": "x86_64",
        "ntp_sources": "0.rhel.pool.ntp.org, time.google.com",
        "high_availability_mode": "Full",
        "control_plane_count": 3,
        "user_managed_networking": False,
        "machine_networks": [
            models.MachineNetwork(cidr="192.168.200.0/24"),
            models.MachineNetwork(cidr="192.168.201.0/24"),
        ],
        "cluster_networks": [
            models.ClusterNetwork(cidr="10.128.0.0/14", host_prefix=23)
        ],
        "service_networks": [models.ServiceNetwork(cidr="172.30.0.0/16")],
        "api_vips": [
            models.ApiVip(ip="192.168.200.10"),
            models.ApiVip(ip="192.168.201.10"),
        ],
        "ingress_vips": [
            models.IngressVip(ip="192.168.200.11"),
            models.IngressVip(ip="192.168.201.11"),
        ],
        "install_started_at": datetime(2026, 8, 31, tzinfo=UTC),
        "install_completed_at": None,
    }
    values.update(changes)
    return SimpleNamespace(**values)


def _host(**changes: object) -> SimpleNamespace:
    inventory = {
        "hostname": "master-0.internal",
        "interfaces": [
            {
                "ipv4_addresses": [
                    "192.168.200.20/24",
                    "10.0.0.20/24",
                ]
            },
            {"ipv4_addresses": ["192.168.200.20/24"]},
        ],
    }
    values: dict[str, object] = {
        "id": str(HOST_ID),
        "cluster_id": str(CLUSTER_ID),
        "requested_hostname": "master-0",
        "status": "installing-in-progress",
        "status_info": "Writing image",
        "role": "master",
        "inventory": json.dumps(inventory),
        "progress": models.HostProgressInfo(
            current_stage="Writing image to disk",
            progress_info="Writing RHCOS",
        ),
    }
    values.update(changes)
    return SimpleNamespace(**values)


def _infraenv_intent() -> AssistedInfraEnvIntent:
    return AssistedInfraEnvIntent(
        name="ocp-discovery",
        cluster_id=CLUSTER_ID,
        ocp_version="4.19",
        architecture=CpuArchitecture.X86_64,
        image_type=InfraEnvImageType.MINIMAL_ISO,
        ntp_sources=("0.rhel.pool.ntp.org", "time.google.com"),
        ssh_authorized_key="ssh-ed25519 public-key",
    )


def _infraenv(**changes: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "id": str(INFRAENV_ID),
        "name": "ocp-discovery",
        "cluster_id": str(CLUSTER_ID),
        "openshift_version": "4.19",
        "cpu_architecture": "x86_64",
        "type": "minimal-iso",
        "ntp_sources": "0.rhel.pool.ntp.org, time.google.com",
        "ssh_authorized_key": "ssh-ed25519 public-key",
        "pull_secret_set": True,
    }
    values.update(changes)
    return SimpleNamespace(**values)


def test_infraenv_model_maps_to_normalized_snapshot() -> None:
    snapshot = infraenv_to_snapshot(_infraenv(), iso_available=True)

    assert snapshot.id == INFRAENV_ID
    assert snapshot.name == "ocp-discovery"
    assert snapshot.cluster_id == CLUSTER_ID
    assert snapshot.ocp_version == "4.19"
    assert snapshot.architecture is CpuArchitecture.X86_64
    assert snapshot.image_type is InfraEnvImageType.MINIMAL_ISO
    assert snapshot.ntp_sources == (
        "0.rhel.pool.ntp.org",
        "time.google.com",
    )
    assert snapshot.ssh_authorized_key == "ssh-ed25519 public-key"
    assert snapshot.pull_secret_set is True
    assert snapshot.iso_available is True


def test_infraenv_intent_maps_to_exact_create_payload() -> None:
    params = infraenv_create_params(
        _infraenv_intent(),
        pull_secret="test-pull-secret",
    )

    assert params.name == "ocp-discovery"
    assert params.cluster_id == str(CLUSTER_ID)
    assert params.openshift_version == "4.19"
    assert params.cpu_architecture == "x86_64"
    assert params.image_type == "minimal-iso"
    assert params.ntp_sources == "0.rhel.pool.ntp.org,time.google.com"
    assert params.additional_ntp_sources is None
    assert params.ssh_authorized_key == "ssh-ed25519 public-key"
    assert params.pull_secret == "test-pull-secret"


def test_infraenv_absent_optional_values_are_empty_observations() -> None:
    snapshot = infraenv_to_snapshot(
        _infraenv(ntp_sources=None, ssh_authorized_key=None),
        iso_available=False,
    )

    assert snapshot.ntp_sources == ()
    assert snapshot.ssh_authorized_key == ""
    assert snapshot.iso_available is False


def test_unknown_infraenv_values_remain_visible() -> None:
    snapshot = infraenv_to_snapshot(
        _infraenv(cpu_architecture="riscv64", type="future-iso"),
        iso_available=True,
    )

    assert snapshot.architecture is CpuArchitecture.UNKNOWN
    assert snapshot.image_type is InfraEnvImageType.UNKNOWN


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"id": "not-a-uuid"}, "InfraEnv UUID"),
        ({"cluster_id": None}, "InfraEnv cluster UUID"),
        ({"name": ""}, "InfraEnv name"),
        ({"openshift_version": None}, "OpenShift version"),
        ({"pull_secret_set": None}, "pull-secret state"),
        ({"pull_secret_set": 1}, "pull-secret state"),
        ({"ntp_sources": ["time.google.com"]}, "NTP sources"),
        ({"ssh_authorized_key": 42}, "SSH authorized key"),
    ],
)
def test_infraenv_rejects_malformed_required_data(
    change: dict[str, object],
    error: str,
) -> None:
    with pytest.raises(AssistedError, match=error):
        infraenv_to_snapshot(_infraenv(**change), iso_available=True)


@pytest.mark.parametrize(
    "intent",
    [
        replace(_infraenv_intent(), architecture=CpuArchitecture.UNKNOWN),
        replace(_infraenv_intent(), image_type=InfraEnvImageType.UNKNOWN),
        replace(_infraenv_intent(), ssh_authorized_key=""),
    ],
)
def test_invalid_infraenv_intent_cannot_be_created(
    intent: AssistedInfraEnvIntent,
) -> None:
    with pytest.raises(AssistedError):
        infraenv_create_params(intent, pull_secret="test-pull-secret")


def test_infraenv_requires_pull_secret_for_creation() -> None:
    with pytest.raises(AssistedError, match="pull secret"):
        infraenv_create_params(_infraenv_intent(), pull_secret="")


def test_cluster_model_maps_to_normalized_snapshot() -> None:
    snapshot = cluster_to_snapshot(_cluster())

    assert snapshot.id == CLUSTER_ID
    assert snapshot.name == "ocp"
    assert snapshot.status is ClusterStatus.INSTALLING
    assert snapshot.status_info == "Installation in progress"
    assert snapshot.ocp_version == "4.19"
    assert snapshot.base_dns_domain == "dsx.air.local"
    assert snapshot.architecture is CpuArchitecture.X86_64
    assert snapshot.ntp_sources == ("0.rhel.pool.ntp.org", "time.google.com")
    assert snapshot.high_availability_mode == "Full"
    assert snapshot.control_plane_count == 3
    assert snapshot.user_managed_networking is False
    assert snapshot.machine_networks == (
        "192.168.200.0/24",
        "192.168.201.0/24",
    )
    assert snapshot.cluster_networks == (
        AssistedClusterNetwork("10.128.0.0/14", 23),
    )
    assert snapshot.service_networks == ("172.30.0.0/16",)
    assert snapshot.api_vips == ("192.168.200.10", "192.168.201.10")
    assert snapshot.ingress_vips == ("192.168.200.11", "192.168.201.11")
    assert snapshot.install_started is True
    assert snapshot.install_completed is False


def test_unknown_cluster_values_remain_visible() -> None:
    snapshot = cluster_to_snapshot(
        _cluster(status="future-status", cpu_architecture="riscv64")
    )

    assert snapshot.status is ClusterStatus.UNKNOWN
    assert snapshot.architecture is CpuArchitecture.UNKNOWN


@pytest.mark.parametrize("cluster_id", [None, "not-a-uuid"])
def test_cluster_requires_valid_uuid(cluster_id: str | None) -> None:
    with pytest.raises(AssistedError, match="cluster UUID"):
        cluster_to_snapshot(_cluster(id=cluster_id))


def test_cluster_rejects_boolean_control_plane_count() -> None:
    with pytest.raises(AssistedError, match="control-plane count"):
        cluster_to_snapshot(_cluster(control_plane_count=True))


def test_cluster_intent_maps_to_exact_create_payload() -> None:
    params = cluster_create_params(
        _intent(),
        pull_secret="test-pull-secret",
        ssh_public_key="ssh-ed25519 test-key",
    )

    assert params.name == "ocp"
    assert params.openshift_version == "4.19"
    assert params.base_dns_domain == "dsx.air.local"
    assert params.cpu_architecture == "x86_64"
    assert params.ntp_sources == "0.rhel.pool.ntp.org,time.google.com"
    assert params.high_availability_mode == "Full"
    assert params.control_plane_count == 3
    assert params.user_managed_networking is False
    assert params.vip_dhcp_allocation is False
    assert params.machine_networks is not None
    assert params.cluster_networks is not None
    assert params.service_networks is not None
    assert params.api_vips is not None
    assert params.ingress_vips is not None
    assert [network.cidr for network in params.machine_networks] == [
        "192.168.200.0/24",
        "192.168.201.0/24",
    ]
    assert [
        (network.cidr, network.host_prefix) for network in params.cluster_networks
    ] == [("10.128.0.0/14", 23)]
    assert [network.cidr for network in params.service_networks] == [
        "172.30.0.0/16"
    ]
    assert [vip.ip for vip in params.api_vips] == [
        "192.168.200.10",
        "192.168.201.10",
    ]
    assert [vip.ip for vip in params.ingress_vips] == [
        "192.168.200.11",
        "192.168.201.11",
    ]


def test_unknown_architecture_cannot_be_created() -> None:
    unknown = replace(_intent(), architecture=CpuArchitecture.UNKNOWN)

    with pytest.raises(AssistedError, match="architecture"):
        cluster_create_params(
            unknown,
            pull_secret="test-pull-secret",
            ssh_public_key="ssh-ed25519 test-key",
        )


def test_host_model_maps_inventory_and_progress() -> None:
    snapshot = host_to_snapshot(_host(), infraenv_id=INFRAENV_ID)

    assert snapshot.id == HOST_ID
    assert snapshot.infraenv_id == INFRAENV_ID
    assert snapshot.requested_hostname == "master-0"
    assert snapshot.inventory_hostname == "master-0.internal"
    assert snapshot.status is HostStatus.INSTALLING_IN_PROGRESS
    assert snapshot.status_info == "Writing image"
    assert snapshot.role is OpenShiftNodeRole.MASTER
    assert tuple(map(str, snapshot.ipv4_addresses)) == (
        "192.168.200.20",
        "10.0.0.20",
    )
    assert snapshot.install_stage is InstallStage.WRITING_IMAGE
    assert snapshot.progress_info == "Writing RHCOS"


def test_missing_inventory_is_an_empty_observation() -> None:
    snapshot = host_to_snapshot(
        _host(
            inventory=None,
            requested_hostname=None,
            status_info=None,
            role=None,
            progress=None,
        ),
        infraenv_id=INFRAENV_ID,
    )

    assert snapshot.inventory_hostname is None
    assert snapshot.ipv4_addresses == ()
    assert snapshot.requested_hostname is None
    assert snapshot.status_info == ""
    assert snapshot.role is None
    assert snapshot.install_stage is InstallStage.UNKNOWN
    assert snapshot.progress_info == ""


def test_unknown_host_status_and_stage_remain_visible() -> None:
    progress = models.HostProgressInfo(
        current_stage="A future stage",
        progress_info="Still working",
    )

    snapshot = host_to_snapshot(
        _host(status="future-status", progress=progress),
        infraenv_id=INFRAENV_ID,
    )

    assert snapshot.status is HostStatus.UNKNOWN
    assert snapshot.install_stage is InstallStage.UNKNOWN


@pytest.mark.parametrize(
    "inventory",
    ["not-json", json.dumps({"interfaces": [{"ipv4_addresses": ["not-an-ip"]}]})],
)
def test_nonempty_malformed_inventory_is_rejected(inventory: str) -> None:
    with pytest.raises(AssistedError, match="inventory"):
        host_to_snapshot(_host(inventory=inventory), infraenv_id=INFRAENV_ID)


@pytest.mark.parametrize("host_id", [None, "not-a-uuid"])
def test_host_requires_valid_uuid(host_id: str | None) -> None:
    with pytest.raises(AssistedError, match="host UUID"):
        host_to_snapshot(_host(id=host_id), infraenv_id=INFRAENV_ID)
