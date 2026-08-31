from dataclasses import replace
from uuid import UUID

import pytest

from ocp_dsx_air.core.contracts import (
    AssistedClusterIntent,
    AssistedClusterSnapshot,
    ClusterAction,
    ClusterStatus,
    CpuArchitecture,
)
from ocp_dsx_air.core.decisions import decide_cluster_action


def _intent() -> AssistedClusterIntent:
    return AssistedClusterIntent(
        name="ocp",
        ocp_version="4.19",
        base_dns_domain="dsx.air.local",
        architecture=CpuArchitecture.X86_64,
        ntp_sources=("192.168.200.1",),
        high_availability=True,
        control_plane_count=3,
        user_managed_networking=False,
        machine_networks=("192.168.200.0/24",),
        api_vips=("192.168.200.10",),
        ingress_vips=("192.168.200.11",),
    )


def _observed(
    *,
    status: ClusterStatus = ClusterStatus.PENDING_FOR_INPUT,
    install_started: bool = False,
    install_completed: bool = False,
) -> AssistedClusterSnapshot:
    return AssistedClusterSnapshot(
        id=UUID("5ad7357e-6c65-46e2-bad8-cd796cc82070"),
        name="ocp",
        status=status,
        status_info="",
        ocp_version="4.19",
        base_dns_domain="dsx.air.local",
        architecture=CpuArchitecture.X86_64,
        ntp_sources=("192.168.200.1",),
        high_availability_mode="Full",
        control_plane_count=3,
        user_managed_networking=False,
        machine_networks=("192.168.200.0/24",),
        api_vips=("192.168.200.10",),
        ingress_vips=("192.168.200.11",),
        install_started=install_started,
        install_completed=install_completed,
    )


def _assert_action(decision, expected: ClusterAction) -> None:
    assert decision.action is expected
    assert decision.reason.strip()


@pytest.mark.parametrize("replace", [False, True])
def test_absent_cluster_is_created(replace: bool) -> None:
    decision = decide_cluster_action(_intent(), None, replace=replace)

    _assert_action(decision, ClusterAction.CREATE)
    assert decision.drift == ()


@pytest.mark.parametrize("status", list(ClusterStatus))
def test_replace_authorization_replaces_any_existing_cluster(status: ClusterStatus) -> None:
    decision = decide_cluster_action(_intent(), _observed(status=status), replace=True)

    _assert_action(decision, ClusterAction.REPLACE)


@pytest.mark.parametrize(
    ("status", "install_started", "install_completed", "expected"),
    [
        (ClusterStatus.INSUFFICIENT, False, False, ClusterAction.WAIT_FOR_HOSTS),
        (ClusterStatus.PENDING_FOR_INPUT, False, False, ClusterAction.WAIT_FOR_HOSTS),
        (ClusterStatus.READY, False, False, ClusterAction.START_INSTALL),
        (
            ClusterStatus.PREPARING_FOR_INSTALLATION,
            True,
            False,
            ClusterAction.WAIT_FOR_INSTALL,
        ),
        (ClusterStatus.INSTALLING, True, False, ClusterAction.WAIT_FOR_INSTALL),
        (ClusterStatus.FINALIZING, True, False, ClusterAction.WAIT_FOR_INSTALL),
        (ClusterStatus.INSTALLED, True, True, ClusterAction.DOWNLOAD_CREDENTIALS),
        # Status alone is sufficient because Assisted can report installed before
        # its completion timestamp has propagated to a subsequent observation.
        (ClusterStatus.INSTALLED, True, False, ClusterAction.DOWNLOAD_CREDENTIALS),
    ],
)
def test_compatible_cluster_lifecycle_selects_next_action(
    status: ClusterStatus,
    install_started: bool,
    install_completed: bool,
    expected: ClusterAction,
) -> None:
    decision = decide_cluster_action(
        _intent(),
        _observed(
            status=status,
            install_started=install_started,
            install_completed=install_completed,
        ),
        replace=False,
    )

    _assert_action(decision, expected)
    assert decision.drift == ()


@pytest.mark.parametrize(
    "status",
    [ClusterStatus.ADDING_HOSTS, ClusterStatus.UNMONITORED],
)
def test_known_but_unsupported_cluster_status_is_refused(
    status: ClusterStatus,
) -> None:
    decision = decide_cluster_action(_intent(), _observed(status=status), replace=False)

    _assert_action(decision, ClusterAction.REFUSE_UNSUPPORTED)
    assert decision.drift == ()


def test_completion_marker_wins_over_stale_terminal_status() -> None:
    observed = _observed(
        status=ClusterStatus.ERROR,
        install_started=True,
        install_completed=True,
    )

    decision = decide_cluster_action(_intent(), observed, replace=False)

    _assert_action(decision, ClusterAction.DOWNLOAD_CREDENTIALS)


def test_install_started_marker_prevents_duplicate_start() -> None:
    observed = _observed(
        status=ClusterStatus.READY,
        install_started=True,
        install_completed=False,
    )

    decision = decide_cluster_action(_intent(), observed, replace=False)

    _assert_action(decision, ClusterAction.WAIT_FOR_INSTALL)


@pytest.mark.parametrize(
    "status",
    [
        ClusterStatus.ERROR,
        ClusterStatus.CANCELLED,
        ClusterStatus.INSTALLING_PENDING_USER_ACTION,
    ],
)
def test_terminal_cluster_is_refused(status: ClusterStatus) -> None:
    decision = decide_cluster_action(_intent(), _observed(status=status), replace=False)

    _assert_action(decision, ClusterAction.REFUSE_TERMINAL)
    assert decision.drift == ()


def test_unknown_cluster_status_is_refused() -> None:
    decision = decide_cluster_action(
        _intent(),
        _observed(status=ClusterStatus.UNKNOWN),
        replace=False,
    )

    _assert_action(decision, ClusterAction.REFUSE_UNKNOWN)
    assert decision.drift == ()


@pytest.mark.parametrize(
    ("change", "expected_field"),
    [
        ({"ocp_version": "4.20"}, "ocp_version"),
        ({"base_dns_domain": "example.com"}, "base_dns_domain"),
        ({"architecture": CpuArchitecture.ARM64}, "architecture"),
        ({"ntp_sources": ("192.168.200.2",)}, "ntp_sources"),
        ({"high_availability_mode": "None"}, "high_availability"),
        ({"control_plane_count": 1}, "control_plane_count"),
        ({"user_managed_networking": True}, "user_managed_networking"),
        ({"machine_networks": ["10.0.0.0/24"]}, "machine_networks"),
        ({"api_vips": ["192.168.200.20"]}, "api_vips"),
        ({"ingress_vips": ["192.168.200.21"]}, "ingress_vips"),
    ],
)
def test_material_drift_is_refused(change: dict[str, object], expected_field: str) -> None:
    observed = replace(_observed(status=ClusterStatus.INSTALLED), **change)

    decision = decide_cluster_action(_intent(), observed, replace=False)

    _assert_action(decision, ClusterAction.REFUSE_DRIFT)
    assert expected_field in decision.drift


def test_decision_reports_all_material_drift() -> None:
    observed = replace(
        _observed(status=ClusterStatus.READY),
        ocp_version="4.20",
        control_plane_count=1,
        machine_networks=["10.0.0.0/24"],
    )

    decision = decide_cluster_action(_intent(), observed, replace=False)

    _assert_action(decision, ClusterAction.REFUSE_DRIFT)
    assert set(decision.drift) == {
        "ocp_version",
        "control_plane_count",
        "machine_networks",
    }
