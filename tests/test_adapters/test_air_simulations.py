import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest

from ocp_dsx_air.adapters.air.adapter import NvidiaAirAdapter
from ocp_dsx_air.adapters.air.mapping import simulation_topology_sha256
from ocp_dsx_air.adapters.air.transport import AirApiTransport
from ocp_dsx_air.core.contracts import (
    AirBootDevice,
    AirCpuMode,
    AirLinkEndpoint,
    AirLinkIntent,
    AirNetworkPciEmulationType,
    AirNetworkPciIntent,
    AirNodeEmulationType,
    AirNodeHardwareIntent,
    AirNodeIntent,
    AirSimulationIntent,
    AirSimulationStatus,
)
from ocp_dsx_air.core.exceptions import AirSimError

SIMULATION_ID = UUID("1d798d44-9b22-4ec6-b9a1-d1f194294f95")
NODE_ID = UUID("ee9fa020-7b15-4d9d-a131-785daf83e978")
BLANK_IMAGE_ID = UUID("c966e5dc-d40e-41b9-b29e-7bf99d187793")
DISCOVERY_IMAGE_ID = UUID("62f3d8c7-7257-4ce4-a94a-cdcf052ccf3f")


def _node_intent() -> AirNodeIntent:
    return AirNodeIntent(
        name="ocp-cp-0",
        cpu=16,
        memory_mib=65536,
        storage_gib=100,
        base_image_id=BLANK_IMAGE_ID,
        base_image_name="ocp-dsx-air-blank-x86_64-100g-qcow2-v1-deadbeefcafe",
        discovery_image_id=DISCOVERY_IMAGE_ID,
        discovery_image_name="ocp-dsx-air-discovery-7a0ddc45",
        hardware=AirNodeHardwareIntent(
            boot_order=(AirBootDevice.HARD_DISK, AirBootDevice.CDROM),
            cpu_mode=AirCpuMode.HOST_PASSTHROUGH,
            nic_model="virtio",
            uefi=False,
            secureboot=False,
        ),
    )


def _intent(*, topology_sha256: str | None = None) -> AirSimulationIntent:
    provisional = AirSimulationIntent(
        name="ocp-lab",
        nodes=(_node_intent(),),
        auto_oob_enabled=True,
        enable_dhcp=True,
        topology_sha256="pending",
    )
    return AirSimulationIntent(
        name=provisional.name,
        nodes=provisional.nodes,
        auto_oob_enabled=provisional.auto_oob_enabled,
        enable_dhcp=provisional.enable_dhcp,
        topology_sha256=topology_sha256 or simulation_topology_sha256(provisional),
    )


def _image(image_id: UUID, name: str) -> SimpleNamespace:
    return SimpleNamespace(id=str(image_id), name=name)


def _node_model(**changes: object) -> SimpleNamespace:
    intent = _node_intent()
    fields: dict[str, object] = {
        "id": str(NODE_ID),
        "name": intent.name,
        "state": "RUNNING",
        "status_from_worker": "healthy",
        "cpu": intent.cpu,
        "memory": intent.memory_mib,
        "storage": intent.storage_gib,
        "image": _image(intent.base_image_id, intent.base_image_name),
        "cdrom": {
            "image": _image(intent.discovery_image_id, intent.discovery_image_name)
        },
        "advanced": {
            "boot": [device.value for device in intent.hardware.boot_order],
            "cpu_mode": intent.hardware.cpu_mode.value,
            "nic_model": intent.hardware.nic_model,
            "uefi": intent.hardware.uefi,
            "secureboot": intent.hardware.secureboot,
        },
        "management_interfaces": {
            "eth0": {"ip": "192.168.200.10/24"},
            "eth1": {"ip": None},
            "eth2": {"ip": "192.168.200.10"},
        },
    }
    fields.update(changes)
    return SimpleNamespace(**fields)


class FakeNodes:
    def __init__(self, nodes: object) -> None:
        self.nodes = nodes
        self.calls = 0

    def list(self) -> Any:
        self.calls += 1
        return self.nodes


def _metadata(intent: AirSimulationIntent) -> str:
    return json.dumps(
        {
            "managed_by": "ocp-dsx-air",
            "managed_nodes": ["ocp-cp-0"],
            "schema": 1,
            "topology_sha256": intent.topology_sha256,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _simulation_model(**changes: object) -> SimpleNamespace:
    intent = _intent()
    fields: dict[str, object] = {
        "id": str(SIMULATION_ID),
        "name": intent.name,
        "state": "ACTIVE",
        "auto_oob_enabled": True,
        "enable_dhcp": True,
        "complete_checkpoint_count": 0,
        "metadata": _metadata(intent),
        "nodes": FakeNodes([_node_model()]),
    }
    fields.update(changes)
    return SimpleNamespace(**fields)


class FakeSimulations:
    def __init__(self) -> None:
        self.list_result: object = []
        self.get_result: object = _simulation_model()
        self.import_result: object = SimpleNamespace(id=str(SIMULATION_ID))
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def list(self, **kwargs: object) -> Any:
        self.calls.append(("list", (), kwargs))
        return self.list_result

    def get(self, simulation_id: str) -> Any:
        self.calls.append(("get", (simulation_id,), {}))
        return self.get_result

    def import_from_simulation_manifest(self, **kwargs: object) -> Any:
        self.calls.append(("import", (), kwargs))
        return self.import_result

    def update(self, **kwargs: object) -> Any:
        self.calls.append(("update", (), kwargs))
        return self.get_result

    def start(self, **kwargs: object) -> None:
        self.calls.append(("start", (), kwargs))

    def shutdown(self, **kwargs: object) -> None:
        self.calls.append(("shutdown", (), kwargs))

    def delete(self, simulation_id: str) -> None:
        self.calls.append(("delete", (simulation_id,), {}))


def _adapter(
    simulations: FakeSimulations,
    *,
    images: object | None = None,
) -> NvidiaAirAdapter:
    api = SimpleNamespace(simulations=simulations, client=SimpleNamespace())
    if images is not None:
        api.images = images
    transport = AirApiTransport(
        api_key="nvapi-secret",
        _api_factory=lambda **kwargs: api,
    )
    return NvidiaAirAdapter(api_key="nvapi-secret", _transport=transport)


def test_find_simulation_returns_none_without_an_exact_match() -> None:
    simulations = FakeSimulations()
    simulations.list_result = [_simulation_model(name="ocp-lab-copy")]

    result = _adapter(simulations).find_simulation("ocp-lab")

    assert result is None
    assert simulations.calls == [("list", (), {"search": "ocp-lab"})]


def test_find_simulation_refetches_and_normalizes_nodes() -> None:
    simulations = FakeSimulations()
    simulations.list_result = [SimpleNamespace(id=str(SIMULATION_ID), name="ocp-lab")]

    result = _adapter(simulations).find_simulation("ocp-lab")

    assert result is not None
    assert result.id == SIMULATION_ID
    assert result.status is AirSimulationStatus.ACTIVE
    assert result.managed_by_us is True
    assert result.managed_node_names == ("ocp-cp-0",)
    assert result.nodes[0].id == NODE_ID
    assert result.nodes[0].base_image_id == BLANK_IMAGE_ID
    assert result.nodes[0].discovery_image_id == DISCOVERY_IMAGE_ID
    assert result.nodes[0].hardware.boot_order == (
        AirBootDevice.HARD_DISK,
        AirBootDevice.CDROM,
    )
    assert result.nodes[0].hardware.cpu_mode is AirCpuMode.HOST_PASSTHROUGH
    assert tuple(map(str, result.nodes[0].management_ipv4s)) == (
        "192.168.200.10",
    )
    assert simulations.calls == [
        ("list", (), {"search": "ocp-lab"}),
        ("get", (str(SIMULATION_ID),), {}),
    ]


def test_find_simulation_rejects_duplicate_exact_names() -> None:
    simulations = FakeSimulations()
    simulations.list_result = [
        _simulation_model(),
        _simulation_model(id=str(UUID(int=2))),
    ]

    with pytest.raises(AirSimError, match=r"multiple.*named"):
        _adapter(simulations).find_simulation("ocp-lab")


def test_unknown_simulation_and_node_values_remain_observable() -> None:
    simulations = FakeSimulations()
    simulations.list_result = [_simulation_model()]
    node = _node_model(
        advanced={
            "boot": ["future-device"],
            "cpu_mode": "future-mode",
            "nic_model": "virtio",
            "uefi": False,
            "secureboot": False,
        }
    )
    simulations.get_result = _simulation_model(
        state="FUTURE_STATE",
        nodes=FakeNodes([node]),
    )

    result = _adapter(simulations).find_simulation("ocp-lab")

    assert result is not None
    assert result.status is AirSimulationStatus.UNKNOWN
    assert result.nodes[0].hardware.boot_order == (AirBootDevice.UNKNOWN,)
    assert result.nodes[0].hardware.cpu_mode is AirCpuMode.UNKNOWN


@pytest.mark.parametrize(
    "changes",
    [
        {"id": "invalid"},
        {"name": ""},
        {"state": None},
        {"auto_oob_enabled": "yes"},
        {"enable_dhcp": 1},
        {"complete_checkpoint_count": -1},
        {"metadata": "not-json"},
    ],
)
def test_find_simulation_rejects_malformed_model(changes: dict[str, object]) -> None:
    simulations = FakeSimulations()
    simulations.list_result = [_simulation_model()]
    simulations.get_result = _simulation_model(**changes)

    with pytest.raises(AirSimError, match="invalid Air simulation"):
        _adapter(simulations).find_simulation("ocp-lab")


@pytest.mark.parametrize(
    "changes",
    [
        {"id": "invalid"},
        {"name": ""},
        {"cpu": 0},
        {"memory": True},
        {"image": None},
        {"cdrom": {"image": "invalid"}},
        {"advanced": {"boot": 1}},
        {"management_interfaces": {"eth0": {"ip": "not-an-ip"}}},
    ],
)
def test_find_simulation_rejects_malformed_node(changes: dict[str, object]) -> None:
    simulations = FakeSimulations()
    simulations.list_result = [_simulation_model()]
    simulations.get_result = _simulation_model(
        nodes=FakeNodes([_node_model(**changes)])
    )

    with pytest.raises(AirSimError, match="invalid Air node"):
        _adapter(simulations).find_simulation("ocp-lab")


def test_find_simulation_accepts_mapping_shaped_cdrom_image() -> None:
    simulations = FakeSimulations()
    simulations.list_result = [_simulation_model()]
    simulations.get_result = _simulation_model(
        nodes=FakeNodes(
            [
                _node_model(
                    cdrom={
                        "image": {
                            "id": str(DISCOVERY_IMAGE_ID),
                            "name": _node_intent().discovery_image_name,
                        }
                    }
                )
            ]
        )
    )

    result = _adapter(simulations).find_simulation("ocp-lab")

    assert result is not None
    assert result.nodes[0].discovery_image_id == DISCOVERY_IMAGE_ID


def test_find_simulation_resolves_string_cdrom_image_name() -> None:
    simulations = FakeSimulations()
    simulations.list_result = [_simulation_model()]
    simulations.get_result = _simulation_model(
        nodes=FakeNodes([_node_model(cdrom={"image": _node_intent().discovery_image_name})])
    )
    images = SimpleNamespace(list=lambda: iter([_image(DISCOVERY_IMAGE_ID, _node_intent().discovery_image_name)]))

    result = _adapter(simulations, images=images).find_simulation("ocp-lab")

    assert result is not None
    assert result.nodes[0].discovery_image_id == DISCOVERY_IMAGE_ID


def test_foreign_simulation_metadata_is_observable_as_unmanaged() -> None:
    simulations = FakeSimulations()
    simulations.list_result = [_simulation_model()]
    simulations.get_result = _simulation_model(metadata='{"owner":"someone-else"}')

    result = _adapter(simulations).find_simulation("ocp-lab")

    assert result is not None
    assert result.managed_by_us is False
    assert result.metadata_schema is None
    assert result.topology_sha256 is None
    assert result.managed_node_names == ()


def test_import_simulation_sends_deterministic_safe_manifest_and_claims_it() -> None:
    simulations = FakeSimulations()
    intent = _intent()

    result = _adapter(simulations).import_simulation(intent)

    assert result.id == SIMULATION_ID
    manifest = simulations.calls[0][2]["simulation_manifest"]
    assert manifest == {
        "format": "JSON",
        "ztp": None,
        "content": {
            "nodes": {
                "ocp-cp-0": {
                    "cpu": 16,
                    "memory": 65536,
                    "storage": 100,
                    "nic_model": "virtio",
                    "cpu_mode": "host-passthrough",
                    "cpu_options": [],
                    "secureboot": False,
                    "os": "ocp-dsx-air-blank-x86_64-100g-qcow2-v1-deadbeefcafe",
                    "storage_pci": None,
                    "pxehost": False,
                    "cdrom": "ocp-dsx-air-discovery-7a0ddc45",
                    "boot": ["hd", "cdrom"],
                    "features": {"uefi": False},
                }
            },
            "links": [],
            "oob": True,
        },
        "name": "ocp-lab",
    }
    assert simulations.calls[0][2]["attempt_start"] is False
    assert simulations.calls[1] == (
        "update",
        (),
        {"simulation": str(SIMULATION_ID), "metadata": _metadata(intent)},
    )
    assert simulations.calls[2] == ("get", (str(SIMULATION_ID),), {})


def test_topology_digest_uses_canonical_manifest_content() -> None:
    intent = _intent()
    expected_content = {
        "nodes": {
            "ocp-cp-0": {
                "cpu": 16,
                "memory": 65536,
                "storage": 100,
                "nic_model": "virtio",
                "cpu_mode": "host-passthrough",
                "cpu_options": [],
                "secureboot": False,
                "os": "ocp-dsx-air-blank-x86_64-100g-qcow2-v1-deadbeefcafe",
                "storage_pci": None,
                "pxehost": False,
                "cdrom": "ocp-dsx-air-discovery-7a0ddc45",
                "boot": ["hd", "cdrom"],
                "features": {"uefi": False},
            }
        },
        "links": [],
        "oob": True,
    }
    expected = hashlib.sha256(
        json.dumps(expected_content, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    assert simulation_topology_sha256(intent) == expected


def test_connectx_pci_topology_and_links_are_rendered_canonically() -> None:
    base = _node_intent()
    hardware = replace(
        base.hardware,
        emulation_type=AirNodeEmulationType.HOST,
        network_pci=(
            AirNetworkPciIntent(
                name="nic1",
                emulation_type=AirNetworkPciEmulationType.NIC_ETHERNET,
                model="connectx7",
            ),
        ),
    )
    first = replace(base, name="host1", hardware=hardware)
    second = replace(base, name="host2", hardware=hardware)
    link = AirLinkIntent(
        endpoints=(
            AirLinkEndpoint("host2", "p0", "nic1"),
            AirLinkEndpoint("host1", "p0", "nic1"),
        )
    )
    provisional = replace(
        _intent(),
        nodes=(second, first),
        links=(link,),
        topology_sha256="pending",
    )
    intent = replace(
        provisional,
        topology_sha256=simulation_topology_sha256(provisional),
    )
    simulations = FakeSimulations()

    _adapter(simulations).import_simulation(intent)

    manifest = cast(
        "dict[str, Any]",
        simulations.calls[0][2]["simulation_manifest"],
    )
    rendered = cast("dict[str, Any]", manifest["content"])
    assert list(rendered["nodes"]) == ["host1", "host2"]
    assert rendered["nodes"]["host1"]["emulation_type"] == "HOST"
    assert rendered["nodes"]["host1"]["network_pci"] == {
        "nic1": {"emulation_type": "NIC_ETHERNET", "model": "connectx7"}
    }
    assert rendered["links"] == [
        [
            {"node": "host1", "interface": "p0", "network_pci": "nic1"},
            {"node": "host2", "interface": "p0", "network_pci": "nic1"},
        ]
    ]


def test_import_rejects_mismatched_topology_digest() -> None:
    with pytest.raises(AirSimError, match="topology digest"):
        _adapter(FakeSimulations()).import_simulation(
            _intent(topology_sha256="0" * 64)
        )


def test_import_rejects_nonpositive_metadata_schema() -> None:
    with pytest.raises(AirSimError, match="metadata schema"):
        _adapter(FakeSimulations()).import_simulation(
            replace(_intent(), metadata_schema=0)
        )


@pytest.mark.parametrize(
    ("auto_oob_enabled", "enable_dhcp"),
    [(False, False), (True, False)],
)
def test_import_rejects_unsupported_oob_policy(
    auto_oob_enabled: bool,
    enable_dhcp: bool,
) -> None:
    original = _intent()
    intent = AirSimulationIntent(
        name=original.name,
        nodes=original.nodes,
        auto_oob_enabled=auto_oob_enabled,
        enable_dhcp=enable_dhcp,
        topology_sha256=original.topology_sha256,
    )

    with pytest.raises(AirSimError, match="OOB"):
        _adapter(FakeSimulations()).import_simulation(intent)


def test_start_simulation_omits_checkpoint_to_preserve_resume_semantics() -> None:
    simulations = FakeSimulations()

    _adapter(simulations).start_simulation(SIMULATION_ID)

    assert simulations.calls == [
        ("start", (), {"simulation": str(SIMULATION_ID)})
    ]


def test_shutdown_simulation_passes_explicit_checkpoint_policy() -> None:
    simulations = FakeSimulations()

    _adapter(simulations).shutdown_simulation(
        SIMULATION_ID,
        create_checkpoint=False,
    )

    assert simulations.calls == [
        (
            "shutdown",
            (),
            {"simulation": str(SIMULATION_ID), "create_checkpoint": False},
        )
    ]


def test_delete_simulation_uses_uuid() -> None:
    simulations = FakeSimulations()

    _adapter(simulations).delete_simulation(SIMULATION_ID)

    assert simulations.calls == [("delete", (str(SIMULATION_ID),), {})]
