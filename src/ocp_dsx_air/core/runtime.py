"""Runtime seams for deterministic deployment orchestration."""

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from ocp_dsx_air.core.contracts import DeploymentEvent


class Clock(Protocol):
    """Provide monotonic time and sleeping to reconciliation loops."""

    def monotonic(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...


class DeploymentReporter(Protocol):
    """Receive structured, non-secret deployment progress events."""

    def emit(self, event: DeploymentEvent) -> None: ...


@dataclass(slots=True)
class SystemClock:
    """Adapt the system monotonic clock and sleeper to ``Clock``."""

    _monotonic: Callable[[], float] = time.monotonic
    _sleep: Callable[[float], None] = time.sleep

    def monotonic(self) -> float:
        return self._monotonic()

    def sleep(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("sleep duration must be non-negative")
        self._sleep(seconds)
