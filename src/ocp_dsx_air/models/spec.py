"""Lab spec: simulation + cluster + auth file pointers."""

import json
import os
import re
from ipaddress import IPv4Address, IPv4Network, ip_address, ip_network
from pathlib import Path
from typing import Any, Self

import yaml
from pydantic import BaseModel, Field, model_validator

from ocp_dsx_air.core.common import cache_dir
from ocp_dsx_air.core.contracts import (
    AirBootDevice,
    AirCpuMode,
    AirNetworkPciEmulationType,
    AirNodeEmulationType,
    CpuArchitecture,
)

_ENV_VAR = re.compile(r"\$\{([^}]+)\}")
_NODE_NAME = re.compile(r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$")


def _ipv4_networks(values: list[str], field: str) -> tuple[IPv4Network, ...]:
    try:
        parsed = tuple(ip_network(value, strict=True) for value in values)
    except ValueError as exc:
        raise ValueError(f"{field} must contain valid canonical IPv4 CIDRs") from exc
    if not parsed or any(not isinstance(network, IPv4Network) for network in parsed):
        raise ValueError(f"{field} must contain at least one IPv4 CIDR")
    return parsed  # type: ignore[return-value]


def _ipv4_addresses(values: list[str], field: str) -> tuple[IPv4Address, ...]:
    try:
        parsed = tuple(ip_address(value) for value in values)
    except ValueError as exc:
        raise ValueError(f"{field} must contain valid IPv4 addresses") from exc
    if not parsed or any(not isinstance(address, IPv4Address) for address in parsed):
        raise ValueError(f"{field} must contain at least one IPv4 address")
    return parsed  # type: ignore[return-value]


class NetworkPciSpec(BaseModel):
    name: str
    emulation_type: AirNetworkPciEmulationType
    model: str


class NodeHardwareSpec(BaseModel):
    boot_order: list[AirBootDevice] = Field(
        default_factory=lambda: [AirBootDevice.HARD_DISK, AirBootDevice.CDROM]
    )
    cpu_mode: AirCpuMode = AirCpuMode.HOST_PASSTHROUGH
    nic_model: str = "virtio"
    uefi: bool = False
    secureboot: bool = False
    emulation_type: AirNodeEmulationType | None = None
    network_pci: list[NetworkPciSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_hardware(self) -> Self:
        device_names = [device.name for device in self.network_pci]
        if any(not name.strip() for name in device_names):
            raise ValueError("PCI device names must be non-empty")
        if len(device_names) != len(set(device_names)):
            raise ValueError("PCI device names must be unique within a node")
        if any(not device.model.strip() for device in self.network_pci):
            raise ValueError("PCI device models must be non-empty")
        if self.network_pci and self.emulation_type is not AirNodeEmulationType.HOST:
            raise ValueError("PCI devices require HOST node emulation")
        if not self.boot_order or AirBootDevice.UNKNOWN in self.boot_order:
            raise ValueError("boot_order must contain supported boot devices")
        if self.cpu_mode is AirCpuMode.UNKNOWN:
            raise ValueError("cpu_mode must be supported")
        if not self.nic_model.strip():
            raise ValueError("nic_model must be non-empty")
        return self


class NodePool(BaseModel):
    count: int = Field(ge=0)
    names: list[str] | None = None
    cpu: int = Field(default=16, gt=0)
    memory_mb: int = Field(default=65536, gt=0)
    disk_gb: int = Field(default=100, gt=0)
    hardware: NodeHardwareSpec = Field(default_factory=NodeHardwareSpec)

    @model_validator(mode="after")
    def validate_names(self) -> Self:
        if self.names is not None and len(self.names) != self.count:
            raise ValueError("node name count must match pool count")
        return self


class ClusterNetworkSpec(BaseModel):
    cidr: str = "10.128.0.0/14"
    host_prefix: int = 23


class LinkEndpointSpec(BaseModel):
    node: str
    interface: str
    network_pci: str | None = None


class LinkSpec(BaseModel):
    endpoints: tuple[LinkEndpointSpec, LinkEndpointSpec]


class ClusterSpec(BaseModel):
    name: str
    version: str
    control_plane: NodePool
    workers: NodePool = Field(default_factory=lambda: NodePool(count=0, cpu=8, memory_mb=32768, disk_gb=100))
    architecture: CpuArchitecture = CpuArchitecture.X86_64
    base_dns_domain: str = "dsx.air.local"
    ntp_sources: list[str] = Field(default_factory=list)
    machine_networks: list[str] = Field(
        default_factory=lambda: ["192.168.200.0/24"]
    )
    cluster_networks: list[ClusterNetworkSpec] = Field(
        default_factory=lambda: [ClusterNetworkSpec()]
    )
    service_networks: list[str] = Field(
        default_factory=lambda: ["172.30.0.0/16"]
    )
    api_vips: list[str] = Field(default_factory=lambda: ["192.168.200.10"])
    ingress_vips: list[str] = Field(
        default_factory=lambda: ["192.168.200.11"]
    )

    @model_validator(mode="after")
    def validate_networking(self) -> Self:
        machine_networks = _ipv4_networks(self.machine_networks, "machine_networks")
        _ipv4_networks(self.service_networks, "service_networks")
        for network in self.cluster_networks:
            parsed = _ipv4_networks([network.cidr], "cluster_networks")[0]
            if not parsed.prefixlen < network.host_prefix <= 32:
                raise ValueError("cluster network host_prefix must exceed its prefix")
        api_vips = _ipv4_addresses(self.api_vips, "api_vips")
        ingress_vips = _ipv4_addresses(self.ingress_vips, "ingress_vips")
        if set(api_vips) & set(ingress_vips):
            raise ValueError("API and Ingress VIPs must be distinct")
        for vip in (*api_vips, *ingress_vips):
            if not any(vip in network for network in machine_networks):
                raise ValueError("API and Ingress VIPs must belong to a machine network")
        return self


class AuthSpec(BaseModel):
    air_api_key_file: str | None = None
    ai_offlinetoken_file: str | None = None
    pull_secret_file: str | None = None
    ssh_public_key_file: str | None = None
    jump_host_password_file: str | None = None


class SimulationSpec(BaseModel):
    name: str
    links: list[LinkSpec] = Field(default_factory=list)


class LabSpec(BaseModel):
    simulation: SimulationSpec
    cluster: ClusterSpec
    auth: AuthSpec = Field(default_factory=AuthSpec)

    @model_validator(mode="after")
    def validate_topology(self) -> Self:
        if self.cluster.control_plane.count not in {1, 3}:
            raise ValueError("control-plane count must be 1 or 3")
        if self.cluster.workers.count and self.cluster.control_plane.count != 3:
            raise ValueError("worker nodes require three control-plane nodes")
        names = _resolved_node_names(self)
        if len(names) != len(set(names)):
            raise ValueError("node names must be globally unique")
        valid_names = set(names)
        devices_by_node = {
            name: {device.name for device in pool.hardware.network_pci}
            for pool, pool_names in (
                (self.cluster.control_plane, names[: self.cluster.control_plane.count]),
                (self.cluster.workers, names[self.cluster.control_plane.count :]),
            )
            for name in pool_names
        }
        for link in self.simulation.links:
            if link.endpoints[0] == link.endpoints[1]:
                raise ValueError("links cannot connect an endpoint to itself")
            for endpoint in link.endpoints:
                if endpoint.node not in valid_names:
                    raise ValueError("link references an unknown node")
                if not endpoint.interface.strip():
                    raise ValueError("link interfaces must be non-empty")
                if (
                    endpoint.network_pci is not None
                    and endpoint.network_pci not in devices_by_node[endpoint.node]
                ):
                    raise ValueError("link references an unknown PCI device")
        return self

    def merge(
        self,
        *,
        sim: str | None = None,
        cluster: str | None = None,
        control_plane: int | None = None,
        workers: int | None = None,
        ocp_version: str | None = None,
    ) -> Self:
        data = self.model_dump()
        if sim is not None:
            data["simulation"]["name"] = sim
        if cluster is not None:
            data["cluster"]["name"] = cluster
        if control_plane is not None:
            data["cluster"]["control_plane"]["count"] = control_plane
        if workers is not None:
            data["cluster"]["workers"]["count"] = workers
        if ocp_version is not None:
            data["cluster"]["version"] = ocp_version
        return type(self).model_validate(data)

    @property
    def expected_hosts(self) -> int:
        return self.cluster.control_plane.count + self.cluster.workers.count

    @property
    def profile(self) -> str:
        if self.cluster.control_plane.count > 1 or self.cluster.workers.count:
            return "multinode"
        return "sno"


def _pool_node_names(
    cluster_name: str,
    pool: NodePool,
    label: str,
) -> tuple[str, ...]:
    names = (
        tuple(pool.names)
        if pool.names is not None
        else tuple(f"{cluster_name}-{label}-{index}" for index in range(pool.count))
    )
    if any(not _NODE_NAME.fullmatch(name) or len(name) > 63 for name in names):
        raise ValueError("node names must be lowercase DNS labels of at most 63 characters")
    return names


def _resolved_node_names(spec: LabSpec) -> tuple[str, ...]:
    return (
        *_pool_node_names(
            spec.cluster.name,
            spec.cluster.control_plane,
            "control-plane",
        ),
        *_pool_node_names(spec.cluster.name, spec.cluster.workers, "worker"),
    )


def expand_path(raw: str) -> Path:
    """Expand ~ and ${ENV} in a path string (not secret values)."""

    def _sub(match: re.Match[str]) -> str:
        name = match.group(1)
        value = os.environ.get(name)
        if not value:
            raise SystemExit(f"Environment variable {name} is unset in path {raw!r}.")
        return value

    expanded = _ENV_VAR.sub(_sub, raw)
    return Path(expanded).expanduser()


def load_spec(path: Path) -> LabSpec:
    text = path.read_text()
    suffix = path.suffix.lower()
    data: Any
    if suffix in {".yaml", ".yml"}:
        data = yaml.safe_load(text)
    elif suffix == ".json":
        data = json.loads(text)
    elif suffix == ".toml":
        import tomllib

        data = tomllib.loads(text)
    else:
        raise SystemExit(f"Unsupported spec format: {path.suffix} (use yaml, toml, or json)")
    if not isinstance(data, dict):
        raise SystemExit(f"Spec {path} must be a mapping.")
    return LabSpec.model_validate(data)


def activate_spec(spec_path: Path | None) -> LabSpec | None:
    """Load a lab spec into env (CLUSTER_NAME, SIMULATION_NAME, auth files)."""
    if spec_path is None:
        return None

    spec = load_spec(spec_path)
    preflight_auth(spec)
    topo = cache_dir() / spec.simulation.name / "topology.json"
    apply_to_environ(spec, topology_path=topo if topo.is_file() else None)
    return spec


def apply_to_environ(spec: LabSpec, *, topology_path: Path | None = None) -> None:
    """Export spec into env vars numbered scripts already read."""
    os.environ["CLUSTER_NAME"] = spec.cluster.name
    os.environ["SIMULATION_NAME"] = spec.simulation.name
    os.environ["OCP_VERSION"] = spec.cluster.version
    os.environ["CLUSTER_PROFILE"] = spec.profile
    os.environ["CONTROL_PLANE_COUNT"] = str(spec.cluster.control_plane.count)
    os.environ["EXPECTED_HOSTS"] = str(spec.expected_hosts)
    if topology_path is not None:
        os.environ["TOPOLOGY_PATH"] = str(topology_path)
    mapping = (
        ("air_api_key_file", "AIR_API_KEY_FILE"),
        ("ai_offlinetoken_file", "AI_OFFLINETOKEN_FILE"),
        ("pull_secret_file", "PULL_SECRET_PATH"),
        ("ssh_public_key_file", "SSH_PUBLIC_KEY_PATH"),
    )
    for field, env_name in mapping:
        raw = getattr(spec.auth, field)
        if raw:
            os.environ[env_name] = str(expand_path(raw))


def preflight_auth(spec: LabSpec) -> None:
    """Fail immediately if spec auth files are missing."""
    checks = (
        ("auth.air_api_key_file", spec.auth.air_api_key_file, "Air API key"),
        ("auth.ai_offlinetoken_file", spec.auth.ai_offlinetoken_file, "Assisted Installer offline token"),
        ("auth.pull_secret_file", spec.auth.pull_secret_file, "pull secret"),
        ("auth.ssh_public_key_file", spec.auth.ssh_public_key_file, "SSH public key"),
        (
            "auth.jump_host_password_file",
            spec.auth.jump_host_password_file,
            "jump-host password",
        ),
    )
    for key, raw, what in checks:
        if not raw:
            raise SystemExit(f"Missing required {what} file ({key}).")
        path = expand_path(raw)
        if not path.is_file():
            raise SystemExit(f"{what} file not found ({key}): {path}")
        if not path.read_text().strip() and key != "auth.pull_secret_file":
            raise SystemExit(f"{what} file is empty ({key}): {path}")
        if key == "auth.pull_secret_file" and path.stat().st_size == 0:
            raise SystemExit(f"{what} file is empty ({key}): {path}")
