"""Normalize NVIDIA Air SDK models into stable domain contracts."""

from typing import Any
from uuid import UUID

from ocp_dsx_air.core.contracts import (
    AirImageIntent,
    AirImageSnapshot,
    AirImageUploadStatus,
    CpuArchitecture,
)
from ocp_dsx_air.core.exceptions import AirImageError

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
