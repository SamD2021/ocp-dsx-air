"""Stable local and remote identity for Assisted discovery images."""

from pathlib import Path
from uuid import UUID


def discovery_infraenv_name(cluster_name: str) -> str:
    """Return the logical InfraEnv name owned by a cluster."""
    return f"{cluster_name}-discovery"


def discovery_iso_path(cache_root: Path, infraenv_id: UUID) -> Path:
    """Return the cache path uniquely owned by an InfraEnv generation."""
    return (
        cache_root
        / "assisted"
        / "infraenvs"
        / str(infraenv_id)
        / "discovery.iso"
    )


def air_discovery_image_name(infraenv_id: UUID) -> str:
    """Return the Air image name uniquely owned by an InfraEnv generation."""
    return f"ocp-dsx-air-discovery-{infraenv_id}"


def discovery_iso_is_cached(path: Path) -> bool:
    """Return whether path is a non-empty regular file, not a symlink."""
    try:
        return not path.is_symlink() and path.is_file() and path.stat().st_size > 0
    except OSError:
        return False
