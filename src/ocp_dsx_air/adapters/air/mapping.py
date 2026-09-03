"""Normalize NVIDIA Air SDK models into stable domain contracts."""

import hashlib
import json
from collections.abc import Mapping, Sequence
from ipaddress import IPv4Address, ip_interface
from typing import Any
from uuid import UUID

from ocp_dsx_air.core.contracts import (
    AirBootDevice,
    AirCpuMode,
    AirImageIntent,
    AirImageSnapshot,
    AirImageUploadStatus,
    AirLinkEndpoint,
    AirLinkIntent,
    AirLinkSnapshot,
    AirNetworkPciEmulationType,
    AirNetworkPciSnapshot,
    AirNodeEmulationType,
    AirNodeHardwareSnapshot,
    AirNodeIntent,
    AirNodeSnapshot,
    AirSimulationIntent,
    AirSimulationSnapshot,
    AirSimulationStatus,
    CpuArchitecture,
)
from ocp_dsx_air.core.exceptions import AirImageError, AirSimError

_AIR_ARCHITECTURES = {
    "x86": CpuArchitecture.X86_64,
    "x86_64": CpuArchitecture.X86_64,
    "ARM": CpuArchitecture.ARM64,
    "arm64": CpuArchitecture.ARM64,
}

_SDK_ARCHITECTURES = {
    CpuArchitecture.X86_64: "x86",
    CpuArchitecture.ARM64: "ARM",
}

_MANAGED_BY = "ocp-dsx-air"


def _image_uuid(value: object) -> UUID:
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise AirImageError("NVIDIA Air returned an invalid Air image UUID") from exc


def _required_text(model: object, field: str) -> str:
    value = getattr(model, field, None)
    if not isinstance(value, str) or not value.strip():
        raise AirImageError(f"NVIDIA Air returned an invalid Air image {field}")
    return value


def image_to_snapshot(model: object) -> AirImageSnapshot:
    """Map one full Air SDK image model into a domain snapshot."""
    raw_architecture = getattr(model, "cpu_arch", None)
    raw_status = getattr(model, "upload_status", None)
    size = getattr(model, "size", None)
    sha256 = getattr(model, "hash", None)
    owned_by_client = getattr(model, "is_owned_by_client", None)
    if not isinstance(raw_architecture, str):
        raise AirImageError("NVIDIA Air returned an invalid Air image cpu_arch")
    if not isinstance(raw_status, str):
        raise AirImageError("NVIDIA Air returned an invalid Air image upload_status")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise AirImageError("NVIDIA Air returned an invalid Air image size")
    if not isinstance(sha256, str):
        raise AirImageError("NVIDIA Air returned an invalid Air image hash")
    if not isinstance(owned_by_client, bool):
        raise AirImageError("NVIDIA Air returned invalid Air image ownership")

    try:
        status = AirImageUploadStatus(raw_status)
    except ValueError:
        status = AirImageUploadStatus.UNKNOWN
    if status is AirImageUploadStatus.COMPLETE:
        if size == 0 or len(sha256) != 64:
            raise AirImageError("NVIDIA Air returned invalid Air image content metadata")
        try:
            int(sha256, 16)
        except ValueError as exc:
            raise AirImageError(
                "NVIDIA Air returned invalid Air image content metadata"
            ) from exc

    return AirImageSnapshot(
        id=_image_uuid(getattr(model, "id", None)),
        name=_required_text(model, "name"),
        version=_required_text(model, "version"),
        architecture=_AIR_ARCHITECTURES.get(
            raw_architecture,
            CpuArchitecture.UNKNOWN,
        ),
        provider=_required_text(model, "provider"),
        upload_status=status,
        size_bytes=size,
        sha256=sha256,
        owned_by_client=owned_by_client,
    )


def image_create_payload(intent: AirImageIntent) -> dict[str, Any]:
    """Return validated SDK arguments for a managed image record."""
    if not intent.name.strip():
        raise AirImageError("Cannot create an Air image with an empty name")
    if not intent.version.strip():
        raise AirImageError("Cannot create an Air image with an empty version")
    if not intent.provider.strip():
        raise AirImageError("Cannot create an Air image with an empty provider")
    if intent.source_size_bytes <= 0:
        raise AirImageError("Cannot create an Air image from empty content")
    if len(intent.source_sha256) != 64:
        raise AirImageError("Cannot create an Air image with an invalid SHA-256")
    try:
        int(intent.source_sha256, 16)
    except ValueError as exc:
        raise AirImageError(
            "Cannot create an Air image with an invalid SHA-256"
        ) from exc
    try:
        architecture = _SDK_ARCHITECTURES[intent.architecture]
    except KeyError as exc:
        raise AirImageError(
            "Cannot create an Air image with an unknown architecture"
        ) from exc

    return {
        "name": intent.name,
        "version": intent.version,
        "default_username": "core",
        "default_password": "not-used",
        "cpu_arch": architecture,
        "provider": intent.provider,
    }


def _simulation_uuid(value: object, *, label: str) -> UUID:
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise AirSimError(f"NVIDIA Air returned an invalid Air {label} UUID") from exc


def _simulation_text(model: object, field: str, *, label: str) -> str:
    value = getattr(model, field, None)
    if not isinstance(value, str) or not value.strip():
        raise AirSimError(f"NVIDIA Air returned an invalid Air {label} {field}")
    return value


def _positive_int(model: object, field: str, *, label: str) -> int:
    value = getattr(model, field, None)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise AirSimError(f"NVIDIA Air returned an invalid Air {label} {field}")
    return value


def _image_reference(value: object, *, label: str) -> tuple[UUID, str]:
    if value is None:
        raise AirSimError(f"NVIDIA Air returned an invalid Air node {label}")
    if isinstance(value, Mapping):
        image_id = value.get("id")
        image_name = value.get("name")
    else:
        image_id = getattr(value, "id", None)
        image_name = getattr(value, "name", None)
    return (
        _simulation_uuid(image_id, label=f"node {label}"),
        _simulation_text_value(image_name, label=f"node {label} name"),
    )


def _simulation_text_value(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AirSimError(f"NVIDIA Air returned an invalid Air {label}")
    return value


def _boot_order(value: object) -> tuple[AirBootDevice, ...]:
    values: Sequence[object]
    if isinstance(value, str):
        values = (value,)
    elif isinstance(value, list) and value:
        values = value
    else:
        raise AirSimError("NVIDIA Air returned an invalid Air node boot order")
    if any(not isinstance(device, str) for device in values):
        raise AirSimError("NVIDIA Air returned an invalid Air node boot order")
    return tuple(
        AirBootDevice(device)
        if device in {member.value for member in AirBootDevice if member is not AirBootDevice.UNKNOWN}
        else AirBootDevice.UNKNOWN
        for device in values
    )


def _management_ipv4s(value: object) -> tuple[IPv4Address, ...]:
    if value is None:
        return ()
    if not isinstance(value, Mapping):
        raise AirSimError(
            "NVIDIA Air returned invalid Air node management interfaces"
        )
    addresses: list[IPv4Address] = []
    seen: set[IPv4Address] = set()
    for details in value.values():
        if not isinstance(details, Mapping):
            raise AirSimError(
                "NVIDIA Air returned invalid Air node management interfaces"
            )
        raw_address = details.get("ip")
        if raw_address is None:
            continue
        if not isinstance(raw_address, str) or not raw_address.strip():
            raise AirSimError(
                "NVIDIA Air returned invalid Air node management address"
            )
        try:
            address = ip_interface(raw_address).ip
        except ValueError as exc:
            raise AirSimError(
                "NVIDIA Air returned invalid Air node management address"
            ) from exc
        if not isinstance(address, IPv4Address) or address in seen:
            continue
        seen.add(address)
        addresses.append(address)
    return tuple(addresses)


def _node_hardware_snapshot(value: Mapping[object, object]) -> AirNodeHardwareSnapshot:
    raw_cpu_mode = value.get("cpu_mode")
    raw_nic_model = value.get("nic_model")
    raw_secureboot = value.get("secureboot")
    features = value.get("features")
    if not isinstance(raw_cpu_mode, str):
        raise AirSimError("NVIDIA Air export contains an invalid node CPU mode")
    if not isinstance(raw_nic_model, str) or not raw_nic_model.strip():
        raise AirSimError("NVIDIA Air export contains an invalid node NIC model")
    if not isinstance(raw_secureboot, bool) or not isinstance(features, Mapping):
        raise AirSimError("NVIDIA Air export contains invalid node firmware settings")
    uefi = features.get("uefi")
    if not isinstance(uefi, bool):
        raise AirSimError("NVIDIA Air export contains invalid node firmware settings")
    try:
        cpu_mode = AirCpuMode(raw_cpu_mode)
    except ValueError:
        cpu_mode = AirCpuMode.UNKNOWN

    raw_emulation_type = value.get("emulation_type")
    emulation_type: AirNodeEmulationType | None
    if raw_emulation_type is None:
        emulation_type = None
    elif isinstance(raw_emulation_type, str) and raw_emulation_type.strip():
        try:
            emulation_type = AirNodeEmulationType(raw_emulation_type)
        except ValueError:
            emulation_type = AirNodeEmulationType.UNKNOWN
    else:
        raise AirSimError("NVIDIA Air export contains invalid node emulation")

    raw_devices = value.get("network_pci", {})
    if not isinstance(raw_devices, Mapping):
        raise AirSimError("NVIDIA Air export contains invalid node PCI devices")
    devices: list[AirNetworkPciSnapshot] = []
    for name, details in raw_devices.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(details, Mapping):
            raise AirSimError("NVIDIA Air export contains invalid node PCI devices")
        raw_device_type = details.get("emulation_type")
        model = details.get("model")
        if (
            not isinstance(raw_device_type, str)
            or not raw_device_type.strip()
            or not isinstance(model, str)
            or not model.strip()
        ):
            raise AirSimError("NVIDIA Air export contains invalid node PCI devices")
        try:
            device_type = AirNetworkPciEmulationType(raw_device_type)
        except ValueError:
            device_type = AirNetworkPciEmulationType.UNKNOWN
        devices.append(
            AirNetworkPciSnapshot(
                name=name,
                emulation_type=device_type,
                model=model,
            )
        )

    return AirNodeHardwareSnapshot(
        boot_order=_boot_order(value.get("boot")),
        cpu_mode=cpu_mode,
        nic_model=raw_nic_model,
        uefi=uefi,
        secureboot=raw_secureboot,
        emulation_type=emulation_type,
        network_pci=tuple(sorted(devices, key=lambda device: device.name)),
    )


def node_to_snapshot(
    model: object,
    *,
    exported: Mapping[object, object] | None = None,
) -> AirNodeSnapshot:
    """Map one full Air SDK node model into a domain snapshot."""
    if exported is not None:
        hardware = _node_hardware_snapshot(exported)
    else:
        advanced = getattr(model, "advanced", None)
        if not isinstance(advanced, Mapping):
            raise AirSimError("NVIDIA Air returned invalid Air node advanced settings")

        raw_cpu_mode = advanced.get("cpu_mode")
        raw_nic_model = advanced.get("nic_model")
        uefi = advanced.get("uefi")
        secureboot = advanced.get("secureboot")
        if not isinstance(raw_cpu_mode, str):
            raise AirSimError("NVIDIA Air returned an invalid Air node CPU mode")
        if not isinstance(raw_nic_model, str) or not raw_nic_model.strip():
            raise AirSimError("NVIDIA Air returned an invalid Air node NIC model")
        if not isinstance(uefi, bool) or not isinstance(secureboot, bool):
            raise AirSimError("NVIDIA Air returned invalid Air node firmware settings")
        try:
            cpu_mode = AirCpuMode(raw_cpu_mode)
        except ValueError:
            cpu_mode = AirCpuMode.UNKNOWN
        hardware = AirNodeHardwareSnapshot(
            boot_order=_boot_order(advanced.get("boot")),
            cpu_mode=cpu_mode,
            nic_model=raw_nic_model,
            uefi=uefi,
            secureboot=secureboot,
        )

    base_image_id, base_image_name = _image_reference(
        getattr(model, "image", None),
        label="base image",
    )
    cdrom = getattr(model, "cdrom", None)
    discovery_image_id: UUID | None = None
    discovery_image_name: str | None = None
    if cdrom is not None:
        if not isinstance(cdrom, Mapping):
            raise AirSimError("NVIDIA Air returned invalid Air node CD-ROM settings")
        cdrom_image = cdrom.get("image")
        if cdrom_image is not None:
            discovery_image_id, discovery_image_name = _image_reference(
                cdrom_image,
                label="CD-ROM image",
            )

    worker_status = getattr(model, "status_from_worker", None)
    if not isinstance(worker_status, str):
        raise AirSimError("NVIDIA Air returned an invalid Air node worker status")

    return AirNodeSnapshot(
        id=_simulation_uuid(getattr(model, "id", None), label="node"),
        name=_simulation_text(model, "name", label="node"),
        state=_simulation_text(model, "state", label="node"),
        worker_status=worker_status,
        cpu=_positive_int(model, "cpu", label="node"),
        memory_mib=_positive_int(model, "memory", label="node"),
        storage_gib=_positive_int(model, "storage", label="node"),
        base_image_id=base_image_id,
        base_image_name=base_image_name,
        discovery_image_id=discovery_image_id,
        discovery_image_name=discovery_image_name,
        hardware=hardware,
        management_ipv4s=_management_ipv4s(
            getattr(model, "management_interfaces", None)
        ),
    )


def _simulation_metadata(
    value: object,
) -> tuple[bool, int | None, str | None, tuple[str, ...]]:
    if value is None or value == "":
        return False, None, None, ()
    if not isinstance(value, str):
        raise AirSimError("NVIDIA Air returned invalid Air simulation metadata")
    try:
        metadata: Any = json.loads(value)
    except json.JSONDecodeError as exc:
        raise AirSimError("NVIDIA Air returned invalid Air simulation metadata") from exc
    if not isinstance(metadata, Mapping):
        raise AirSimError("NVIDIA Air returned invalid Air simulation metadata")
    if metadata.get("managed_by") != _MANAGED_BY:
        return False, None, None, ()

    schema = metadata.get("schema")
    topology_sha256 = metadata.get("topology_sha256")
    managed_nodes = metadata.get("managed_nodes")
    if isinstance(schema, bool) or not isinstance(schema, int) or schema <= 0:
        raise AirSimError("NVIDIA Air returned invalid Air simulation metadata schema")
    if not isinstance(topology_sha256, str) or len(topology_sha256) != 64:
        raise AirSimError("NVIDIA Air returned invalid Air simulation topology digest")
    try:
        int(topology_sha256, 16)
    except ValueError as exc:
        raise AirSimError(
            "NVIDIA Air returned invalid Air simulation topology digest"
        ) from exc
    if not isinstance(managed_nodes, list) or any(
        not isinstance(name, str) or not name.strip() for name in managed_nodes
    ):
        raise AirSimError("NVIDIA Air returned invalid Air simulation managed nodes")
    normalized_names = tuple(sorted(managed_nodes))
    if len(normalized_names) != len(set(normalized_names)):
        raise AirSimError("NVIDIA Air returned duplicate Air simulation managed nodes")
    return True, schema, topology_sha256, normalized_names


def _exported_topology(
    value: object,
) -> tuple[Mapping[object, object], tuple[AirLinkSnapshot, ...]]:
    if not isinstance(value, Mapping):
        raise AirSimError("NVIDIA Air returned an invalid exported topology")
    content = value.get("content")
    if not isinstance(content, Mapping):
        raise AirSimError("NVIDIA Air returned an invalid exported topology content")
    nodes = content.get("nodes")
    links = content.get("links")
    if not isinstance(nodes, Mapping) or not isinstance(links, list):
        raise AirSimError("NVIDIA Air returned invalid exported topology resources")
    if any(
        not isinstance(name, str)
        or not name.strip()
        or not isinstance(configuration, Mapping)
        for name, configuration in nodes.items()
    ):
        raise AirSimError("NVIDIA Air export contains invalid node configuration")

    snapshots: list[AirLinkSnapshot] = []
    seen: set[tuple[tuple[str, str, str], ...]] = set()
    for raw_link in links:
        if not isinstance(raw_link, list) or len(raw_link) != 2:
            raise AirSimError("NVIDIA Air export contains an invalid link")
        endpoints: list[AirLinkEndpoint] = []
        for raw_endpoint in raw_link:
            if not isinstance(raw_endpoint, Mapping):
                raise AirSimError("NVIDIA Air export contains an invalid link endpoint")
            node_name = raw_endpoint.get("node")
            interface = raw_endpoint.get("interface")
            network_pci = raw_endpoint.get("network_pci")
            if (
                not isinstance(node_name, str)
                or not node_name.strip()
                or not isinstance(interface, str)
                or not interface.strip()
                or (
                    network_pci is not None
                    and (
                        not isinstance(network_pci, str)
                        or not network_pci.strip()
                    )
                )
            ):
                raise AirSimError("NVIDIA Air export contains an invalid link endpoint")
            if node_name not in nodes:
                raise AirSimError("NVIDIA Air export link references an unknown node")
            if network_pci is not None:
                node_configuration = nodes[node_name]
                if not isinstance(node_configuration, Mapping):
                    raise AirSimError(
                        "NVIDIA Air export contains invalid node configuration"
                    )
                raw_devices = node_configuration.get("network_pci", {})
                if not isinstance(raw_devices, Mapping) or network_pci not in raw_devices:
                    raise AirSimError(
                        "NVIDIA Air export link references an unknown PCI device"
                    )
            endpoints.append(
                AirLinkEndpoint(
                    node_name=node_name,
                    interface=interface,
                    network_pci_name=network_pci,
                )
            )
        endpoints.sort(key=_endpoint_key)
        key = tuple(_endpoint_key(endpoint) for endpoint in endpoints)
        if endpoints[0] == endpoints[1] or key in seen:
            raise AirSimError("NVIDIA Air export contains an invalid duplicate link")
        seen.add(key)
        snapshots.append(AirLinkSnapshot((endpoints[0], endpoints[1])))
    snapshots.sort(
        key=lambda link: tuple(_endpoint_key(endpoint) for endpoint in link.endpoints)
    )
    return nodes, tuple(snapshots)


def simulation_to_snapshot(
    model: object,
    nodes: Sequence[object],
    *,
    exported_topology: object | None = None,
) -> AirSimulationSnapshot:
    """Map one full Air simulation and its nodes into a domain snapshot."""
    raw_status = getattr(model, "state", None)
    if not isinstance(raw_status, str):
        raise AirSimError("NVIDIA Air returned an invalid Air simulation state")
    try:
        status = AirSimulationStatus(raw_status)
    except ValueError:
        status = AirSimulationStatus.UNKNOWN

    auto_oob_enabled = getattr(model, "auto_oob_enabled", None)
    enable_dhcp = getattr(model, "enable_dhcp", None)
    if auto_oob_enabled is not None and not isinstance(auto_oob_enabled, bool):
        raise AirSimError("NVIDIA Air returned invalid Air simulation auto OOB state")
    if enable_dhcp is not None and not isinstance(enable_dhcp, bool):
        raise AirSimError("NVIDIA Air returned invalid Air simulation DHCP state")
    checkpoint_count = getattr(model, "complete_checkpoint_count", None)
    if (
        isinstance(checkpoint_count, bool)
        or not isinstance(checkpoint_count, int)
        or checkpoint_count < 0
    ):
        raise AirSimError(
            "NVIDIA Air returned an invalid Air simulation checkpoint count"
        )
    managed, schema, topology_sha256, managed_node_names = _simulation_metadata(
        getattr(model, "metadata", None)
    )

    exported_nodes: Mapping[object, object] = {}
    links: tuple[AirLinkSnapshot, ...] = ()
    topology_observed = exported_topology is not None
    if exported_topology is not None:
        exported_nodes, links = _exported_topology(exported_topology)

    mapped_nodes: list[AirNodeSnapshot] = []
    for node in nodes:
        name = getattr(node, "name", None)
        exported_node = exported_nodes.get(name)
        if exported_node is not None and not isinstance(exported_node, Mapping):
            raise AirSimError("NVIDIA Air export contains invalid node configuration")
        mapped_nodes.append(node_to_snapshot(node, exported=exported_node))
    snapshots = tuple(sorted(mapped_nodes, key=lambda node: node.name))
    if len({node.id for node in snapshots}) != len(snapshots) or len(
        {node.name for node in snapshots}
    ) != len(snapshots):
        raise AirSimError("NVIDIA Air returned duplicate Air nodes")
    if managed and any(name not in exported_nodes for name in managed_node_names):
        raise AirSimError("NVIDIA Air export is missing a managed node")

    return AirSimulationSnapshot(
        id=_simulation_uuid(getattr(model, "id", None), label="simulation"),
        name=_simulation_text(model, "name", label="simulation"),
        status=status,
        auto_oob_enabled=auto_oob_enabled,
        enable_dhcp=enable_dhcp,
        nodes=snapshots,
        complete_checkpoint_count=checkpoint_count,
        managed_by_us=managed,
        metadata_schema=schema,
        topology_sha256=topology_sha256,
        managed_node_names=managed_node_names,
        links=links,
        topology_observed=topology_observed,
    )


def _node_manifest(node: AirNodeIntent) -> dict[str, Any]:
    if not node.name.strip():
        raise AirSimError("Cannot import an Air simulation with an empty node name")
    for field in ("cpu", "memory_mib", "storage_gib"):
        value = getattr(node, field)
        if isinstance(value, bool) or value <= 0:
            raise AirSimError(
                f"Cannot import an Air simulation with invalid node {field}"
            )
    if not node.base_image_name.strip() or not node.discovery_image_name.strip():
        raise AirSimError("Cannot import an Air simulation with an empty image name")
    hardware = node.hardware
    if (
        not hardware.boot_order
        or AirBootDevice.UNKNOWN in hardware.boot_order
        or hardware.cpu_mode is AirCpuMode.UNKNOWN
    ):
        raise AirSimError("Cannot import an Air simulation with unknown node settings")
    if hardware.nic_model not in {"virtio", "e1000"}:
        raise AirSimError("Cannot import an Air simulation with an unsupported NIC model")
    if hardware.emulation_type is AirNodeEmulationType.UNKNOWN:
        raise AirSimError("Cannot import an Air simulation with unknown node emulation")

    devices: dict[str, dict[str, str]] = {}
    for device in sorted(hardware.network_pci, key=lambda candidate: candidate.name):
        if not device.name.strip() or device.name in devices:
            raise AirSimError(
                "Air simulation node PCI device names must be non-empty and unique"
            )
        if (
            device.emulation_type is AirNetworkPciEmulationType.UNKNOWN
            or not device.model.strip()
        ):
            raise AirSimError("Cannot import an Air simulation with unknown PCI settings")
        devices[device.name] = {
            "emulation_type": device.emulation_type.value,
            "model": device.model,
        }

    if devices and hardware.emulation_type is not AirNodeEmulationType.HOST:
        raise AirSimError("Air simulation PCI devices require HOST node emulation")

    manifest: dict[str, Any] = {
        "cpu": node.cpu,
        "memory": node.memory_mib,
        "storage": node.storage_gib,
        "nic_model": hardware.nic_model,
        "cpu_mode": hardware.cpu_mode.value,
        "cpu_options": [],
        "secureboot": hardware.secureboot,
        "os": node.base_image_name,
        "storage_pci": None,
        "pxehost": False,
        "cdrom": node.discovery_image_name,
        "boot": [device.value for device in hardware.boot_order],
        "features": {"uefi": hardware.uefi},
    }
    if hardware.emulation_type is not None:
        manifest["emulation_type"] = hardware.emulation_type.value
    if devices:
        manifest["network_pci"] = devices
    return manifest


def _endpoint_key(endpoint: AirLinkEndpoint) -> tuple[str, str, str]:
    return (
        endpoint.node_name,
        endpoint.network_pci_name or "",
        endpoint.interface,
    )


def _link_manifest(
    link: AirLinkIntent,
    *,
    nodes: Mapping[str, AirNodeIntent],
) -> list[dict[str, str]]:
    if len(link.endpoints) != 2:
        raise AirSimError("Air simulation links must have exactly two endpoints")
    endpoints = tuple(sorted(link.endpoints, key=_endpoint_key))
    if endpoints[0] == endpoints[1]:
        raise AirSimError("Air simulation links cannot connect an endpoint to itself")

    rendered: list[dict[str, str]] = []
    for endpoint in endpoints:
        if not endpoint.node_name.strip() or not endpoint.interface.strip():
            raise AirSimError("Air simulation link endpoints must be non-empty")
        node = nodes.get(endpoint.node_name)
        if node is None:
            raise AirSimError("Air simulation link references an unknown node")
        value = {
            "node": endpoint.node_name,
            "interface": endpoint.interface,
        }
        if endpoint.network_pci_name is not None:
            device_names = {device.name for device in node.hardware.network_pci}
            if endpoint.network_pci_name not in device_names:
                raise AirSimError(
                    "Air simulation link references an unknown PCI device"
                )
            value["network_pci"] = endpoint.network_pci_name
        rendered.append(value)
    return rendered


def simulation_content(intent: AirSimulationIntent) -> dict[str, Any]:
    """Render the canonical Air topology content represented by intent."""
    if not intent.name.strip():
        raise AirSimError("Cannot import an Air simulation with an empty name")
    if not intent.auto_oob_enabled or not intent.enable_dhcp:
        raise AirSimError("Only automatic Air OOB networking with DHCP is supported")
    names = [node.name for node in intent.nodes]
    if not names or len(names) != len(set(names)):
        raise AirSimError("Air simulation managed node names must be non-empty and unique")
    nodes = {node.name: node for node in intent.nodes}
    links = [
        _link_manifest(link, nodes=nodes)
        for link in sorted(
            intent.links,
            key=lambda candidate: tuple(
                sorted(_endpoint_key(endpoint) for endpoint in candidate.endpoints)
            ),
        )
    ]
    if len({json.dumps(link, sort_keys=True) for link in links}) != len(links):
        raise AirSimError("Air simulation links must be unique")
    return {
        "nodes": {
            node.name: _node_manifest(node)
            for node in sorted(intent.nodes, key=lambda candidate: candidate.name)
        },
        "links": links,
        "oob": True,
    }


def simulation_topology_sha256(intent: AirSimulationIntent) -> str:
    """Hash canonical topology content independently of JSON formatting."""
    content = simulation_content(intent)
    encoded = json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def simulation_manifest(intent: AirSimulationIntent) -> dict[str, Any]:
    """Return a validated deterministic manifest for Air import."""
    content = simulation_content(intent)
    digest = hashlib.sha256(
        json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if digest != intent.topology_sha256:
        raise AirSimError("Air simulation topology digest does not match its content")
    return {
        "format": "JSON",
        "ztp": None,
        "content": content,
        "name": intent.name,
    }


def simulation_metadata(intent: AirSimulationIntent) -> str:
    """Return the canonical ownership marker attached after import."""
    if (
        not isinstance(intent.metadata_schema, int)
        or isinstance(intent.metadata_schema, bool)
        or intent.metadata_schema <= 0
    ):
        raise AirSimError("Air simulation metadata schema must be a positive integer")
    return json.dumps(
        {
            "managed_by": _MANAGED_BY,
            "managed_nodes": sorted(node.name for node in intent.nodes),
            "schema": intent.metadata_schema,
            "topology_sha256": intent.topology_sha256,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
