import hashlib
from dataclasses import replace
from ipaddress import IPv4Address
from pathlib import Path
from uuid import UUID

import pytest

from ocp_dsx_air.core.contracts import (
    AirImageUploadStatus,
    ClusterStatus,
    DeployNodeIntent,
    HostStatus,
    LocalImageArtifact,
    OpenShiftNodeRole,
)
from ocp_dsx_air.core.exceptions import AirImageError, AirSimError, ClusterInstallError
from ocp_dsx_air.core.iso import air_discovery_image_name
from ocp_dsx_air.core.workflows import (
    _reconcile_air_image,
    _reconcile_cluster,
    _reconcile_hosts,
    _reconcile_infraenv,
    _reconcile_installation,
    _reconcile_simulation,
    _teardown_managed_stack,
    deploy_lab,
)
from ocp_dsx_air.models.resolution import resolve_deploy_intent
from ocp_dsx_air.models.runtime import ResolvedCredentials
from ocp_dsx_air.models.spec import LabSpec

from ..support.deployment import (
    FakeAir,
    FakeAssistedInstaller,
    FakeClock,
    RecordingReporter,
    air_image_intent,
    air_image_snapshot,
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
    result, path = _reconcile_infraenv(
        intent,
        assisted=assisted,
        reporter=RecordingReporter(),
        clock=FakeClock(),
        pull_secret="pull-secret",
        cache_root=tmp_path,
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


def test_air_image_wait_times_out_without_mutation(tmp_path: Path) -> None:
    source = tmp_path / "image.iso"
    source.write_bytes(b"image bytes")
    air = FakeAir()
    image = replace(
        air_image_snapshot(),
        upload_status=AirImageUploadStatus.UPLOADING,
        size_bytes=0,
        sha256="",
    )
    air.images[image.id] = image

    with pytest.raises(AirImageError, match="Timed out"):
        _reconcile_air_image(
            air_image_intent(),
            source=source,
            air=air,
            reporter=RecordingReporter(),
            clock=FakeClock(),
            replace=False,
            timeout_seconds=2,
            poll_interval_seconds=1,
        )

    assert "upload_image" not in [call.operation for call in air.calls]


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
    assisted.clusters[cluster.id] = cluster
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
    assisted.clusters[cluster.id] = cluster
    assisted.hosts[cluster.id] = (
        replace(assisted_host_snapshot(), requested_hostname="surprise"),
    )

    with pytest.raises(ClusterInstallError, match="Unexpected discovered host"):
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


def test_host_reconciliation_refuses_ambiguous_matches() -> None:
    assisted = FakeAssistedInstaller()
    cluster = assisted_cluster_snapshot()
    assisted.clusters[cluster.id] = cluster
    first = replace(assisted_host_snapshot(), requested_hostname="cp-0")
    second = replace(
        first,
        id=UUID(int=92),
        requested_hostname="cp-0",
    )
    assisted.hosts[cluster.id] = (first, second)

    with pytest.raises(ClusterInstallError, match="Multiple Assisted hosts"):
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


def test_host_reconciliation_waits_for_missing_hosts_until_timeout() -> None:
    assisted = FakeAssistedInstaller()
    cluster = assisted_cluster_snapshot()
    assisted.clusters[cluster.id] = cluster
    with pytest.raises(ClusterInstallError, match="Timed out"):
        _reconcile_hosts(
            cluster,
            (_deploy_node("cp-0", OpenShiftNodeRole.MASTER),),
            assisted=assisted,
            reporter=RecordingReporter(),
            clock=FakeClock(),
            timeout_seconds=2,
            normal_poll_seconds=1,
            fast_poll_seconds=1,
        )


def test_host_reconciliation_reports_insufficient_until_timeout() -> None:
    assisted = FakeAssistedInstaller()
    cluster = assisted_cluster_snapshot()
    assisted.clusters[cluster.id] = cluster
    assisted.hosts[cluster.id] = (
        replace(
            assisted_host_snapshot(),
            requested_hostname="cp-0",
            status=HostStatus.INSUFFICIENT,
            status_info="NTP not synchronized",
        ),
    )
    reporter = RecordingReporter()

    with pytest.raises(ClusterInstallError, match="Timed out"):
        _reconcile_hosts(
            cluster,
            (_deploy_node("cp-0", OpenShiftNodeRole.MASTER),),
            assisted=assisted,
            reporter=reporter,
            clock=FakeClock(),
            timeout_seconds=2,
            normal_poll_seconds=1,
            fast_poll_seconds=1,
        )

    assert reporter.events[0].action == "host-insufficient"


def test_host_reconciliation_stops_on_terminal_host() -> None:
    assisted = FakeAssistedInstaller()
    cluster = assisted_cluster_snapshot()
    assisted.clusters[cluster.id] = cluster
    assisted.hosts[cluster.id] = (
        replace(
            assisted_host_snapshot(),
            requested_hostname="cp-0",
            status=HostStatus.ERROR,
            status_info="discovery failed",
        ),
    )

    with pytest.raises(ClusterInstallError, match="discovery failed"):
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
    tmp_path: Path,
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


def test_fake_backed_fresh_deploy_noop_rerun_and_full_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    intent = resolve_deploy_intent(
        LabSpec.model_validate(
            {
                "simulation": {"name": "dsx-lab"},
                "cluster": {
                    "name": "ocp",
                    "version": "4.19",
                    "control_plane": {"count": 1},
                },
            }
        ),
        cache_root=tmp_path,
    )
    blank_path = tmp_path / "blank.qcow2"
    blank_bytes = b"fake blank disk"
    blank_path.write_bytes(blank_bytes)
    blank = LocalImageArtifact(
        path=blank_path,
        size_bytes=len(blank_bytes),
        sha256=hashlib.sha256(blank_bytes).hexdigest(),
    )
    monkeypatch.setattr(
        "ocp_dsx_air.core.workflows.ensure_blank_disk",
        lambda cache_root, blank_intent: blank,
    )
    assisted = FakeAssistedInstaller()
    assisted.auto_progress_infraenv = True
    assisted.auto_ready_cluster = True
    assisted.auto_complete_installation = True
    assisted.hosts[FakeAssistedInstaller.CLUSTER_IDS[0]] = (
        replace(
            assisted_host_snapshot(),
            requested_hostname="ocp-control-plane-0",
            inventory_hostname="ocp-control-plane-0",
            role=None,
            ipv4_addresses=(IPv4Address("192.168.200.20"),),
        ),
    )
    air = FakeAir()
    reporter = RecordingReporter()
    credentials = ResolvedCredentials(
        air_api_key="air-key",
        ai_offline_token="offline-token",
        pull_secret="pull-secret",
        ssh_public_key="ssh-ed25519 key",
        jump_host_password="new-password",
    )

    result = deploy_lab(
        intent,
        credentials=credentials,
        assisted=assisted,
        air=air,
        jump_host=air,
        reporter=reporter,
        clock=FakeClock(),
    )

    assert result.cluster.install_completed is True
    assert result.simulation.status.value == "ACTIVE"
    assert result.credentials.kubeconfig.is_file()
    assert any(event.message == "Jump host is ready" for event in reporter.events)

    assisted.calls.clear()
    air.calls.clear()
    rerun = deploy_lab(
        intent,
        credentials=credentials,
        assisted=assisted,
        air=air,
        jump_host=air,
        reporter=RecordingReporter(),
        clock=FakeClock(),
    )

    assert rerun.cluster.id == result.cluster.id
    assert "create_cluster" not in [call.operation for call in assisted.calls]
    assert "import_simulation" not in [call.operation for call in air.calls]

    assisted.hosts[FakeAssistedInstaller.CLUSTER_IDS[1]] = (
        replace(
            assisted_host_snapshot(host_id=UUID(int=93)),
            infraenv_id=FakeAssistedInstaller.INFRAENV_IDS[1],
            requested_hostname="ocp-control-plane-0",
            inventory_hostname="ocp-control-plane-0",
            role=None,
            ipv4_addresses=(IPv4Address("192.168.200.21"),),
        ),
    )
    replacement = deploy_lab(
        intent,
        credentials=credentials,
        assisted=assisted,
        air=air,
        jump_host=air,
        reporter=RecordingReporter(),
        clock=FakeClock(),
        replace=True,
    )

    assert replacement.cluster.id != result.cluster.id
    assert replacement.infraenv.id != result.infraenv.id
    assert replacement.blank_image.id == result.blank_image.id

    air.simulations.clear()
    with pytest.raises(AirSimError, match="missing after installation started"):
        deploy_lab(
            intent,
            credentials=credentials,
            assisted=assisted,
            air=air,
            jump_host=air,
            reporter=RecordingReporter(),
            clock=FakeClock(),
        )


def test_full_replacement_deletes_resources_in_dependency_order(tmp_path: Path) -> None:
    intent = resolve_deploy_intent(
        LabSpec.model_validate(
            {
                "simulation": {"name": "dsx-lab"},
                "cluster": {
                    "name": "ocp",
                    "version": "4.19",
                    "control_plane": {"count": 1},
                },
            }
        ),
        cache_root=tmp_path,
    )
    assisted = FakeAssistedInstaller()
    cluster = assisted_cluster_snapshot()
    assisted.clusters[cluster.id] = cluster
    infraenv = assisted.create_infraenv(
        assisted_infraenv_intent(cluster_id=cluster.id),
        pull_secret="pull-secret",
    )
    air = FakeAir()
    discovery = replace(
        air_image_snapshot(),
        name=air_discovery_image_name(infraenv.id),
    )
    air.images[discovery.id] = discovery
    simulation = air.import_simulation(air_simulation_intent())
    air.start_simulation(simulation.id)
    assisted.calls.clear()
    air.calls.clear()
    reporter = RecordingReporter()

    _teardown_managed_stack(
        intent,
        assisted=assisted,
        air=air,
        reporter=reporter,
        clock=FakeClock(),
    )

    destructive = [
        event.action for event in reporter.events if event.action == "delete-for-replacement"
    ]
    assert destructive == [
        "delete-for-replacement",
        "delete-for-replacement",
        "delete-for-replacement",
        "delete-for-replacement",
    ]
    assert [event.phase.value for event in reporter.events] == [
        "simulation",
        "simulation",
        "air-images",
        "infraenv",
        "cluster",
    ]
    assert assisted.clusters == {}
    assert assisted.infraenvs == {}
    assert air.simulations == {}
    assert air.images == {}


def test_replacement_preflight_refuses_unmanaged_image_before_deletion(
    tmp_path: Path,
) -> None:
    intent = resolve_deploy_intent(
        LabSpec.model_validate(
            {
                "simulation": {"name": "dsx-lab"},
                "cluster": {
                    "name": "ocp",
                    "version": "4.19",
                    "control_plane": {"count": 1},
                },
            }
        ),
        cache_root=tmp_path,
    )
    assisted = FakeAssistedInstaller()
    cluster = assisted_cluster_snapshot()
    assisted.clusters[cluster.id] = cluster
    infraenv = assisted.create_infraenv(
        assisted_infraenv_intent(cluster_id=cluster.id),
        pull_secret="pull-secret",
    )
    air = FakeAir()
    discovery = replace(
        air_image_snapshot(),
        name=air_discovery_image_name(infraenv.id),
        owned_by_client=False,
    )
    air.images[discovery.id] = discovery
    simulation = air.import_simulation(air_simulation_intent())
    air.start_simulation(simulation.id)
    air.calls.clear()

    with pytest.raises(AirImageError, match="unmanaged"):
        _teardown_managed_stack(
            intent,
            assisted=assisted,
            air=air,
            reporter=RecordingReporter(),
            clock=FakeClock(),
        )

    assert "shutdown_simulation" not in [call.operation for call in air.calls]
