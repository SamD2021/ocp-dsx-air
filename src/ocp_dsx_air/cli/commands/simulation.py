"""Production composition and output for simulation lifecycle commands."""

from collections.abc import Callable
from pathlib import Path

from ocp_dsx_air.adapters.air import NvidiaAirAdapter
from ocp_dsx_air.cli.commands.deploy import _read_secret
from ocp_dsx_air.core.contracts import AirSimulationSnapshot, AirSimulationStatus
from ocp_dsx_air.core.runtime import SystemClock
from ocp_dsx_air.core.simulation_lifecycle import (
    STARTING_STATUSES,
    STOPPING_STATUSES,
    observe_simulation,
    restart_simulation,
    start_simulation,
    stop_simulation,
)
from ocp_dsx_air.models.spec import load_spec

Announce = Callable[[str], None]


def _load(spec_path: Path) -> tuple[NvidiaAirAdapter, str]:
    spec = load_spec(spec_path)
    api_key = _read_secret(
        spec.auth.air_api_key_file,
        field="auth.air_api_key_file",
    )
    return NvidiaAirAdapter(api_key=api_key), spec.simulation.name


def _progress(announce: Announce) -> Callable[[AirSimulationSnapshot], None]:
    def emit(simulation: AirSimulationSnapshot) -> None:
        announce(f"[simulation] state={simulation.status.value}")

    return emit


def _next_action(spec_path: Path, status: AirSimulationStatus) -> str:
    quoted = str(spec_path)
    if status is AirSimulationStatus.ACTIVE:
        return f"ocp-air tunnel --spec {quoted}"
    if status is AirSimulationStatus.INACTIVE:
        return f"ocp-air start --spec {quoted}"
    if status in STARTING_STATUSES:
        return f"ocp-air status --spec {quoted}"
    if status in STOPPING_STATUSES:
        return f"ocp-air restart --spec {quoted}"
    return "Check the simulation in NVIDIA Air before continuing."


def run_status(spec_path: Path, *, announce: Announce = print) -> AirSimulationSnapshot:
    air, name = _load(spec_path)
    simulation = observe_simulation(air, name)
    jump_host = air.find_jump_host(simulation.id)
    announce(f"Simulation: {simulation.name}")
    announce(f"ID: {simulation.id}")
    announce(f"State: {simulation.status.value}")
    announce(f"Managed: {'yes' if simulation.managed_by_us else 'no'}")
    if jump_host is None:
        announce("Jump host: unavailable")
    else:
        announce(f"Jump host: {jump_host.username}@{jump_host.host}:{jump_host.port}")
    announce(f"Next: {_next_action(spec_path, simulation.status)}")
    return simulation


def run_start(
    spec_path: Path,
    *,
    timeout_seconds: float,
    announce: Announce = print,
) -> AirSimulationSnapshot:
    air, name = _load(spec_path)
    result = start_simulation(
        air,
        name,
        clock=SystemClock(),
        timeout_seconds=timeout_seconds,
        observer=_progress(announce),
    )
    if result.request_submitted:
        announce(f"Simulation {name!r} started successfully.")
    else:
        announce(f"Simulation {name!r} is active.")
    return result.simulation


def run_stop(
    spec_path: Path,
    *,
    wait: bool,
    timeout_seconds: float,
    announce: Announce = print,
) -> AirSimulationSnapshot:
    air, name = _load(spec_path)
    result = stop_simulation(
        air,
        name,
        clock=SystemClock(),
        wait=wait,
        timeout_seconds=timeout_seconds,
        observer=_progress(announce),
    )
    if result.simulation.status is AirSimulationStatus.INACTIVE:
        announce(f"Simulation {name!r} is inactive.")
    elif wait:
        announce(f"Simulation {name!r} stop completed.")
    else:
        if result.request_submitted:
            announce(f"Shutdown requested for simulation {name!r}.")
        else:
            announce(f"Simulation {name!r} is already stopping.")
        announce(f"Current state: {result.simulation.status.value}")
        announce("Checkpointing continues in NVIDIA Air.")
        announce(f"Check progress: ocp-air status --spec {spec_path}")
        announce(f"Wait for completion: ocp-air stop --spec {spec_path} --wait")
    return result.simulation


def run_restart(
    spec_path: Path,
    *,
    timeout_seconds: float,
    announce: Announce = print,
) -> AirSimulationSnapshot:
    air, name = _load(spec_path)
    result = restart_simulation(
        air,
        name,
        clock=SystemClock(),
        timeout_seconds=timeout_seconds,
        observer=_progress(announce),
    )
    announce(f"Simulation {name!r} restarted and is active.")
    return result.simulation
