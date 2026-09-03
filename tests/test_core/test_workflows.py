from dataclasses import replace
from uuid import UUID

import pytest

from ocp_dsx_air.core.contracts import (
    ClusterStatus,
    DeployNodeIntent,
    HostStatus,
    OpenShiftNodeRole,
)
from ocp_dsx_air.core.exceptions import AirImageError
from ocp_dsx_air.core.workflows import (
    _reconcile_air_image,
    _reconcile_cluster,
    _reconcile_hosts,
    _reconcile_infraenv,
    _reconcile_installation,
    _reconcile_simulation,
)

from ..support.deployment import (
    FakeAir,
    FakeAssistedInstaller,
    FakeClock,
    RecordingReporter,
    air_image_intent,
    air_node_intent,
    air_simulation_intent,
    assisted_cluster_intent,
    assisted_cluster_snapshot,
    assisted_host_snapshot,
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


def _deploy_node(name: str, role: OpenShiftNodeRole) -> DeployNodeIntent:
    air_node = air_node_intent()
    return DeployNodeIntent(
        name=name,
        role=role,
        cpu=air_node.cpu,
        memory_mib=air_node.memory_mib,
        storage_gib=air_node.storage_gib,
        hardware=air_node.hardware,
    )


def test_host_reconciliation_matches_names_and_assigns_one_role_per_observation() -> None:
    assisted = FakeAssistedInstaller()
    cluster = assisted_cluster_snapshot()
    first = replace(
        assisted_host_snapshot(),
        requested_hostname="cp-0",
        inventory_hostname="cp-0",
        role=None,
    )
    second = replace(
        assisted_host_snapshot(host_id=UUID(int=91)),
        requested_hostname=None,
        inventory_hostname="worker-0",
        role=OpenShiftNodeRole.WORKER,
    )
    assisted.hosts[cluster.id] = (second, first)

    result = _reconcile_hosts(
        cluster,
        (
            _deploy_node("cp-0", OpenShiftNodeRole.MASTER),
            _deploy_node("worker-0", OpenShiftNodeRole.WORKER),
        ),
        assisted=assisted,
        reporter=RecordingReporter(),
        clock=FakeClock(),
        timeout_seconds=30,
        normal_poll_seconds=5,
        fast_poll_seconds=2,
    )

    assert [host.requested_hostname or host.inventory_hostname for host in result] == [
        "cp-0",
        "worker-0",
    ]
    assert [call.operation for call in assisted.calls].count("update_host_role") == 1


def test_host_reconciliation_refuses_unexpected_named_host() -> None:
    assisted = FakeAssistedInstaller()
    cluster = assisted_cluster_snapshot()
    assisted.hosts[cluster.id] = (
        replace(assisted_host_snapshot(), requested_hostname="surprise"),
    )

    with pytest.raises(Exception, match="Unexpected discovered host"):
        _reconcile_hosts(
            cluster,
            (_deploy_node("cp-0", OpenShiftNodeRole.MASTER),),
            assisted=assisted,
            reporter=RecordingReporter(),
            clock=FakeClock(),
            timeout_seconds=30,
            normal_poll_seconds=5,
            fast_poll_seconds=2,
        )


class CompletingAssisted(FakeAssistedInstaller):
    def find_cluster(self, name: str):  # type: ignore[no-untyped-def]
        cluster = super().find_cluster(name)
        if cluster is not None and cluster.install_started:
            cluster = replace(
                cluster,
                status=ClusterStatus.INSTALLED,
                install_completed=True,
            )
            self.clusters[cluster.id] = cluster
        return cluster


def test_installation_reconciliation_starts_once_and_downloads_credentials(
    tmp_path,
) -> None:
    assisted = CompletingAssisted()
    cluster = replace(assisted_cluster_snapshot(), status=ClusterStatus.READY)
    assisted.clusters[cluster.id] = cluster
    assisted.hosts[cluster.id] = (
        replace(assisted_host_snapshot(), status=HostStatus.READY),
    )

    final, paths = _reconcile_installation(
        assisted_cluster_intent(),
        assisted=assisted,
        reporter=RecordingReporter(),
        clock=FakeClock(),
        credentials_dir=tmp_path / "auth",
        timeout_seconds=30,
        normal_poll_seconds=5,
        fast_poll_seconds=2,
    )

    assert final.install_completed is True
    assert paths.kubeconfig.is_file()
    assert [call.operation for call in assisted.calls].count("start_installation") == 1
