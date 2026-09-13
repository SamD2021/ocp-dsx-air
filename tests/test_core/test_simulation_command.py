from dataclasses import replace
from pathlib import Path

import pytest

from ocp_dsx_air.cli.commands import simulation as command
from ocp_dsx_air.core.contracts import AirSimulationStatus, JumpHostSnapshot

from ..support.deployment import FakeAir, FakeClock, air_simulation_intent


def _air(status: AirSimulationStatus) -> FakeAir:
    air = FakeAir()
    simulation = air.import_simulation(air_simulation_intent())
    air.simulations[simulation.id] = replace(simulation, status=status)
    air.calls.clear()
    return air


def test_status_reports_identity_ownership_endpoint_and_next_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    air = _air(AirSimulationStatus.ACTIVE)
    air.find_jump_host = lambda simulation_id: JumpHostSnapshot(  # type: ignore[attr-defined]
        simulation_id,
        "jump.example.test",
        22022,
        "ubuntu",
    )
    monkeypatch.setattr(command, "_load", lambda path: (air, "dsx-lab"))
    messages: list[str] = []

    result = command.run_status(Path("spec.yaml"), announce=messages.append)

    assert result.status is AirSimulationStatus.ACTIVE
    assert any(message == "Simulation: dsx-lab" for message in messages)
    assert any(message == "Managed: yes" for message in messages)
    assert any(message.startswith("Jump host: ubuntu@jump.example.test:") for message in messages)
    assert messages[-1] == "Next: ocp-air tunnel --spec spec.yaml"


def test_async_stop_explains_checkpoint_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    air = _air(AirSimulationStatus.ACTIVE)
    simulation = next(iter(air.simulations.values()))

    def begin_shutdown(simulation_id, *, create_checkpoint: bool) -> None:
        assert create_checkpoint is True
        air.simulations[simulation_id] = replace(
            simulation,
            status=AirSimulationStatus.SAVING,
        )

    air.shutdown_simulation = begin_shutdown  # type: ignore[method-assign]
    monkeypatch.setattr(command, "_load", lambda path: (air, "dsx-lab"))
    monkeypatch.setattr(command, "SystemClock", FakeClock)
    messages: list[str] = []

    result = command.run_stop(
        Path("spec.yaml"),
        wait=False,
        timeout_seconds=30,
        announce=messages.append,
    )

    assert result.status is AirSimulationStatus.SAVING
    assert "Shutdown requested for simulation 'dsx-lab'." in messages
    assert "Checkpointing continues in NVIDIA Air." in messages
    assert messages[-1].endswith("--wait")
