from dataclasses import replace

import pytest

from ocp_dsx_air.core.exceptions import AirImageError
from ocp_dsx_air.core.workflows import (
    _reconcile_air_image,
    _reconcile_cluster,
    _reconcile_infraenv,
    _reconcile_simulation,
)

from ..support.deployment import (
    FakeAir,
    FakeAssistedInstaller,
    FakeClock,
    RecordingReporter,
    air_image_intent,
    air_simulation_intent,
    assisted_cluster_intent,
    assisted_cluster_snapshot,
    assisted_infraenv_intent,
)


def test_cluster_reconciliation_creates_and_observes() -> None:
    assisted = FakeAssistedInstaller()
    clock = FakeClock()

    result = _reconcile_cluster(
        assisted_cluster_intent(),
        assisted=assisted,
        reporter=RecordingReporter(),
        clock=clock,
        pull_secret="pull-secret",
        ssh_public_key="ssh-ed25519 key",
        replace=False,
        timeout_seconds=30,
        poll_interval_seconds=5,
    )

    assert result.id in assisted.clusters
    assert [call.operation for call in assisted.calls] == [
        "find_cluster",
        "create_cluster",
        "find_cluster",
    ]
    assert clock.sleeps == [5]


def test_cluster_replacement_is_consumed_after_deletion() -> None:
    assisted = FakeAssistedInstaller()
    original = assisted_cluster_snapshot()
    assisted.clusters[original.id] = original
    assisted._next_cluster = 1

    result = _reconcile_cluster(
        assisted_cluster_intent(),
        assisted=assisted,
        reporter=RecordingReporter(),
        clock=FakeClock(),
        pull_secret="pull-secret",
        ssh_public_key="ssh-ed25519 key",
        replace=True,
        timeout_seconds=30,
        poll_interval_seconds=1,
    )

    assert result.id != original.id
    assert [call.operation for call in assisted.calls].count("delete_cluster") == 1


def test_infraenv_reconciliation_downloads_available_iso(tmp_path) -> None:
    assisted = FakeAssistedInstaller()
    intent = assisted_infraenv_intent(cluster_id=assisted_cluster_snapshot().id)
    snapshot = assisted.create_infraenv(intent, pull_secret="pull-secret")
    assisted.infraenvs[snapshot.id] = replace(snapshot, iso_available=True)
    assisted.calls.clear()
    iso_path = tmp_path / "discovery.iso"

    result, path = _reconcile_infraenv(
        intent,
        assisted=assisted,
        reporter=RecordingReporter(),
        clock=FakeClock(),
        pull_secret="pull-secret",
        iso_path=iso_path,
        replace=False,
        timeout_seconds=30,
        poll_interval_seconds=1,
    )

    assert result.id == snapshot.id
    assert path.read_bytes() == b"fake assisted discovery iso\n"


def test_air_image_reconciliation_uploads_content(tmp_path) -> None:
    source = tmp_path / "image.iso"
    source.write_bytes(b"image bytes")
    air = FakeAir()

    result = _reconcile_air_image(
        air_image_intent(),
        source=source,
        air=air,
        reporter=RecordingReporter(),
        clock=FakeClock(),
        replace=False,
        timeout_seconds=30,
        poll_interval_seconds=1,
    )

    assert result.sha256 == air_image_intent().source_sha256
    assert [call.operation for call in air.calls] == [
        "find_image",
        "create_image",
        "find_image",
        "upload_image",
        "find_image",
    ]


def test_air_image_reconciliation_refuses_unmanaged_replacement(tmp_path) -> None:
    source = tmp_path / "image.iso"
    source.write_bytes(b"image bytes")
    air = FakeAir()
    image = air.create_image(air_image_intent())
    air.images[image.id] = replace(image, owned_by_client=False)

    with pytest.raises(AirImageError, match="unmanaged"):
        _reconcile_air_image(
            air_image_intent(),
            source=source,
            air=air,
            reporter=RecordingReporter(),
            clock=FakeClock(),
            replace=True,
            timeout_seconds=30,
            poll_interval_seconds=1,
        )


def test_simulation_reconciliation_imports_and_starts() -> None:
    air = FakeAir()

    result = _reconcile_simulation(
        air_simulation_intent(),
        air=air,
        reporter=RecordingReporter(),
        clock=FakeClock(),
        replace=False,
        timeout_seconds=30,
        poll_interval_seconds=1,
    )

    assert result.status.value == "ACTIVE"
    assert [call.operation for call in air.calls] == [
        "find_simulation",
        "import_simulation",
        "find_simulation",
        "start_simulation",
        "find_simulation",
    ]
