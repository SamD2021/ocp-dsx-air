from dataclasses import replace
from pathlib import Path
from uuid import UUID

import pytest

from ocp_dsx_air.cli.commands.access import resolve_active_jump_host
from ocp_dsx_air.core.contracts import (
    AirSimulationSnapshot,
    AirSimulationStatus,
    JumpHostSnapshot,
)
from ocp_dsx_air.core.exceptions import JumpHostError

from ..support.deployment import FakeAir, FakeClock, air_simulation_intent


def _managed_air(
    status: AirSimulationStatus,
) -> tuple[FakeAir, AirSimulationSnapshot]:
    air = FakeAir()
    simulation = air.import_simulation(air_simulation_intent())
    simulation = replace(simulation, status=status)
    air.simulations[simulation.id] = simulation
    air.calls.clear()
    return air, simulation


def test_inactive_access_without_start_has_exact_remediation() -> None:
    air, simulation = _managed_air(AirSimulationStatus.INACTIVE)

    with pytest.raises(JumpHostError, match=r"ocp-air start.*or retry with --start"):
        resolve_active_jump_host(
            air,  # type: ignore[arg-type]
            simulation.name,
            spec_path=Path("spec.yaml"),
            start=False,
            require_reachable=True,
            error_type=JumpHostError,
            announce=lambda message: None,
            clock=FakeClock(),
        )


def test_access_start_wakes_simulation_and_resolves_current_endpoint() -> None:
    air, simulation = _managed_air(AirSimulationStatus.INACTIVE)
    old = JumpHostSnapshot(UUID(int=80), "old.example.test", 22001, "ubuntu")
    current = JumpHostSnapshot(UUID(int=81), "new.example.test", 22002, "ubuntu")
    endpoints = iter([old, current])
    air.find_jump_host = lambda simulation_id: next(endpoints)  # type: ignore[method-assign]
    clock = FakeClock()

    result = resolve_active_jump_host(
        air,  # type: ignore[arg-type]
        simulation.name,
        spec_path=Path("spec.yaml"),
        start=True,
        require_reachable=True,
        error_type=JumpHostError,
        announce=lambda message: None,
        clock=clock,
        probe=lambda endpoint: endpoint is current,
    )

    assert result is current
    assert [call.operation for call in air.calls].count("start_simulation") == 1
    assert clock.sleeps == [5]


def test_jump_host_timeout_does_not_restart_active_simulation() -> None:
    air, simulation = _managed_air(AirSimulationStatus.ACTIVE)
    air.find_jump_host = lambda simulation_id: None  # type: ignore[method-assign]
    clock = FakeClock()

    with pytest.raises(JumpHostError, match="Timed out waiting"):
        resolve_active_jump_host(
            air,  # type: ignore[arg-type]
            simulation.name,
            spec_path=Path("spec.yaml"),
            start=True,
            require_reachable=True,
            error_type=JumpHostError,
            announce=lambda message: None,
            clock=clock,
            jump_host_timeout_seconds=10,
        )

    assert "start_simulation" not in [call.operation for call in air.calls]
    assert clock.sleeps == [5, 5]
