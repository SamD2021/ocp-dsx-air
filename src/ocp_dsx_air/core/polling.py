from collections.abc import Mapping
from typing import Final

from ocp_dsx_air.core.contracts import (
    AssistedHostSnapshot,
    HostStatus,
    InstallStage,
    IssueCode,
    PollIssue,
    Severity,
)

FAST_POLL_STAGES: Final[frozenset[InstallStage]] = frozenset(
    {
        InstallStage.WRITING_IMAGE,
        InstallStage.REBOOTING,
        InstallStage.WAITING_FOR_IGNITION,
        InstallStage.CONFIGURING,
    }
)

FAST_POLL_HOST_STATUSES: Final[frozenset[HostStatus]] = frozenset(
    {
        HostStatus.INSTALLING,
        HostStatus.INSTALLING_IN_PROGRESS,
        HostStatus.INSTALLING_PENDING_USER_ACTION,
    }
)

ACTION_HOST_ISSUES: Final[Mapping[HostStatus, IssueCode]] = {
    HostStatus.ERROR: IssueCode.HOST_ERROR,
    HostStatus.CANCELLED: IssueCode.HOST_CANCELLED,
    HostStatus.INSTALLING_PENDING_USER_ACTION: (
        IssueCode.HOST_INSTALLING_PENDING_USER_ACTION
    ),
    HostStatus.RESETTING_PENDING_USER_ACTION: (
        IssueCode.HOST_RESETTING_PENDING_USER_ACTION
    ),
    HostStatus.UNBINDING_PENDING_USER_ACTION: (
        IssueCode.HOST_UNBINDING_PENDING_USER_ACTION
    ),
}


def poll_interval_seconds(
    hosts: tuple[AssistedHostSnapshot, ...],
    *,
    normal: float,
    fast: float,
) -> float:
    if normal <= 0 or fast <= 0:
        raise ValueError("poll intervals must be positive")

    fragile = any(
        host.status in FAST_POLL_HOST_STATUSES
        or host.install_stage in FAST_POLL_STAGES
        for host in hosts
    )
    return fast if fragile else normal


def find_poll_issues(
    hosts: tuple[AssistedHostSnapshot, ...],
) -> tuple[PollIssue, ...]:
    """Report host conditions requiring attention without performing recovery."""
    issues: list[PollIssue] = []

    for host in hosts:
        label = (
            host.requested_hostname
            or host.inventory_hostname
            or str(host.id)
        )

        code = ACTION_HOST_ISSUES.get(host.status)
        if code is not None:
            issues.append(
                PollIssue(
                    severity=Severity.ACTION_REQUIRED,
                    code=code,
                    detail=(
                        f"Host {label!r} is in "
                        f"{host.status.value!r} state: {host.status_info}"
                    ),
                )
            )
        elif host.status is HostStatus.INSUFFICIENT:
            issues.append(
                PollIssue(
                    severity=Severity.WARNING,
                    code=IssueCode.HOST_INSUFFICIENT,
                    detail=(
                        f"Host {label!r} is insufficient: "
                        f"{host.status_info}"
                    ),
                )
            )

    return tuple(issues)
