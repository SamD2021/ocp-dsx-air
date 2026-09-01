"""Translate generated Assisted Service models at the adapter boundary."""

import json
from ipaddress import IPv4Address, ip_interface
from typing import Any, TypeVar
from uuid import UUID

from assisted_service_client import models

from ocp_dsx_air.core.contracts import (
    AssistedClusterIntent,
    AssistedClusterSnapshot,
    AssistedHostSnapshot,
    AssistedInfraEnvIntent,
    AssistedInfraEnvSnapshot,
    ClusterStatus,
    CpuArchitecture,
    HostStatus,
    InfraEnvImageType,
    InstallStage,
)
from ocp_dsx_air.core.exceptions import AssistedError

_EnumT = TypeVar(
    "_EnumT",
    ClusterStatus,
    CpuArchitecture,
    HostStatus,
    InfraEnvImageType,
    InstallStage,
)


def _required_uuid(raw: object, *, label: str) -> UUID:
    try:
        return UUID(str(raw))
    except (TypeError, ValueError, AttributeError) as exc:
        raise AssistedError(f"Assisted returned an invalid {label} UUID") from exc


def _required_text(
    raw: object,
    *,
    label: str,
    resource: str = "cluster",
) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise AssistedError(f"Assisted returned an invalid {resource} {label}")
    return raw.strip()


def _optional_text(raw: object) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise AssistedError("Assisted returned invalid optional text")
    return raw.strip() or None


def _text_or_empty(raw: object, *, label: str) -> str:
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise AssistedError(f"Assisted returned invalid {label}")
    return raw.strip()


def _enum_or_unknown(enum_type: type[_EnumT], raw: object) -> _EnumT:
    try:
        return enum_type(str(raw))
    except ValueError:
        return enum_type("unknown")


def _ordered_values(items: object, attribute: str) -> tuple[str, ...]:
    if items is None:
        return ()
    try:
        values = []
        for item in items:  # type: ignore[union-attr]
            value = getattr(item, attribute, None)
            if not isinstance(value, str) or not value.strip():
                raise AssistedError(
                    f"Assisted returned an invalid {attribute} collection"
                )
            values.append(value.strip())
        return tuple(values)
    except TypeError as exc:
        raise AssistedError(
            f"Assisted returned an invalid {attribute} collection"
        ) from exc


def _ntp_sources(raw: object) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, str):
        raise AssistedError("Assisted returned invalid NTP sources")
    return tuple(source.strip() for source in raw.split(",") if source.strip())


def cluster_to_snapshot(cluster: Any) -> AssistedClusterSnapshot:
    """Normalize one generated cluster model for core decisions."""
    control_plane_count = getattr(cluster, "control_plane_count", None)
    managed_networking = getattr(cluster, "user_managed_networking", None)
    if not isinstance(control_plane_count, int) or isinstance(
        control_plane_count, bool
    ):
        raise AssistedError("Assisted returned an invalid cluster control-plane count")
    if not isinstance(managed_networking, bool):
        raise AssistedError("Assisted returned an invalid cluster networking mode")

    return AssistedClusterSnapshot(
        id=_required_uuid(getattr(cluster, "id", None), label="cluster"),
        name=_required_text(getattr(cluster, "name", None), label="name"),
        status=_enum_or_unknown(
            ClusterStatus,
            getattr(cluster, "status", None),
        ),
        status_info=str(getattr(cluster, "status_info", None) or ""),
        ocp_version=_required_text(
            getattr(cluster, "openshift_version", None),
            label="OpenShift version",
        ),
        base_dns_domain=_required_text(
            getattr(cluster, "base_dns_domain", None),
            label="base DNS domain",
        ),
        architecture=_enum_or_unknown(
            CpuArchitecture,
            getattr(cluster, "cpu_architecture", None),
        ),
        ntp_sources=_ntp_sources(getattr(cluster, "ntp_sources", None)),
        high_availability_mode=_required_text(
            getattr(cluster, "high_availability_mode", None),
            label="high-availability mode",
        ),
        control_plane_count=control_plane_count,
        user_managed_networking=managed_networking,
        machine_networks=_ordered_values(
            getattr(cluster, "machine_networks", None),
            "cidr",
        ),
        api_vips=_ordered_values(getattr(cluster, "api_vips", None), "ip"),
        ingress_vips=_ordered_values(
            getattr(cluster, "ingress_vips", None),
            "ip",
        ),
        install_started=getattr(cluster, "install_started_at", None) is not None,
        install_completed=(
            getattr(cluster, "install_completed_at", None) is not None
        ),
    )


def cluster_create_params(
    intent: AssistedClusterIntent,
    *,
    pull_secret: str,
    ssh_public_key: str,
) -> models.ClusterCreateParams:
    """Translate validated intent into the generated create model."""
    if intent.architecture is CpuArchitecture.UNKNOWN:
        raise AssistedError("Cannot create a cluster with an unknown architecture")
    if not pull_secret:
        raise AssistedError("A pull secret is required to create a cluster")
    if not ssh_public_key:
        raise AssistedError("An SSH public key is required to create a cluster")

    return models.ClusterCreateParams(
        name=intent.name,
        openshift_version=intent.ocp_version,
        base_dns_domain=intent.base_dns_domain,
        cpu_architecture=intent.architecture.value,
        ntp_sources=",".join(intent.ntp_sources),
        high_availability_mode="Full" if intent.high_availability else "None",
        control_plane_count=intent.control_plane_count,
        user_managed_networking=intent.user_managed_networking,
        machine_networks=[
            models.MachineNetwork(cidr=cidr) for cidr in intent.machine_networks
        ],
        api_vips=[models.ApiVip(ip=ip) for ip in intent.api_vips],
        ingress_vips=[models.IngressVip(ip=ip) for ip in intent.ingress_vips],
        pull_secret=pull_secret,
        ssh_public_key=ssh_public_key,
        vip_dhcp_allocation=False,
    )


def infraenv_to_snapshot(
    infraenv: Any,
    *,
    iso_available: bool,
) -> AssistedInfraEnvSnapshot:
    """Normalize one generated InfraEnv model for core decisions."""
    pull_secret_set = getattr(infraenv, "pull_secret_set", None)
    if not isinstance(pull_secret_set, bool):
        raise AssistedError("Assisted returned an invalid InfraEnv pull-secret state")

    return AssistedInfraEnvSnapshot(
        id=_required_uuid(getattr(infraenv, "id", None), label="InfraEnv"),
        name=_required_text(
            getattr(infraenv, "name", None),
            label="name",
            resource="InfraEnv",
        ),
        cluster_id=_required_uuid(
            getattr(infraenv, "cluster_id", None),
            label="InfraEnv cluster",
        ),
        ocp_version=_required_text(
            getattr(infraenv, "openshift_version", None),
            label="OpenShift version",
            resource="InfraEnv",
        ),
        architecture=_enum_or_unknown(
            CpuArchitecture,
            getattr(infraenv, "cpu_architecture", None),
        ),
        image_type=_enum_or_unknown(
            InfraEnvImageType,
            getattr(infraenv, "type", None),
        ),
        ntp_sources=_ntp_sources(getattr(infraenv, "ntp_sources", None)),
        ssh_authorized_key=_text_or_empty(
            getattr(infraenv, "ssh_authorized_key", None),
            label="InfraEnv SSH authorized key",
        ),
        pull_secret_set=pull_secret_set,
        iso_available=iso_available,
    )


def infraenv_create_params(
    intent: AssistedInfraEnvIntent,
    *,
    pull_secret: str,
) -> models.InfraEnvCreateParams:
    """Translate validated InfraEnv intent into the generated create model."""
    if intent.architecture is CpuArchitecture.UNKNOWN:
        raise AssistedError("Cannot create an InfraEnv with an unknown architecture")
    if intent.image_type is InfraEnvImageType.UNKNOWN:
        raise AssistedError("Cannot create an InfraEnv with an unknown image type")
    if not pull_secret:
        raise AssistedError("A pull secret is required to create an InfraEnv")
    if not intent.ssh_authorized_key:
        raise AssistedError("An SSH authorized key is required to create an InfraEnv")

    return models.InfraEnvCreateParams(
        name=intent.name,
        cluster_id=str(intent.cluster_id),
        openshift_version=intent.ocp_version,
        cpu_architecture=intent.architecture.value,
        image_type=intent.image_type.value,
        ntp_sources=",".join(intent.ntp_sources),
        ssh_authorized_key=intent.ssh_authorized_key,
        pull_secret=pull_secret,
    )


def _inventory_values(raw: object) -> tuple[str | None, tuple[IPv4Address, ...]]:
    if raw is None or raw == "":
        return None, ()
    try:
        inventory = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(inventory, dict):
            raise TypeError

        hostname_value = inventory.get("hostname")
        hostname = (
            hostname_value.strip()
            if isinstance(hostname_value, str) and hostname_value.strip()
            else None
        )
        addresses: list[IPv4Address] = []
        seen: set[IPv4Address] = set()
        interfaces = inventory.get("interfaces") or []
        if not isinstance(interfaces, list):
            raise TypeError
        for interface in interfaces:
            if not isinstance(interface, dict):
                raise TypeError
            raw_addresses = interface.get("ipv4_addresses") or []
            if not isinstance(raw_addresses, list):
                raise TypeError
            for raw_address in raw_addresses:
                parsed = ip_interface(str(raw_address)).ip
                if not isinstance(parsed, IPv4Address):
                    raise ValueError
                if parsed not in seen:
                    addresses.append(parsed)
                    seen.add(parsed)
        return hostname, tuple(addresses)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise AssistedError("Assisted returned malformed host inventory") from exc


def host_to_snapshot(host: Any) -> AssistedHostSnapshot:
    """Normalize one generated host model for polling and orchestration."""
    inventory_hostname, addresses = _inventory_values(
        getattr(host, "inventory", None)
    )
    progress = getattr(host, "progress", None)
    role_value = getattr(host, "role", None)

    return AssistedHostSnapshot(
        id=_required_uuid(getattr(host, "id", None), label="host"),
        requested_hostname=_optional_text(
            getattr(host, "requested_hostname", None)
        ),
        inventory_hostname=inventory_hostname,
        status=_enum_or_unknown(HostStatus, getattr(host, "status", None)),
        status_info=str(getattr(host, "status_info", None) or ""),
        role=_optional_text(role_value),
        ipv4_addresses=addresses,
        install_stage=_enum_or_unknown(
            InstallStage,
            getattr(progress, "current_stage", None),
        ),
        progress_info=str(getattr(progress, "progress_info", None) or ""),
    )
