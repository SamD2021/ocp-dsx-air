"""Synchronous NVIDIA Air port implementation."""

import math
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any, Protocol, TypeVar
from uuid import UUID

from ocp_dsx_air.adapters.air.jump_host import (
    JumpHostExecutor,
    JumpHostTarget,
    SshJumpHostExecutor,
)
from ocp_dsx_air.adapters.air.mapping import (
    image_create_payload,
    image_to_snapshot,
    simulation_manifest,
    simulation_metadata,
    simulation_to_snapshot,
)
from ocp_dsx_air.adapters.air.transport import (
    DEFAULT_AIR_API_URL,
    DEFAULT_AIR_REQUEST_TIMEOUT,
    AirApiTransport,
)
from ocp_dsx_air.core.contracts import (
    AirImageIntent,
    AirImageSnapshot,
    AirSimulationIntent,
    AirSimulationSnapshot,
    JumpHostSnapshot,
)
from ocp_dsx_air.core.exceptions import AirError, AirImageError, AirSimError, JumpHostError
from ocp_dsx_air.models.runtime import ClusterNetworkConfig

_T = TypeVar("_T")


class _Transport(Protocol):
    def call(self, operation: str, request: Callable[[Any], _T]) -> _T: ...


def _image_id(value: object, *, label: str) -> UUID:
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise AirImageError(f"NVIDIA Air returned an invalid {label} UUID") from exc


def _simulation_id(value: object, *, label: str) -> UUID:
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise AirSimError(f"NVIDIA Air returned an invalid {label} UUID") from exc


def _capacity_number(
    source: object,
    field: str,
    *,
    label: str,
    positive: bool = False,
) -> float:
    value = getattr(source, field, None)
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
        or (positive and value == 0)
    ):
        raise AirSimError(f"NVIDIA Air returned invalid {label} capacity data")
    return float(value)


def _usage_number(usage: object, field: str) -> float:
    if not isinstance(usage, Mapping):
        raise AirSimError("NVIDIA Air returned invalid organization usage data")
    value = usage.get(field)
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise AirSimError("NVIDIA Air returned invalid organization usage data")
    return float(value)


def _format_capacity(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _ensure_capacity(nodes: Sequence[object], budgets: Sequence[object]) -> None:
    if len(budgets) != 1:
        raise AirSimError(
            "NVIDIA Air did not return exactly one organization resource budget"
        )
    if not nodes:
        raise AirSimError("NVIDIA Air returned no nodes for simulation capacity check")

    budget = budgets[0]
    usage = getattr(budget, "usage", None)
    cpu_limit = _capacity_number(budget, "cpu", label="organization CPU")
    memory_limit = _capacity_number(budget, "memory", label="organization memory")
    storage_limit = _capacity_number(
        budget, "disk_storage_total", label="organization storage"
    )
    per_node_storage_limit = _capacity_number(
        budget,
        "disk_storage_per_node",
        label="organization per-node storage",
    )
    cpu_available = max(0.0, cpu_limit - _usage_number(usage, "cpu"))
    memory_available = max(0.0, memory_limit - _usage_number(usage, "memory"))
    storage_available = max(
        0.0, storage_limit - _usage_number(usage, "disk_storage")
    )

    required_cpu = sum(
        _capacity_number(node, "cpu", label="simulation node CPU", positive=True)
        for node in nodes
    )
    required_memory = sum(
        _capacity_number(
            node, "memory", label="simulation node memory", positive=True
        )
        for node in nodes
    )
    node_storage = tuple(
        _capacity_number(
            node, "storage", label="simulation node storage", positive=True
        )
        for node in nodes
    )
    required_storage = sum(node_storage)

    shortages: list[str] = []
    if required_cpu > cpu_available:
        shortages.append(
            f"CPU: {_format_capacity(required_cpu)} cores required, "
            f"{_format_capacity(cpu_available)} available"
        )
    if required_memory > memory_available:
        shortages.append(
            f"memory: {_format_capacity(required_memory / 1024)} GiB required, "
            f"{_format_capacity(memory_available / 1024)} GiB available"
        )
    if required_storage > storage_available:
        shortages.append(
            f"storage: {_format_capacity(required_storage)} GB required, "
            f"{_format_capacity(storage_available)} GB available"
        )
    largest_node_storage = max(node_storage)
    if largest_node_storage > per_node_storage_limit:
        shortages.append(
            "per-node storage: "
            f"{_format_capacity(largest_node_storage)} GB required, "
            f"{_format_capacity(per_node_storage_limit)} GB limit"
        )
    if shortages:
        raise AirSimError(
            "NVIDIA Air cannot start simulation: insufficient organization resources "
            f"({'; '.join(shortages)})"
        )


class NvidiaAirAdapter:
    """Implement managed Air image and simulation operations with the Air SDK."""

    def __init__(
        self,
        *,
        api_key: str,
        api_url: str = DEFAULT_AIR_API_URL,
        ca_bundle: Path | None = None,
        request_timeout: float = DEFAULT_AIR_REQUEST_TIMEOUT,
        upload_workers: int = 4,
        _transport: _Transport | None = None,
        _jump_host_executor: JumpHostExecutor | None = None,
    ) -> None:
        if upload_workers < 1:
            raise AirError("NVIDIA Air upload worker count must be positive")
        self._transport = _transport or AirApiTransport(
            api_key=api_key,
            api_url=api_url,
            ca_bundle=ca_bundle,
            request_timeout=request_timeout,
        )
        self._upload_workers = upload_workers
        self._jump_host_executor = _jump_host_executor or SshJumpHostExecutor()

    def _get_image(
        self,
        image_id: UUID,
        *,
        expected_name: str | None = None,
    ) -> AirImageSnapshot:
        model = self._transport.call(
            "get image",
            lambda api: api.images.get(str(image_id)),
        )
        snapshot = image_to_snapshot(model)
        if expected_name is not None and snapshot.name != expected_name:
            raise AirImageError("NVIDIA Air image name changed during lookup")
        return snapshot

    def find_image(self, name: str) -> AirImageSnapshot | None:
        models = self._transport.call(
            "list images",
            lambda api: list(api.images.list(search=name)),
        )
        try:
            exact = [model for model in models if model.name == name]
        except (TypeError, AttributeError) as exc:
            raise AirImageError("NVIDIA Air returned an invalid image list") from exc
        if not exact:
            return None
        if len(exact) > 1:
            raise AirImageError(
                f"NVIDIA Air returned multiple images named {name!r}"
            )
        return self._get_image(
            _image_id(getattr(exact[0], "id", None), label="image"),
            expected_name=name,
        )

    def create_image(self, intent: AirImageIntent) -> AirImageSnapshot:
        payload = image_create_payload(intent)
        created = self._transport.call(
            "create image",
            lambda api: api.images.create(**payload),
        )
        return self._get_image(
            _image_id(getattr(created, "id", None), label="created image"),
            expected_name=intent.name,
        )

    def upload_image(self, image_id: UUID, source: Path) -> AirImageSnapshot:
        try:
            source_is_safe = (
                not source.is_symlink()
                and source.is_file()
                and source.stat().st_size > 0
            )
        except OSError as exc:
            raise AirImageError("Could not inspect the Air image upload source") from exc
        if not source_is_safe:
            raise AirImageError("Air image upload source must be a non-empty regular file")
        self._transport.call(
            "upload image",
            lambda api: api.images.upload(
                image=str(image_id),
                filepath=source,
                max_workers=self._upload_workers,
            ),
        )
        return self._get_image(image_id)

    def delete_image(self, image_id: UUID) -> None:
        self._transport.call(
            "delete image",
            lambda api: api.images.delete(str(image_id)),
        )

    def _get_simulation(
        self,
        simulation_id: UUID,
        *,
        expected_name: str | None = None,
    ) -> AirSimulationSnapshot:
        def observe(api: Any) -> AirSimulationSnapshot:
            model = api.simulations.get(str(simulation_id))
            nodes = list(model.nodes.list())
            exported = api.simulations.export(
                simulation=model,
                image_ids=True,
                topology_format="JSON",
            )
            return simulation_to_snapshot(
                model,
                nodes,
                exported_topology=exported,
            )

        snapshot = self._transport.call("get simulation", observe)
        if expected_name is not None and snapshot.name != expected_name:
            raise AirSimError("NVIDIA Air simulation name changed during lookup")
        return snapshot

    def find_simulation(self, name: str) -> AirSimulationSnapshot | None:
        models = self._transport.call(
            "list simulations",
            lambda api: list(api.simulations.list(search=name)),
        )
        try:
            exact = [model for model in models if model.name == name]
        except (TypeError, AttributeError) as exc:
            raise AirSimError(
                "NVIDIA Air returned an invalid simulation list"
            ) from exc
        if not exact:
            return None
        if len(exact) > 1:
            raise AirSimError(
                f"NVIDIA Air returned multiple simulations named {name!r}"
            )
        return self._get_simulation(
            _simulation_id(
                getattr(exact[0], "id", None),
                label="simulation",
            ),
            expected_name=name,
        )

    def import_simulation(
        self,
        intent: AirSimulationIntent,
    ) -> AirSimulationSnapshot:
        manifest = simulation_manifest(intent)
        metadata = simulation_metadata(intent)
        created = self._transport.call(
            "import simulation",
            lambda api: api.simulations.import_from_simulation_manifest(
                simulation_manifest=manifest,
                attempt_start=False,
            ),
        )
        simulation_id = _simulation_id(
            getattr(created, "id", None),
            label="created simulation",
        )
        self._transport.call(
            "wait for simulation import",
            lambda api: created.wait_for_state(
                target_states="INACTIVE",
                error_states="INVALID",
                timeout=timedelta(minutes=10),
                poll_interval=timedelta(seconds=2),
            ),
        )
        self._transport.call(
            "claim simulation",
            lambda api: api.simulations.update(
                simulation=str(simulation_id),
                metadata=metadata,
            ),
        )
        return self._get_simulation(
            simulation_id,
            expected_name=intent.name,
        )

    def start_simulation(self, simulation_id: UUID) -> None:
        self._transport.call(
            "start simulation",
            lambda api: api.simulations.start(simulation=str(simulation_id)),
        )

    def ensure_simulation_capacity(self, simulation_id: UUID) -> None:
        def check(api: Any) -> None:
            simulation = api.simulations.get(str(simulation_id))
            nodes = list(simulation.nodes.list())
            budgets = list(api.organizations.list())
            _ensure_capacity(nodes, budgets)

        self._transport.call("check simulation capacity", check)

    def shutdown_simulation(
        self,
        simulation_id: UUID,
        *,
        create_checkpoint: bool,
    ) -> None:
        self._transport.call(
            "shutdown simulation",
            lambda api: api.simulations.shutdown(
                simulation=str(simulation_id),
                create_checkpoint=create_checkpoint,
            ),
        )

    def delete_simulation(self, simulation_id: UUID) -> None:
        self._transport.call(
            "delete simulation",
            lambda api: api.simulations.delete(str(simulation_id)),
        )

    def ensure_jump_host(
        self,
        simulation_id: UUID,
        network: ClusterNetworkConfig,
        *,
        new_password: str,
        timeout_seconds: float,
    ) -> JumpHostSnapshot:
        """Ensure the managed OOB server exposes ready SSH and cluster DNS."""
        if timeout_seconds <= 0:
            raise JumpHostError("Jump-host timeout must be positive")
        created_service: Any | None = None

        def resolve(api: Any) -> tuple[JumpHostSnapshot, JumpHostTarget] | None:
            nonlocal created_service
            simulation = api.simulations.get(str(simulation_id))
            try:
                server = next(
                    node
                    for node in simulation.nodes.list()
                    if node.name == "oob-mgmt-server"
                )
                interface = next(
                    item for item in server.interfaces.list() if item.name == "eth0"
                )
                service = next(
                    (
                        item
                        for item in interface.services.list()
                        if item.node_port == 22
                    ),
                    None,
                )
                if service is None:
                    if created_service is None:
                        created_service = simulation.create_service(
                            name="ocp-dsx-air-ssh",
                            node_name="oob-mgmt-server",
                            interface_name="eth0",
                            node_port=22,
                            service_type="SSH",
                        )
                    else:
                        created_service.refresh()
                    service = created_service
                if service is None:
                    raise JumpHostError(
                        "NVIDIA Air did not return the jump-host service"
                    )
                service_id = _simulation_id(
                    getattr(service, "id", None),
                    label="jump-host service",
                )
                host = service.worker_fqdn
                port = service.worker_port
                image = server.image
                username = getattr(image, "default_username", None) or "ubuntu"
                initial_password = getattr(image, "default_password", None) or "nvidia"
            except (AttributeError, StopIteration, TypeError) as exc:
                raise JumpHostError(
                    "NVIDIA Air returned incomplete jump-host service data"
                ) from exc
            if host is None or port is None:
                return None
            if not isinstance(host, str) or not host.strip():
                raise JumpHostError("NVIDIA Air returned an invalid jump-host address")
            if not isinstance(port, int) or port <= 0:
                raise JumpHostError("NVIDIA Air returned an invalid jump-host port")
            snapshot = JumpHostSnapshot(service_id, host, port, username)
            target = JumpHostTarget(host, port, username, initial_password)
            return snapshot, target

        deadline = time.monotonic() + timeout_seconds
        resolved = self._transport.call("ensure jump-host service", resolve)
        while resolved is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise JumpHostError("Timed out waiting for the jump-host service")
            time.sleep(min(5, remaining))
            resolved = self._transport.call("observe jump-host service", resolve)
        snapshot, target = resolved
        self._jump_host_executor.ensure_ready(
            target,
            network,
            new_password=new_password,
            timeout_seconds=max(1, deadline - time.monotonic()),
        )
        return snapshot
