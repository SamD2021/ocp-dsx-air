from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

from ocp_dsx_air.adapters.air.adapter import NvidiaAirAdapter
from ocp_dsx_air.adapters.air.transport import AirApiTransport
from ocp_dsx_air.core.contracts import (
    AirImageIntent,
    AirImagePurpose,
    AirImageUploadStatus,
    CpuArchitecture,
)
from ocp_dsx_air.core.exceptions import AirImageError

IMAGE_ID = UUID("62f3d8c7-7257-4ce4-a94a-cdcf052ccf3f")
IMAGE_NAME = "ocp-dsx-air-discovery-7a0ddc45"
SHA256 = "c5f67d4563c93f8080b9f11f80fa3a152c958232ec2e747d23a75b669afc3ce9"


def _image_model(**changes: object) -> SimpleNamespace:
    fields: dict[str, object] = {
        "id": str(IMAGE_ID),
        "name": IMAGE_NAME,
        "version": "1",
        "cpu_arch": "x86",
        "provider": "VM",
        "upload_status": "COMPLETE",
        "size": 4096,
        "hash": SHA256,
        "is_owned_by_client": True,
    }
    fields.update(changes)
    return SimpleNamespace(**fields)


def _intent(**changes: object) -> AirImageIntent:
    fields: dict[str, object] = {
        "name": IMAGE_NAME,
        "purpose": AirImagePurpose.DISCOVERY_ISO,
        "version": "1",
        "architecture": CpuArchitecture.X86_64,
        "provider": "VM",
        "source_size_bytes": 4096,
        "source_sha256": SHA256,
    }
    fields.update(changes)
    return AirImageIntent(**fields)  # type: ignore[arg-type]


class FakeImages:
    def __init__(self) -> None:
        self.list_result: object = []
        self.get_result: object = _image_model()
        self.create_result: object = SimpleNamespace(id=str(IMAGE_ID))
        self.upload_result: object = _image_model(upload_status="UPLOADING")
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def list(self, **kwargs: object) -> Any:
        self.calls.append(("list", (), kwargs))
        return self.list_result

    def get(self, image_id: str) -> Any:
        self.calls.append(("get", (image_id,), {}))
        return self.get_result

    def create(self, **kwargs: object) -> Any:
        self.calls.append(("create", (), kwargs))
        return self.create_result

    def upload(self, **kwargs: object) -> Any:
        self.calls.append(("upload", (), kwargs))
        return self.upload_result

    def delete(self, image_id: str) -> None:
        self.calls.append(("delete", (image_id,), {}))


def _adapter(images: FakeImages) -> NvidiaAirAdapter:
    api = SimpleNamespace(images=images, client=SimpleNamespace())
    transport = AirApiTransport(
        api_key="nvapi-secret",
        _api_factory=lambda **kwargs: api,
    )
    return NvidiaAirAdapter(api_key="nvapi-secret", _transport=transport)


def test_find_image_returns_none_when_exact_name_is_absent() -> None:
    images = FakeImages()
    images.list_result = [_image_model(name="similar-image")]

    result = _adapter(images).find_image(IMAGE_NAME)

    assert result is None
    assert images.calls == [("list", (), {"search": IMAGE_NAME})]


def test_find_image_refetches_and_normalizes_exact_match() -> None:
    images = FakeImages()
    images.list_result = [
        _image_model(name="similar-image"),
        SimpleNamespace(id=str(IMAGE_ID), name=IMAGE_NAME),
    ]

    result = _adapter(images).find_image(IMAGE_NAME)

    assert result is not None
    assert result.id == IMAGE_ID
    assert result.name == IMAGE_NAME
    assert result.architecture is CpuArchitecture.X86_64
    assert result.upload_status is AirImageUploadStatus.COMPLETE
    assert result.size_bytes == 4096
    assert result.sha256 == SHA256
    assert result.owned_by_client is True
    assert images.calls == [
        ("list", (), {"search": IMAGE_NAME}),
        ("get", (str(IMAGE_ID),), {}),
    ]


def test_find_image_rejects_duplicate_exact_names() -> None:
    images = FakeImages()
    images.list_result = [
        _image_model(),
        _image_model(id=str(UUID(int=2))),
    ]

    with pytest.raises(AirImageError, match=r"multiple.*named"):
        _adapter(images).find_image(IMAGE_NAME)


@pytest.mark.parametrize(
    ("changes", "expected_architecture", "expected_status"),
    [
        ({"cpu_arch": "ARM"}, CpuArchitecture.ARM64, AirImageUploadStatus.COMPLETE),
        (
            {"cpu_arch": "future-arch"},
            CpuArchitecture.UNKNOWN,
            AirImageUploadStatus.COMPLETE,
        ),
        (
            {"upload_status": "FUTURE"},
            CpuArchitecture.X86_64,
            AirImageUploadStatus.UNKNOWN,
        ),
    ],
)
def test_find_image_preserves_unknown_vendor_values(
    changes: dict[str, object],
    expected_architecture: CpuArchitecture,
    expected_status: AirImageUploadStatus,
) -> None:
    images = FakeImages()
    images.list_result = [_image_model()]
    images.get_result = _image_model(**changes)

    result = _adapter(images).find_image(IMAGE_NAME)

    assert result is not None
    assert result.architecture is expected_architecture
    assert result.upload_status is expected_status


@pytest.mark.parametrize(
    "changes",
    [
        {"id": "not-a-uuid"},
        {"name": ""},
        {"version": None},
        {"provider": ""},
        {"size": -1},
        {"size": 0},
        {"hash": None},
        {"hash": "not-a-sha256"},
        {"is_owned_by_client": 1},
    ],
)
def test_find_image_rejects_malformed_models(changes: dict[str, object]) -> None:
    images = FakeImages()
    images.list_result = [_image_model()]
    images.get_result = _image_model(**changes)

    with pytest.raises(AirImageError, match="invalid Air image"):
        _adapter(images).find_image(IMAGE_NAME)


def test_create_image_sends_explicit_safe_metadata_and_refetches() -> None:
    images = FakeImages()

    result = _adapter(images).create_image(_intent())

    assert result.id == IMAGE_ID
    assert images.calls == [
        (
            "create",
            (),
            {
                "name": IMAGE_NAME,
                "version": "1",
                "default_username": "core",
                "default_password": "not-used",
                "cpu_arch": "x86",
                "provider": "VM",
            },
        ),
        ("get", (str(IMAGE_ID),), {}),
    ]


@pytest.mark.parametrize(
    "changes",
    [
        {"name": ""},
        {"version": ""},
        {"architecture": CpuArchitecture.UNKNOWN},
        {"provider": ""},
        {"source_size_bytes": 0},
        {"source_sha256": "not-a-sha256"},
    ],
)
def test_create_image_rejects_invalid_intent(changes: dict[str, object]) -> None:
    with pytest.raises(AirImageError, match="Cannot create"):
        _adapter(FakeImages()).create_image(_intent(**changes))


def test_upload_image_uses_uuid_path_then_refetches(tmp_path: Path) -> None:
    source = tmp_path / "discovery.iso"
    source.write_bytes(b"iso")
    images = FakeImages()

    result = _adapter(images).upload_image(IMAGE_ID, source)

    assert result.id == IMAGE_ID
    assert images.calls == [
        (
            "upload",
            (),
            {"image": str(IMAGE_ID), "filepath": source, "max_workers": 4},
        ),
        ("get", (str(IMAGE_ID),), {}),
    ]


@pytest.mark.parametrize("kind", ["missing", "empty", "symlink"])
def test_upload_image_rejects_unsafe_source(tmp_path: Path, kind: str) -> None:
    source = tmp_path / "image"
    if kind == "empty":
        source.touch()
    elif kind == "symlink":
        target = tmp_path / "target"
        target.write_bytes(b"image")
        source.symlink_to(target)

    with pytest.raises(AirImageError, match="source"):
        _adapter(FakeImages()).upload_image(IMAGE_ID, source)


def test_delete_image_uses_uuid() -> None:
    images = FakeImages()

    _adapter(images).delete_image(IMAGE_ID)

    assert images.calls == [("delete", (str(IMAGE_ID),), {})]
