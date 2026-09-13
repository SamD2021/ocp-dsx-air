"""Shared Air wake-up and jump-host resolution for interactive access."""

import socket
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from ocp_dsx_air.adapters.air import NvidiaAirAdapter
from ocp_dsx_air.core.contracts import AirSimulationStatus, JumpHostSnapshot
from ocp_dsx_air.core.exceptions import OcpAirError
from ocp_dsx_air.core.runtime import Clock, SystemClock
from ocp_dsx_air.core.simulation_lifecycle import start_simulation

AccessError = TypeVar("AccessError", bound=OcpAirError)


def _tcp_reachable(jump_host: JumpHostSnapshot) -> bool:
    try:
        with socket.create_connection((jump_host.host, jump_host.port), timeout=5):
            return True
    except OSError:
        return False


def resolve_active_jump_host(
    air: NvidiaAirAdapter,
    simulation_name: str,
    *,
    spec_path: Path,
    start: bool,
    require_reachable: bool,
    error_type: type[AccessError],
    announce: Callable[[str], None],
    clock: Clock | None = None,
    start_timeout_seconds: float = 30 * 60,
    jump_host_timeout_seconds: float = 5 * 60,
    probe: Callable[[JumpHostSnapshot], bool] | None = None,
) -> JumpHostSnapshot:
    """Optionally wake a simulation, then resolve its current SSH endpoint."""
    runtime_clock = clock or SystemClock()
    simulation = air.find_simulation(simulation_name)
    if simulation is None:
        raise error_type(f"NVIDIA Air simulation {simulation_name!r} does not exist")
    if simulation.status is not AirSimulationStatus.ACTIVE:
        if not start:
            raise error_type(
                f"NVIDIA Air simulation {simulation_name!r} is {simulation.status.value}; "
                f"run 'ocp-air start --spec {spec_path}' or retry with --start"
            )
        simulation = start_simulation(
            air,
            simulation_name,
            clock=runtime_clock,
            timeout_seconds=start_timeout_seconds,
            observer=lambda observed: announce(f"[simulation] state={observed.status.value}"),
        ).simulation

    deadline = runtime_clock.monotonic() + jump_host_timeout_seconds
    while True:
        jump_host = air.find_jump_host(simulation.id)
        if jump_host is not None and (not require_reachable or (probe or _tcp_reachable)(jump_host)):
            return jump_host
        remaining = deadline - runtime_clock.monotonic()
        if remaining <= 0:
            raise error_type("Timed out waiting for the jump-host SSH service")
        runtime_clock.sleep(min(5, remaining))
