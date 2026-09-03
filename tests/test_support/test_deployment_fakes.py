from pathlib import Path

import pytest

from ocp_dsx_air.core.contracts import (
    AirImageUploadStatus,
    AirSimulationStatus,
    ClusterStatus,
    DeploymentEvent,
    DeploymentPhase,
)
from ocp_dsx_air.core.exceptions import AirError, AssistedError
from ocp_dsx_air.core.ports.air import AirPort
from ocp_dsx_air.core.ports.assisted import AssistedInstallerPort
from ocp_dsx_air.core.ports.jump_host import JumpHostPort
from ocp_dsx_air.core.runtime import Clock, DeploymentReporter

from ..support.deployment import (
    FakeAir,
    FakeAssistedInstaller,
    FakeClock,
    RecordingReporter,
    air_image_intent,
    air_image_snapshot,
    air_simulation_intent,
    assisted_cluster_intent,
    assisted_cluster_snapshot,
    assisted_infraenv_intent,
)


def _accept_assisted_port(port: AssistedInstallerPort) -> None:
    del port


def _accept_air_port(port: AirPort) -> None:
    del port


def _accept_clock(clock: Clock) -> None:
    del clock


def _accept_reporter(reporter: DeploymentReporter) -> None:
    del reporter


def _accept_jump_host(port: JumpHostPort) -> None:
    del port


def test_test_doubles_conform_to_orchestration_protocols() -> None:
    _accept_assisted_port(FakeAssistedInstaller())
    _accept_air_port(FakeAir())
    _accept_jump_host(FakeAir())
    _accept_clock(FakeClock())
    _accept_reporter(RecordingReporter())


def test_fake_clock_advances_without_sleeping() -> None:
    clock = FakeClock(current=10.0)

    clock.sleep(2.5)
    clock.sleep(0)

    assert clock.monotonic() == 12.5
    assert clock.sleeps == [2.5, 0]


def test_recording_reporter_preserves_event_order() -> None:
    reporter = RecordingReporter()
    first = DeploymentEvent(DeploymentPhase.CLUSTER, "Creating cluster")
    second = DeploymentEvent(DeploymentPhase.INFRAENV, "Creating InfraEnv")

    reporter.emit(first)
    reporter.emit(second)

    assert reporter.events == [first, second]


def test_fake_assisted_mutations_are_observable(tmp_path: Path) -> None:
    assisted = FakeAssistedInstaller()
    cluster_intent = assisted_cluster_intent()

    created = assisted.create_cluster(
        cluster_intent,
        pull_secret="pull-secret",
        ssh_public_key="ssh-ed25519 public-key",
    )
    assert assisted.find_cluster(cluster_intent.name) == created

    infraenv_intent = assisted_infraenv_intent(cluster_id=created.id)
    infraenv = assisted.create_infraenv(
        infraenv_intent,
        pull_secret="pull-secret",
    )
    assert assisted.find_infraenv(infraenv_intent.name) == infraenv

    iso = assisted.download_discovery_iso(
        infraenv.id,
        tmp_path / "discovery.iso",
    )
    assert iso.read_bytes() == b"fake assisted discovery iso\n"

    assisted.start_installation(created.id)
    installing = assisted.find_cluster(cluster_intent.name)
    assert installing is not None
    assert installing.status is ClusterStatus.INSTALLING
    assert installing.install_started is True

    credentials = assisted.download_credentials(created.id, tmp_path / "auth")
    assert credentials.kubeconfig.read_bytes() == b"fake kubeconfig\n"
    assert credentials.kubeadmin_password.read_bytes() == b"fake password\n"

    assisted.delete_infraenv(infraenv.id)
    assisted.delete_cluster(created.id)
    assert assisted.find_infraenv(infraenv_intent.name) is None
    assert assisted.find_cluster(cluster_intent.name) is None


def test_fake_assisted_supports_duplicates_and_failure_injection() -> None:
    assisted = FakeAssistedInstaller()
    first = assisted_cluster_snapshot()
    second = assisted_cluster_snapshot(cluster_id=FakeAssistedInstaller.CLUSTER_IDS[1])
    assisted.clusters[first.id] = first
    assisted.clusters[second.id] = second

    with pytest.raises(AssistedError, match="multiple"):
        assisted.find_cluster(first.name)

    assisted.clusters.clear()
    assisted.failures["find_cluster"] = AssistedError("injected lookup failure")
    with pytest.raises(AssistedError, match="injected"):
        assisted.find_cluster(first.name)


def test_fake_air_image_mutations_are_observable(tmp_path: Path) -> None:
    air = FakeAir()
    intent = air_image_intent()
    source = tmp_path / "image.iso"
    source.write_bytes(b"image bytes")

    created = air.create_image(intent)
    assert created.upload_status is AirImageUploadStatus.READY
    assert air.find_image(intent.name) == created

    uploaded = air.upload_image(created.id, source)
    assert uploaded.upload_status is AirImageUploadStatus.COMPLETE
    assert uploaded.size_bytes == len(b"image bytes")
    assert air.find_image(intent.name) == uploaded

    air.delete_image(created.id)
    assert air.find_image(intent.name) is None
    assert [call.operation for call in air.calls] == [
        "create_image",
        "find_image",
        "upload_image",
        "find_image",
        "delete_image",
        "find_image",
    ]


def test_fake_air_simulation_transitions_are_observable() -> None:
    air = FakeAir()
    intent = air_simulation_intent()

    created = air.import_simulation(intent)
    assert created.status is AirSimulationStatus.INACTIVE

    air.start_simulation(created.id)
    active = air.find_simulation(intent.name)
    assert active is not None
    assert active.status is AirSimulationStatus.ACTIVE

    air.shutdown_simulation(created.id, create_checkpoint=True)
    stopped = air.find_simulation(intent.name)
    assert stopped is not None
    assert stopped.status is AirSimulationStatus.INACTIVE
    assert stopped.complete_checkpoint_count == 1

    air.delete_simulation(created.id)
    assert air.find_simulation(intent.name) is None


def test_fake_air_supports_failure_injection() -> None:
    air = FakeAir()
    air.failures["find_image"] = AirError("injected image failure")

    with pytest.raises(AirError, match="injected"):
        air.find_image("image")


def test_fake_air_can_represent_duplicate_exact_names() -> None:
    air = FakeAir()
    first = air_image_snapshot()
    second = air_image_snapshot(image_id=FakeAir.IMAGE_IDS[1])
    air.images[first.id] = first
    air.images[second.id] = second

    with pytest.raises(AirError, match="multiple"):
        air.find_image(first.name)
