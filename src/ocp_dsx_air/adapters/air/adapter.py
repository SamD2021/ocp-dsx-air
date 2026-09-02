"""Synchronous NVIDIA Air port implementation."""

from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, TypeVar
from uuid import UUID

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
)
from ocp_dsx_air.core.exceptions import AirError, AirImageError, AirSimError

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
            return simulation_to_snapshot(model, nodes)

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
