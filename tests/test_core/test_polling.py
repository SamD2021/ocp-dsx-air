from ipaddress import IPv4Address
from uuid import UUID

import pytest

from ocp_dsx_air.core.contracts import (
    AssistedHostSnapshot,
    HostStatus,
    InstallStage,
    IssueCode,
    OpenShiftNodeRole,
    Severity,
)
from ocp_dsx_air.core.polling import find_poll_issues, poll_interval_seconds


def _host(
    *,
    status: HostStatus = HostStatus.KNOWN,
    install_stage: InstallStage = InstallStage.UNKNOWN,
    requested_hostname: str | None = "master-0",
    inventory_hostname: str | None = "master-0.internal",
    status_info: str = "",
) -> AssistedHostSnapshot:
    return AssistedHostSnapshot(
        id=UUID("18b86b2e-46a7-43af-8de8-1a482cd68eb6"),
        infraenv_id=UUID("7a0ddc45-ce1a-4d8d-ab9f-0be5fbe98d27"),
        requested_hostname=requested_hostname,
        inventory_hostname=inventory_hostname,
        status=status,
        status_info=status_info,
        role=OpenShiftNodeRole.MASTER,
        ipv4_addresses=(IPv4Address("192.168.200.20"),),
        install_stage=install_stage,
        progress_info="",
    )


@pytest.mark.parametrize(
    "status",
    [
        HostStatus.INSTALLING,
        HostStatus.INSTALLING_IN_PROGRESS,
        HostStatus.INSTALLING_PENDING_USER_ACTION,
    ],
)
def test_fragile_host_status_uses_fast_poll_interval(status: HostStatus) -> None:
    interval = poll_interval_seconds((_host(status=status),), normal=30, fast=5)

    assert interval == 5


@pytest.mark.parametrize(
    "stage",
    [
        InstallStage.WRITING_IMAGE,
        InstallStage.REBOOTING,
        InstallStage.WAITING_FOR_IGNITION,
        InstallStage.CONFIGURING,
    ],
)
def test_fragile_install_stage_uses_fast_poll_interval(
    stage: InstallStage,
) -> None:
    interval = poll_interval_seconds(
        (_host(install_stage=stage),),
        normal=30,
        fast=5,
    )

    assert interval == 5


@pytest.mark.parametrize(
    "hosts",
    [
        (),
        (_host(status=HostStatus.KNOWN),),
        (_host(status=HostStatus.INSTALLED),),
    ],
)
def test_non_fragile_hosts_use_normal_poll_interval(
    hosts: tuple[AssistedHostSnapshot, ...],
) -> None:
    assert poll_interval_seconds(hosts, normal=30, fast=5) == 30


@pytest.mark.parametrize(
    ("normal", "fast"),
    [(0, 5), (-1, 5), (30, 0), (30, -1)],
)
def test_poll_intervals_must_be_positive(normal: int, fast: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        poll_interval_seconds((), normal=normal, fast=fast)


@pytest.mark.parametrize(
    ("status", "expected_code"),
    [
        (HostStatus.ERROR, IssueCode.HOST_ERROR),
        (HostStatus.CANCELLED, IssueCode.HOST_CANCELLED),
        (
            HostStatus.INSTALLING_PENDING_USER_ACTION,
            IssueCode.HOST_INSTALLING_PENDING_USER_ACTION,
        ),
        (
            HostStatus.RESETTING_PENDING_USER_ACTION,
            IssueCode.HOST_RESETTING_PENDING_USER_ACTION,
        ),
        (
            HostStatus.UNBINDING_PENDING_USER_ACTION,
            IssueCode.HOST_UNBINDING_PENDING_USER_ACTION,
        ),
    ],
)
def test_actionable_host_status_reports_action_required_issue(
    status: HostStatus,
    expected_code: IssueCode,
) -> None:
    (issue,) = find_poll_issues(
        (_host(status=status, status_info="operator intervention required"),)
    )

    assert issue.severity is Severity.ACTION_REQUIRED
    assert issue.code is expected_code
    assert "master-0" in issue.detail
    assert status.value in issue.detail
    assert "operator intervention required" in issue.detail


def test_insufficient_host_reports_warning() -> None:
    (issue,) = find_poll_issues(
        (_host(status=HostStatus.INSUFFICIENT, status_info="NTP not synchronized"),)
    )

    assert issue.severity is Severity.WARNING
    assert issue.code is IssueCode.HOST_INSUFFICIENT
    assert "master-0" in issue.detail
    assert "NTP not synchronized" in issue.detail


@pytest.mark.parametrize(
    "status",
    [
        HostStatus.DISCOVERING,
        HostStatus.KNOWN,
        HostStatus.READY,
        HostStatus.INSTALLING,
        HostStatus.INSTALLING_IN_PROGRESS,
        HostStatus.INSTALLED,
    ],
)
def test_non_actionable_host_status_reports_no_issue(status: HostStatus) -> None:
    assert find_poll_issues((_host(status=status),)) == ()


def test_issue_uses_inventory_hostname_when_requested_hostname_is_absent() -> None:
    (issue,) = find_poll_issues(
        (
            _host(
                status=HostStatus.ERROR,
                requested_hostname=None,
                inventory_hostname="master-0.internal",
            ),
        )
    )

    assert "master-0.internal" in issue.detail


def test_issue_uses_host_id_when_hostnames_are_absent() -> None:
    host = _host(
        status=HostStatus.ERROR,
        requested_hostname=None,
        inventory_hostname=None,
    )

    (issue,) = find_poll_issues((host,))

    assert str(host.id) in issue.detail
