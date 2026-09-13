"""Explicit lifecycle operations for an existing managed Air simulation."""

from collections.abc import Callable
from dataclasses import dataclass

from ocp_dsx_air.core.contracts import AirSimulationSnapshot, AirSimulationStatus
from ocp_dsx_air.core.exceptions import AirSimError
from ocp_dsx_air.core.ports.air import AirPort
from ocp_dsx_air.core.runtime import Clock

SimulationObserver = Callable[[AirSimulationSnapshot], None]

STARTING_STATUSES = frozenset(
    {
        AirSimulationStatus.REQUESTING,
        AirSimulationStatus.PROVISIONING,
        AirSimulationStatus.PREPARE_BOOT,
        AirSimulationStatus.BOOTING,
        AirSimulationStatus.PREPARE_REBUILD,
        AirSimulationStatus.REBUILDING,
    }
)
STOPPING_STATUSES = frozenset(
    {
        AirSimulationStatus.PREPARE_SHUTDOWN,
        AirSimulationStatus.SHUTTING_DOWN,
        AirSimulationStatus.SAVING,
    }
)


@dataclass(frozen=True, slots=True)
class SimulationLifecycleResult:
    simulation: AirSimulationSnapshot
    request_submitted: bool = False


def _observe(
    air: AirPort,
    name: str,
    *,
    require_managed: bool,
) -> AirSimulationSnapshot:
    simulation = air.find_simulation(name)
    if simulation is None:
        raise AirSimError(f"Air simulation {name!r} does not exist")
    if require_managed and not simulation.managed_by_us:
        raise AirSimError(f"Refusing to operate unmanaged Air simulation {name!r}")
    return simulation


def observe_simulation(air: AirPort, name: str) -> AirSimulationSnapshot:
    """Return one exact-name simulation observation without mutating it."""
    return _observe(air, name, require_managed=False)


def _notify_on_change(
    observer: SimulationObserver | None,
    simulation: AirSimulationSnapshot,
    previous: AirSimulationStatus | None,
) -> AirSimulationStatus:
    if observer is not None and simulation.status is not previous:
        observer(simulation)
    return simulation.status


def _wait_for(
    air: AirPort,
    name: str,
    *,
    target: AirSimulationStatus,
    allowed: frozenset[AirSimulationStatus],
    clock: Clock,
    deadline: float,
    poll_seconds: float,
    observer: SimulationObserver | None,
    previous: AirSimulationStatus | None = None,
) -> AirSimulationSnapshot:
    while True:
        simulation = _observe(air, name, require_managed=True)
        previous = _notify_on_change(observer, simulation, previous)
        if simulation.status is target:
            return simulation
        if simulation.status not in allowed:
            raise AirSimError(
                f"Air simulation {name!r} entered unexpected state "
                f"{simulation.status.value!r} while waiting for {target.value!r}"
            )
        remaining = deadline - clock.monotonic()
        if remaining <= 0:
            raise AirSimError(
                f"Timed out waiting for Air simulation {name!r} to reach {target.value!r}; "
                "the NVIDIA Air operation was not cancelled"
            )
        clock.sleep(min(poll_seconds, remaining))


def _request_start_and_wait(
    air: AirPort,
    simulation: AirSimulationSnapshot,
    *,
    clock: Clock,
    deadline: float,
    poll_seconds: float,
    observer: SimulationObserver | None,
) -> SimulationLifecycleResult:
    air.ensure_simulation_capacity(simulation.id)
    air.start_simulation(simulation.id)
    active = _wait_for(
        air,
        simulation.name,
        target=AirSimulationStatus.ACTIVE,
        allowed=STARTING_STATUSES | {AirSimulationStatus.INACTIVE},
        clock=clock,
        deadline=deadline,
        poll_seconds=poll_seconds,
        observer=observer,
        previous=simulation.status,
    )
    return SimulationLifecycleResult(active, request_submitted=True)


def start_simulation(
    air: AirPort,
    name: str,
    *,
    clock: Clock,
    timeout_seconds: float,
    poll_seconds: float = 5,
    observer: SimulationObserver | None = None,
) -> SimulationLifecycleResult:
    """Start or join startup for one managed simulation and wait for ACTIVE."""
    if timeout_seconds <= 0 or poll_seconds <= 0:
        raise ValueError("Simulation timeout and poll interval must be positive")
    deadline = clock.monotonic() + timeout_seconds
    simulation = _observe(air, name, require_managed=True)
    _notify_on_change(observer, simulation, None)
    if simulation.status is AirSimulationStatus.ACTIVE:
        return SimulationLifecycleResult(simulation)
    if simulation.status in STARTING_STATUSES:
        active = _wait_for(
            air,
            name,
            target=AirSimulationStatus.ACTIVE,
            allowed=STARTING_STATUSES,
            clock=clock,
            deadline=deadline,
            poll_seconds=poll_seconds,
            observer=observer,
            previous=simulation.status,
        )
        return SimulationLifecycleResult(active)
    if simulation.status in STOPPING_STATUSES:
        simulation = _wait_for(
            air,
            name,
            target=AirSimulationStatus.INACTIVE,
            allowed=STOPPING_STATUSES,
            clock=clock,
            deadline=deadline,
            poll_seconds=poll_seconds,
            observer=observer,
            previous=simulation.status,
        )
    if simulation.status is AirSimulationStatus.INACTIVE:
        return _request_start_and_wait(
            air,
            simulation,
            clock=clock,
            deadline=deadline,
            poll_seconds=poll_seconds,
            observer=observer,
        )
    raise AirSimError(f"Cannot start Air simulation {name!r} from state {simulation.status.value!r}")


def stop_simulation(
    air: AirPort,
    name: str,
    *,
    clock: Clock,
    wait: bool,
    timeout_seconds: float,
    poll_seconds: float = 5,
    observer: SimulationObserver | None = None,
) -> SimulationLifecycleResult:
    """Request a checkpoint-preserving stop, optionally waiting for INACTIVE."""
    if timeout_seconds <= 0 or poll_seconds <= 0:
        raise ValueError("Simulation timeout and poll interval must be positive")
    simulation = _observe(air, name, require_managed=True)
    _notify_on_change(observer, simulation, None)
    request_submitted = False
    if simulation.status is AirSimulationStatus.ACTIVE:
        air.shutdown_simulation(simulation.id, create_checkpoint=True)
        request_submitted = True
        simulation = _observe(air, name, require_managed=True)
        _notify_on_change(observer, simulation, AirSimulationStatus.ACTIVE)
    elif simulation.status is AirSimulationStatus.INACTIVE:
        return SimulationLifecycleResult(simulation)
    elif simulation.status not in STOPPING_STATUSES:
        raise AirSimError(f"Cannot stop Air simulation {name!r} from state {simulation.status.value!r}")

    if not wait or simulation.status is AirSimulationStatus.INACTIVE:
        return SimulationLifecycleResult(simulation, request_submitted=request_submitted)

    inactive = _wait_for(
        air,
        name,
        target=AirSimulationStatus.INACTIVE,
        allowed=STOPPING_STATUSES | {AirSimulationStatus.ACTIVE},
        clock=clock,
        deadline=clock.monotonic() + timeout_seconds,
        poll_seconds=poll_seconds,
        observer=observer,
        previous=simulation.status,
    )
    return SimulationLifecycleResult(inactive, request_submitted=request_submitted)


def restart_simulation(
    air: AirPort,
    name: str,
    *,
    clock: Clock,
    timeout_seconds: float,
    poll_seconds: float = 5,
    observer: SimulationObserver | None = None,
) -> SimulationLifecycleResult:
    """Checkpoint, stop, and start one managed simulation within one deadline."""
    if timeout_seconds <= 0 or poll_seconds <= 0:
        raise ValueError("Simulation timeout and poll interval must be positive")
    deadline = clock.monotonic() + timeout_seconds
    simulation = _observe(air, name, require_managed=True)
    _notify_on_change(observer, simulation, None)
    stop_requested = False

    if simulation.status is AirSimulationStatus.ACTIVE:
        air.shutdown_simulation(simulation.id, create_checkpoint=True)
        stop_requested = True
        simulation = _wait_for(
            air,
            name,
            target=AirSimulationStatus.INACTIVE,
            allowed=STOPPING_STATUSES | {AirSimulationStatus.ACTIVE},
            clock=clock,
            deadline=deadline,
            poll_seconds=poll_seconds,
            observer=observer,
            previous=AirSimulationStatus.ACTIVE,
        )
    elif simulation.status in STOPPING_STATUSES:
        simulation = _wait_for(
            air,
            name,
            target=AirSimulationStatus.INACTIVE,
            allowed=STOPPING_STATUSES,
            clock=clock,
            deadline=deadline,
            poll_seconds=poll_seconds,
            observer=observer,
            previous=simulation.status,
        )
    elif simulation.status is not AirSimulationStatus.INACTIVE:
        raise AirSimError(f"Cannot restart Air simulation {name!r} from state {simulation.status.value!r}")

    started = _request_start_and_wait(
        air,
        simulation,
        clock=clock,
        deadline=deadline,
        poll_seconds=poll_seconds,
        observer=observer,
    )
    return SimulationLifecycleResult(
        started.simulation,
        request_submitted=stop_requested or started.request_submitted,
    )
