from pathlib import Path
from uuid import UUID

from ocp_dsx_air.core.iso import (
    air_discovery_image_name,
    discovery_infraenv_name,
    discovery_iso_is_cached,
    discovery_iso_path,
)

INFRAENV_ID = UUID("7a0ddc45-ce1a-4d8d-ab9f-0be5fbe98d27")


def test_discovery_identity_is_derived_from_cluster_and_infraenv() -> None:
    assert discovery_infraenv_name("ocp") == "ocp-discovery"
    assert air_discovery_image_name(INFRAENV_ID) == (
        "ocp-dsx-air-discovery-7a0ddc45-ce1a-4d8d-ab9f-0be5fbe98d27"
    )
    assert discovery_iso_path(Path("/cache"), INFRAENV_ID) == Path(
        "/cache/assisted/infraenvs/7a0ddc45-ce1a-4d8d-ab9f-0be5fbe98d27/discovery.iso"
    )


def test_nonempty_regular_iso_is_cached(tmp_path: Path) -> None:
    iso_path = tmp_path / "discovery.iso"
    iso_path.write_bytes(b"iso")

    assert discovery_iso_is_cached(iso_path) is True


def test_missing_empty_directory_and_symlink_are_not_cached(tmp_path: Path) -> None:
    empty_path = tmp_path / "empty.iso"
    empty_path.touch()
    directory_path = tmp_path / "directory.iso"
    directory_path.mkdir()
    symlink_path = tmp_path / "linked.iso"
    symlink_path.symlink_to(empty_path)

    assert discovery_iso_is_cached(tmp_path / "missing.iso") is False
    assert discovery_iso_is_cached(empty_path) is False
    assert discovery_iso_is_cached(directory_path) is False
    assert discovery_iso_is_cached(symlink_path) is False
