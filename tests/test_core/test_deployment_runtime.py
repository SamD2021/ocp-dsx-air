from dataclasses import FrozenInstanceError
from uuid import UUID

import pytest

from ocp_dsx_air.cli.reporting import CliDeploymentReporter
from ocp_dsx_air.core.contracts import DeploymentEvent, DeploymentPhase
from ocp_dsx_air.core.runtime import SystemClock

RESOURCE_ID = UUID("62f3d8c7-7257-4ce4-a94a-cdcf052ccf3f")


def test_deployment_event_is_immutable() -> None:
    event = DeploymentEvent(
        phase=DeploymentPhase.CLUSTER,
        message="Cluster does not exist",
        action="create",
        resource_id=RESOURCE_ID,
    )

    with pytest.raises(FrozenInstanceError):
        event.message = "changed"  # type: ignore[misc]


def test_system_clock_delegates_to_injected_time_functions() -> None:
    sleeps: list[float] = []
    clock = SystemClock(
        _monotonic=lambda: 17.5,
        _sleep=sleeps.append,
    )

    assert clock.monotonic() == 17.5
    clock.sleep(2.25)
    assert sleeps == [2.25]


@pytest.mark.parametrize("seconds", [-1.0, -0.01])
def test_system_clock_rejects_negative_sleep(seconds: float) -> None:
    clock = SystemClock(_sleep=lambda _: None)

    with pytest.raises(ValueError, match="non-negative"):
        clock.sleep(seconds)


def test_cli_reporter_renders_structured_event_without_optional_fields() -> None:
    output: list[str] = []
    reporter = CliDeploymentReporter(output=output.append)

    reporter.emit(
        DeploymentEvent(
            phase=DeploymentPhase.INFRAENV,
            message="Waiting for discovery image",
        )
    )

    assert output == ["[infraenv] Waiting for discovery image"]


def test_cli_reporter_renders_action_and_resource_identifier() -> None:
    output: list[str] = []
    reporter = CliDeploymentReporter(output=output.append)

    reporter.emit(
        DeploymentEvent(
            phase=DeploymentPhase.AIR_IMAGES,
            message="Uploading discovery image",
            action="upload",
            resource_id=RESOURCE_ID,
        )
    )

    assert output == [
        f"[air-images] Uploading discovery image (action=upload, resource={RESOURCE_ID})"
    ]
