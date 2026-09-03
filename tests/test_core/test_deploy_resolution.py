from pathlib import Path

import pytest
from pydantic import ValidationError

from ocp_dsx_air.core.contracts import (
    AirNetworkPciEmulationType,
    AirNodeEmulationType,
    OpenShiftNodeRole,
)
from ocp_dsx_air.models.resolution import resolve_deploy_intent
from ocp_dsx_air.models.spec import LabSpec


def _spec(**cluster_changes: object) -> LabSpec:
    cluster: dict[str, object] = {
        "name": "ocp",
        "version": "4.19",
        "control_plane": {"count": 3},
        "workers": {"count": 2, "cpu": 8, "memory_mb": 32768},
    }
    cluster.update(cluster_changes)
    return LabSpec.model_validate(
        {
            "simulation": {"name": "dsx-lab"},
            "cluster": cluster,
        }
    )


def test_default_spec_resolves_complete_static_intent(tmp_path: Path) -> None:
    intent = resolve_deploy_intent(_spec(), cache_root=tmp_path)

    assert tuple(node.name for node in intent.nodes) == (
        "ocp-control-plane-0",
        "ocp-control-plane-1",
        "ocp-control-plane-2",
        "ocp-worker-0",
        "ocp-worker-1",
    )
    assert tuple(node.role for node in intent.nodes) == (
        OpenShiftNodeRole.MASTER,
        OpenShiftNodeRole.MASTER,
        OpenShiftNodeRole.MASTER,
        OpenShiftNodeRole.WORKER,
        OpenShiftNodeRole.WORKER,
    )
    assert intent.cluster.machine_networks == ("192.168.200.0/24",)
    assert intent.cluster.cluster_networks[0].host_prefix == 23
    assert intent.cluster.service_networks == ("172.30.0.0/16",)
    assert intent.cluster.api_vips == ("192.168.200.10",)
    assert intent.timeouts.discovery_seconds == 40 * 60
    assert intent.blank_disk.virtual_size_gib == 100


def test_explicit_node_names_and_timeout_override_are_respected(
    tmp_path: Path,
) -> None:
    spec = _spec(
        control_plane={
            "count": 3,
            "names": ["master-a", "master-b", "master-c"],
        },
        workers={"count": 1, "names": ["compute-a"]},
    )

    intent = resolve_deploy_intent(
        spec,
        cache_root=tmp_path,
        discovery_timeout_seconds=123,
    )

    assert tuple(node.name for node in intent.nodes) == (
        "master-a",
        "master-b",
        "master-c",
        "compute-a",
    )
    assert intent.timeouts.discovery_seconds == 123


def test_connectx_pool_and_link_resolve_to_domain_topology(tmp_path: Path) -> None:
    hardware = {
        "emulation_type": "HOST",
        "network_pci": [
            {
                "name": "nic1",
                "emulation_type": "NIC_ETHERNET",
                "model": "connectx7",
            }
        ],
    }
    spec = LabSpec.model_validate(
        {
            "simulation": {
                "name": "dsx-lab",
                "links": [
                    {
                        "endpoints": [
                            {
                                "node": "host1",
                                "network_pci": "nic1",
                                "interface": "p0",
                            },
                            {
                                "node": "host2",
                                "network_pci": "nic1",
                                "interface": "p0",
                            },
                        ]
                    }
                ],
            },
            "cluster": {
                "name": "ocp",
                "version": "4.19",
                "control_plane": {
                    "count": 3,
                    "names": ["host1", "host2", "host3"],
                    "hardware": hardware,
                },
                "workers": {"count": 0},
            },
        }
    )

    intent = resolve_deploy_intent(spec, cache_root=tmp_path)

    assert intent.nodes[0].hardware.emulation_type is AirNodeEmulationType.HOST
    assert (
        intent.nodes[0].hardware.network_pci[0].emulation_type
        is AirNetworkPciEmulationType.NIC_ETHERNET
    )
    assert intent.links[0].endpoints[1].node_name == "host2"


def test_sno_omits_ha_vips_from_assisted_intent(tmp_path: Path) -> None:
    spec = _spec(control_plane={"count": 1}, workers={"count": 0})

    intent = resolve_deploy_intent(spec, cache_root=tmp_path)

    assert intent.cluster.high_availability is False
    assert intent.cluster.api_vips == ()
    assert intent.cluster.ingress_vips == ()


@pytest.mark.parametrize(
    "cluster",
    [
        {
            "control_plane": {"count": 3, "names": ["one"]},
            "workers": {"count": 0},
        },
        {
            "control_plane": {"count": 1},
            "workers": {"count": 1},
        },
        {
            "control_plane": {"count": 3},
            "workers": {"count": 0},
            "api_vips": ["10.0.0.10"],
        },
    ],
)
def test_invalid_topology_or_network_is_rejected(cluster: dict[str, object]) -> None:
    data: dict[str, object] = {"name": "ocp", "version": "4.19", **cluster}

    with pytest.raises(ValidationError):
        LabSpec.model_validate(
            {"simulation": {"name": "dsx-lab"}, "cluster": data}
        )
