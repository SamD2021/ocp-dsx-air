from dataclasses import replace

import pytest

from ocp_dsx_air.core.contracts import AirSimulationSnapshot, AirSimulationStatus
from ocp_dsx_air.core.exceptions import AirSimError
from ocp_dsx_air.core.simulation_lifecycle import (
    observe_simulation,
    restart_simulation,
    start_simulation,
    stop_simulation,
)

from ..support.deployment import FakeAir, FakeClock, air_simulation_intent


def _air_with_simulation(
    status: AirSimulationStatus,
    *,
    managed: bool = True,
) -> tuple[FakeAir, AirSimulationSnapshot]:
    air = FakeAir()
    simulation = air.import_simulation(air_simulation_intent())
    simulation = replace(simulation, status=status, managed_by_us=managed)
    air.simulations[simulation.id] = simulation
    air.calls.clear()
    return air, simulation


def _operations(air: FakeAir) -> list[str]:
    return [call.operation for call in air.calls]


def test_observe_reports_unmanaged_simulation_without_mutation() -> None:
    air, simulation = _air_with_simulation(
        AirSimulationStatus.INACTIVE,
        managed=False,
    )

    observed = observe_simulation(air, simulation.name)

    assert observed.managed_by_us is False
    assert _operations(air) == ["find_simulation"]


def test_start_active_simulation_is_idempotent() -> None:
    air, simulation = _air_with_simulation(AirSimulationStatus.ACTIVE)
    clock = FakeClock()

    result = start_simulation(
        air,
        simulation.name,
        clock=clock,
        timeout_seconds=30,
    )

    assert result.simulation.status is AirSimulationStatus.ACTIVE
    assert result.request_submitted is False
    assert _operations(air) == ["find_simulation"]
    assert clock.sleeps == []


def test_start_inactive_simulation_checks_capacity_and_starts_once() -> None:
    air, simulation = _air_with_simulation(AirSimulationStatus.INACTIVE)

    result = start_simulation(
        air,
        simulation.name,
        clock=FakeClock(),
        timeout_seconds=30,
    )

    assert result.simulation.status is AirSimulationStatus.ACTIVE
    assert result.request_submitted is True
    assert _operations(air) == [
        "find_simulation",
        "ensure_simulation_capacity",
        "start_simulation",
        "find_simulation",
    ]


def test_start_joins_existing_startup_without_duplicate_request() -> None:
    air, simulation = _air_with_simulation(AirSimulationStatus.BOOTING)
    original_find = air.find_simulation
    observations = iter(
        [
            replace(simulation, status=AirSimulationStatus.BOOTING),
            replace(simulation, status=AirSimulationStatus.BOOTING),
            replace(simulation, status=AirSimulationStatus.ACTIVE),
        ]
    )
    air.find_simulation = lambda name: next(observations)  # type: ignore[method-assign]
    clock = FakeClock()

    result = start_simulation(
        air,
        simulation.name,
        clock=clock,
        timeout_seconds=30,
    )

    air.find_simulation = original_find  # type: ignore[method-assign]
    assert result.simulation.status is AirSimulationStatus.ACTIVE
    assert result.request_submitted is False
    assert clock.sleeps == [5]


def test_start_waits_for_existing_shutdown_then_starts() -> None:
    air, simulation = _air_with_simulation(AirSimulationStatus.SAVING)
    observations = iter(
        [
            replace(simulation, status=AirSimulationStatus.SAVING),
            replace(simulation, status=AirSimulationStatus.INACTIVE),
            replace(simulation, status=AirSimulationStatus.ACTIVE),
        ]
    )
    air.find_simulation = lambda name: next(observations)  # type: ignore[method-assign]

    result = start_simulation(
        air,
        simulation.name,
        clock=FakeClock(),
        timeout_seconds=30,
    )

    assert result.simulation.status is AirSimulationStatus.ACTIVE
    assert result.request_submitted is True
    assert _operations(air) == ["ensure_simulation_capacity", "start_simulation"]


def test_stop_is_asynchronous_and_preserves_checkpoint() -> None:
    air, simulation = _air_with_simulation(AirSimulationStatus.ACTIVE)
    clock = FakeClock()

    result = stop_simulation(
        air,
        simulation.name,
        clock=clock,
        wait=False,
        timeout_seconds=30,
    )

    assert result.simulation.status is AirSimulationStatus.INACTIVE
    assert result.request_submitted is True
    assert clock.sleeps == []
    shutdown = next(call for call in air.calls if call.operation == "shutdown_simulation")
    assert shutdown.kwargs == {"create_checkpoint": True}
    assert _operations(air).count("find_simulation") == 2


def test_stop_inactive_and_already_stopping_are_idempotent() -> None:
    inactive_air, inactive = _air_with_simulation(AirSimulationStatus.INACTIVE)
    stopping_air, stopping = _air_with_simulation(AirSimulationStatus.SAVING)

    inactive_result = stop_simulation(
        inactive_air,
        inactive.name,
        clock=FakeClock(),
        wait=False,
        timeout_seconds=30,
    )
    stopping_result = stop_simulation(
        stopping_air,
        stopping.name,
        clock=FakeClock(),
        wait=False,
        timeout_seconds=30,
    )

    assert inactive_result.request_submitted is False
    assert stopping_result.request_submitted is False
    assert "shutdown_simulation" not in _operations(inactive_air)
    assert "shutdown_simulation" not in _operations(stopping_air)


def test_waiting_stop_reports_progress_and_does_not_cancel_on_timeout() -> None:
    air, simulation = _air_with_simulation(AirSimulationStatus.SAVING)
    air.find_simulation = lambda name: replace(  # type: ignore[method-assign]
        simulation,
        status=AirSimulationStatus.SAVING,
    )
    clock = FakeClock()

    with pytest.raises(AirSimError, match="was not cancelled"):
        stop_simulation(
            air,
            simulation.name,
            clock=clock,
            wait=True,
            timeout_seconds=10,
        )

    assert clock.sleeps == [5, 5]
    assert "shutdown_simulation" not in _operations(air)


def test_waiting_stop_reports_only_state_changes_until_inactive() -> None:
    air, simulation = _air_with_simulation(AirSimulationStatus.SAVING)
    observations = iter(
        [
            replace(simulation, status=AirSimulationStatus.SAVING),
            replace(simulation, status=AirSimulationStatus.SAVING),
            replace(simulation, status=AirSimulationStatus.INACTIVE),
        ]
    )
    air.find_simulation = lambda name: next(observations)  # type: ignore[method-assign]
    states: list[AirSimulationStatus] = []

    result = stop_simulation(
        air,
        simulation.name,
        clock=FakeClock(),
        wait=True,
        timeout_seconds=30,
        observer=lambda observed: states.append(observed.status),
    )

    assert result.simulation.status is AirSimulationStatus.INACTIVE
    assert states == [AirSimulationStatus.SAVING, AirSimulationStatus.INACTIVE]


@pytest.mark.parametrize(
    "status",
    [
        AirSimulationStatus.BOOTING,
        AirSimulationStatus.INVALID,
        AirSimulationStatus.DELETING,
        AirSimulationStatus.DEMO,
        AirSimulationStatus.TRAINING,
        AirSimulationStatus.UNKNOWN,
    ],
)
def test_stop_refuses_unsafe_states(status: AirSimulationStatus) -> None:
    air, simulation = _air_with_simulation(status)

    with pytest.raises(AirSimError, match="Cannot stop"):
        stop_simulation(
            air,
            simulation.name,
            clock=FakeClock(),
            wait=False,
            timeout_seconds=30,
        )


def test_restart_active_simulation_checkpoints_then_starts() -> None:
    air, simulation = _air_with_simulation(AirSimulationStatus.ACTIVE)

    result = restart_simulation(
        air,
        simulation.name,
        clock=FakeClock(),
        timeout_seconds=60,
    )

    assert result.simulation.status is AirSimulationStatus.ACTIVE
    assert _operations(air) == [
        "find_simulation",
        "shutdown_simulation",
        "find_simulation",
        "ensure_simulation_capacity",
        "start_simulation",
        "find_simulation",
    ]
    shutdown = next(call for call in air.calls if call.operation == "shutdown_simulation")
    assert shutdown.kwargs == {"create_checkpoint": True}


def test_restart_inactive_simulation_only_starts() -> None:
    air, simulation = _air_with_simulation(AirSimulationStatus.INACTIVE)

    restart_simulation(
        air,
        simulation.name,
        clock=FakeClock(),
        timeout_seconds=60,
    )

    assert "shutdown_simulation" not in _operations(air)
    assert _operations(air).count("start_simulation") == 1


def test_restart_joins_existing_shutdown_without_duplicate_stop() -> None:
    air, simulation = _air_with_simulation(AirSimulationStatus.SHUTTING_DOWN)
    observations = iter(
        [
            replace(simulation, status=AirSimulationStatus.SHUTTING_DOWN),
            replace(simulation, status=AirSimulationStatus.INACTIVE),
            replace(simulation, status=AirSimulationStatus.ACTIVE),
        ]
    )
    air.find_simulation = lambda name: next(observations)  # type: ignore[method-assign]

    result = restart_simulation(
        air,
        simulation.name,
        clock=FakeClock(),
        timeout_seconds=60,
    )

    assert result.simulation.status is AirSimulationStatus.ACTIVE
    assert "shutdown_simulation" not in _operations(air)
    assert _operations(air).count("start_simulation") == 1


@pytest.mark.parametrize("operation", ["start", "stop", "restart"])
def test_mutations_refuse_unmanaged_simulation(operation: str) -> None:
    air, simulation = _air_with_simulation(
        AirSimulationStatus.INACTIVE,
        managed=False,
    )

    def mutate() -> None:
        if operation == "start":
            start_simulation(
                air,
                simulation.name,
                clock=FakeClock(),
                timeout_seconds=30,
            )
        elif operation == "stop":
            stop_simulation(
                air,
                simulation.name,
                clock=FakeClock(),
                wait=False,
                timeout_seconds=30,
            )
        else:
            restart_simulation(
                air,
                simulation.name,
                clock=FakeClock(),
                timeout_seconds=60,
            )

    with pytest.raises(AirSimError, match="unmanaged"):
        mutate()

    assert _operations(air) == ["find_simulation"]
