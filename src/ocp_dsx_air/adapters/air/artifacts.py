"""Build and identify local image artifacts managed for NVIDIA Air."""

import hashlib
import json
import os
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ocp_dsx_air.core.contracts import (
    BlankDiskIntent,
    CpuArchitecture,
    LocalImageArtifact,
)
from ocp_dsx_air.core.exceptions import AirImageError, DependencyError

_Run = Callable[..., subprocess.CompletedProcess[str]]


def blank_disk_path(cache_root: Path, intent: BlankDiskIntent) -> Path:
    """Return the cache path owned by a semantic blank-disk generation."""
    return (
        cache_root
        / "air"
        / "images"
        / "blank"
        / intent.architecture.value
        / f"{intent.virtual_size_gib}g"
        / f"{intent.image_format.value}-v{intent.schema_version}"
        / f"disk.{intent.image_format.value}"
    )


def blank_disk_air_image_name(intent: BlankDiskIntent, sha256: str) -> str:
    """Return an immutable Air image name for exact blank-disk bytes."""
    normalized_hash = sha256.lower()
    if len(normalized_hash) != 64:
        raise AirImageError("Blank disk SHA-256 must contain 64 hexadecimal characters")
    try:
        int(normalized_hash, 16)
    except ValueError as exc:
        raise AirImageError(
            "Blank disk SHA-256 must contain 64 hexadecimal characters"
        ) from exc
    return (
        f"ocp-dsx-air-blank-{intent.architecture.value}-"
        f"{intent.virtual_size_gib}g-{intent.image_format.value}-"
        f"v{intent.schema_version}-{normalized_hash[:12]}"
    )


def _validate_intent(intent: BlankDiskIntent) -> None:
    if intent.architecture is CpuArchitecture.UNKNOWN:
        raise AirImageError("Blank disk architecture cannot be unknown")
    if intent.virtual_size_gib <= 0:
        raise AirImageError("Blank disk virtual size must be positive")
    if intent.schema_version <= 0:
        raise AirImageError("Blank disk schema version must be positive")


def _run_qemu(
    run: _Run,
    command: list[str],
    *,
    operation: str,
) -> subprocess.CompletedProcess[str]:
    try:
        return run(command, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise DependencyError("Required executable qemu-img was not found") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise AirImageError(f"Could not {operation} with qemu-img") from exc


def _qemu_info_matches(
    path: Path,
    intent: BlankDiskIntent,
    *,
    run: _Run,
) -> bool:
    result = _run_qemu(
        run,
        ["qemu-img", "info", "--output=json", str(path)],
        operation="inspect blank disk",
    )
    try:
        info: Any = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError):
        return False
    if not isinstance(info, Mapping):
        return False
    return (
        info.get("format") == intent.image_format.value
        and info.get("virtual-size") == intent.virtual_size_gib * 1024**3
    )


def _artifact(path: Path) -> LocalImageArtifact:
    digest = hashlib.sha256()
    with path.open("rb") as image_file:
        for chunk in iter(lambda: image_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return LocalImageArtifact(
        path=path,
        size_bytes=path.stat().st_size,
        sha256=digest.hexdigest(),
    )


def inspect_local_artifact(path: Path) -> LocalImageArtifact:
    """Hash a non-empty, non-symlink local image artifact."""
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size <= 0:
            raise AirImageError("Local image artifact must be a non-empty regular file")
        return _artifact(path)
    except AirImageError:
        raise
    except OSError as exc:
        raise AirImageError("Could not inspect the local image artifact") from exc


def _blank_metadata_path(destination: Path) -> Path:
    return destination.with_name(f"{destination.name}.metadata.json")


def _cached_blank_artifact(
    destination: Path,
    intent: BlankDiskIntent,
) -> LocalImageArtifact | None:
    metadata_path = _blank_metadata_path(destination)
    try:
        if metadata_path.is_symlink() or not metadata_path.is_file():
            return None
        metadata: Any = json.loads(metadata_path.read_text())
        if not isinstance(metadata, Mapping):
            return None
        artifact = inspect_local_artifact(destination)
        if metadata != {
            "format": intent.image_format.value,
            "schema_version": intent.schema_version,
            "sha256": artifact.sha256,
            "size_bytes": artifact.size_bytes,
            "virtual_size_gib": intent.virtual_size_gib,
        }:
            return None
        metadata_path.chmod(0o600)
        return artifact
    except (AirImageError, OSError, TypeError, json.JSONDecodeError):
        return None


def _write_blank_metadata(
    destination: Path,
    intent: BlankDiskIntent,
    artifact: LocalImageArtifact,
) -> None:
    metadata_path = _blank_metadata_path(destination)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{metadata_path.name}.",
    )
    temporary_path = Path(temporary_name)
    payload = json.dumps(
        {
            "format": intent.image_format.value,
            "schema_version": intent.schema_version,
            "sha256": artifact.sha256,
            "size_bytes": artifact.size_bytes,
            "virtual_size_gib": intent.virtual_size_gib,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    try:
        with os.fdopen(descriptor, "wb") as metadata_file:
            os.fchmod(metadata_file.fileno(), 0o600)
            metadata_file.write(payload)
            metadata_file.flush()
            os.fsync(metadata_file.fileno())
        os.replace(temporary_path, metadata_path)
        directory_descriptor = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        temporary_path.unlink(missing_ok=True)


def ensure_blank_disk(
    cache_root: Path,
    intent: BlankDiskIntent,
    *,
    _run: _Run = subprocess.run,
) -> LocalImageArtifact:
    """Return a verified blank disk, building it with atomic replacement if needed."""
    _validate_intent(intent)
    destination = blank_disk_path(cache_root, intent)
    destination_dir = destination.parent
    if destination_dir.is_symlink():
        raise AirImageError("Blank disk destination directory cannot be a symlink")

    temporary_path: Path | None = None
    try:
        destination_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if destination_dir.is_symlink():
            raise AirImageError("Blank disk destination directory cannot be a symlink")
        if not destination_dir.is_dir():
            raise AirImageError("Blank disk destination must be a directory")
        destination_dir.chmod(0o700)

        cached = (
            not destination.is_symlink()
            and destination.is_file()
            and destination.stat().st_size > 0
        )
        if cached:
            cached_artifact = _cached_blank_artifact(destination, intent)
            if cached_artifact is not None:
                destination.chmod(0o600)
                return cached_artifact
            if _qemu_info_matches(destination, intent, run=_run):
                destination.chmod(0o600)
                cached_artifact = _artifact(destination)
                _write_blank_metadata(destination, intent, cached_artifact)
                return cached_artifact

        descriptor, temporary_name = tempfile.mkstemp(
            dir=destination_dir,
            prefix=f".{destination.name}.",
        )
        os.close(descriptor)
        temporary_path = Path(temporary_name)
        temporary_path.chmod(0o600)
        _run_qemu(
            _run,
            [
                "qemu-img",
                "create",
                "-f",
                intent.image_format.value,
                str(temporary_path),
                f"{intent.virtual_size_gib}G",
            ],
            operation="create blank disk",
        )
        if (
            temporary_path.is_symlink()
            or not temporary_path.is_file()
            or temporary_path.stat().st_size == 0
            or not _qemu_info_matches(temporary_path, intent, run=_run)
        ):
            raise AirImageError("qemu-img created an invalid blank disk")

        temporary_path.chmod(0o600)
        with temporary_path.open("rb") as staged_file:
            os.fsync(staged_file.fileno())
        os.replace(temporary_path, destination)
        temporary_path = None
        directory_descriptor = os.open(destination_dir, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
        artifact = _artifact(destination)
        _write_blank_metadata(destination, intent, artifact)
        return artifact
    except (AirImageError, DependencyError):
        raise
    except OSError as exc:
        raise AirImageError("Could not prepare the local blank disk") from exc
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
