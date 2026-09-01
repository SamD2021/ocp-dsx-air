from dataclasses import replace
from uuid import UUID

import pytest

from ocp_dsx_air.core.contracts import (
    AssistedClusterIntent,
    AssistedClusterSnapshot,
    AssistedInfraEnvIntent,
    AssistedInfraEnvSnapshot,
    ClusterAction,
    ClusterStatus,
    CpuArchitecture,
    InfraEnvAction,
    InfraEnvDecision,
    InfraEnvImageType,
)
from ocp_dsx_air.core.decisions import (
    decide_cluster_action,
    decide_infraenv_action,
)

CLUSTER_ID = UUID("5ad7357e-6c65-46e2-bad8-cd796cc82070")
INFRAENV_ID = UUID("7a0ddc45-ce1a-4d8d-ab9f-0be5fbe98d27")


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
        id=CLUSTER_ID,
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


def _infraenv_intent() -> AssistedInfraEnvIntent:
    return AssistedInfraEnvIntent(
        name="ocp-discovery",
        cluster_id=CLUSTER_ID,
        ocp_version="4.19",
        architecture=CpuArchitecture.X86_64,
        image_type=InfraEnvImageType.MINIMAL_ISO,
        ntp_sources=("192.168.200.1",),
        ssh_authorized_key="ssh-ed25519 public-key",
    )


def _infraenv_observed(
    *,
    iso_available: bool = True,
) -> AssistedInfraEnvSnapshot:
    return AssistedInfraEnvSnapshot(
        id=INFRAENV_ID,
        name="ocp-discovery",
        cluster_id=CLUSTER_ID,
        ocp_version="4.19",
        architecture=CpuArchitecture.X86_64,
        image_type=InfraEnvImageType.MINIMAL_ISO,
        ntp_sources=("192.168.200.1",),
        ssh_authorized_key="ssh-ed25519 public-key",
        pull_secret_set=True,
        iso_available=iso_available,
    )


def _assert_infraenv_action(
    decision: InfraEnvDecision,
    expected: InfraEnvAction,
) -> None:
    assert decision.action is expected
    assert decision.reason.strip()


@pytest.mark.parametrize("replace", [False, True])
def test_absent_infraenv_is_created(replace: bool) -> None:
    decision = decide_infraenv_action(
        _infraenv_intent(),
        None,
        replace=replace,
        iso_cached=False,
    )

    _assert_infraenv_action(decision, InfraEnvAction.CREATE)
    assert decision.drift == ()


def test_replace_authorization_precedes_unknown_drift_and_iso_state() -> None:
    observed = replace(
        _infraenv_observed(iso_available=False),
        architecture=CpuArchitecture.UNKNOWN,
        ocp_version="4.20",
    )

    decision = decide_infraenv_action(
        _infraenv_intent(),
        observed,
        replace=True,
        iso_cached=False,
    )

    _assert_infraenv_action(decision, InfraEnvAction.REPLACE)
    assert decision.drift == ()


@pytest.mark.parametrize(
    "change",
    [
        {"architecture": CpuArchitecture.UNKNOWN},
        {"image_type": InfraEnvImageType.UNKNOWN},
    ],
)
def test_unknown_infraenv_configuration_is_refused_before_drift(
    change: dict[str, object],
) -> None:
    observed = replace(_infraenv_observed(), ocp_version="4.20", **change)

    decision = decide_infraenv_action(
        _infraenv_intent(),
        observed,
        replace=False,
        iso_cached=False,
    )

    _assert_infraenv_action(decision, InfraEnvAction.REFUSE_UNKNOWN)
    assert decision.drift == ()


@pytest.mark.parametrize(
    ("change", "expected_field"),
    [
        (
            {"cluster_id": UUID("f926699c-77d9-472e-aa40-ccf70e98681b")},
            "cluster_id",
        ),
        ({"ocp_version": "4.20"}, "ocp_version"),
        ({"architecture": CpuArchitecture.ARM64}, "architecture"),
        ({"ntp_sources": ("time.google.com",)}, "ntp_sources"),
        (
            {"ssh_authorized_key": "ssh-ed25519 other-key"},
            "ssh_authorized_key",
        ),
        ({"pull_secret_set": False}, "pull_secret"),
    ],
)
def test_infraenv_material_drift_is_refused(
    change: dict[str, object],
    expected_field: str,
) -> None:
    observed = replace(_infraenv_observed(), **change)

    decision = decide_infraenv_action(
        _infraenv_intent(),
        observed,
        replace=False,
        iso_cached=True,
    )

    _assert_infraenv_action(decision, InfraEnvAction.REFUSE_DRIFT)
    assert decision.drift == (expected_field,)


def test_infraenv_decision_reports_all_material_drift() -> None:
    observed = replace(
        _infraenv_observed(),
        ocp_version="4.20",
        ntp_sources=("time.google.com",),
        pull_secret_set=False,
    )

    decision = decide_infraenv_action(
        _infraenv_intent(),
        observed,
        replace=False,
        iso_cached=True,
    )

    _assert_infraenv_action(decision, InfraEnvAction.REFUSE_DRIFT)
    assert decision.drift == ("ocp_version", "ntp_sources", "pull_secret")


@pytest.mark.parametrize(
    ("iso_available", "iso_cached", "expected"),
    [
        (False, False, InfraEnvAction.WAIT_FOR_ISO),
        (False, True, InfraEnvAction.WAIT_FOR_ISO),
        (True, False, InfraEnvAction.DOWNLOAD_ISO),
        (True, True, InfraEnvAction.READY),
    ],
)
def test_compatible_infraenv_selects_next_iso_action(
    iso_available: bool,
    iso_cached: bool,
    expected: InfraEnvAction,
) -> None:
    decision = decide_infraenv_action(
        _infraenv_intent(),
        _infraenv_observed(iso_available=iso_available),
        replace=False,
        iso_cached=iso_cached,
    )

    _assert_infraenv_action(decision, expected)
    assert decision.drift == ()


def test_infraenv_drift_precedes_iso_and_cache_state() -> None:
    observed = replace(
        _infraenv_observed(iso_available=False),
        ocp_version="4.20",
    )

    decision = decide_infraenv_action(
        _infraenv_intent(),
        observed,
        replace=False,
        iso_cached=False,
    )

    _assert_infraenv_action(decision, InfraEnvAction.REFUSE_DRIFT)
    assert decision.drift == ("ocp_version",)
