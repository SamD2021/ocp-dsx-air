import hashlib
import json
import stat
import subprocess
from pathlib import Path

import pytest

from ocp_dsx_air.adapters.air.artifacts import (
    blank_disk_air_image_name,
    blank_disk_path,
    ensure_blank_disk,
)
from ocp_dsx_air.core.contracts import BlankDiskIntent, CpuArchitecture
from ocp_dsx_air.core.exceptions import AirImageError, DependencyError


class FakeQemuImg:
    def __init__(
        self,
        *,
        content: bytes = b"qcow2 blank disk",
        fail_create: bool = False,
        malformed_info: bool = False,
    ) -> None:
        self.content = content
        self.fail_create = fail_create
        self.malformed_info = malformed_info
        self.calls: list[list[str]] = []

    def __call__(self, command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        self.calls.append(command)
        if command[1] == "create":
            if self.fail_create:
                raise subprocess.CalledProcessError(1, command, stderr="private backend detail")
            Path(command[-2]).write_bytes(self.content)
            return subprocess.CompletedProcess(command, 0, "", "")

        if self.malformed_info:
            output = "not-json"
        else:
            size_gib = int(Path(command[-1]).parent.parent.name.removesuffix("g"))
            output = json.dumps(
                {
                    "format": "qcow2",
                    "virtual-size": size_gib * 1024**3,
                }
            )
        return subprocess.CompletedProcess(command, 0, output, "")


def _intent() -> BlankDiskIntent:
    return BlankDiskIntent(
        architecture=CpuArchitecture.X86_64,
        virtual_size_gib=100,
    )


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_blank_disk_identity_is_deterministic(tmp_path: Path) -> None:
    destination = blank_disk_path(tmp_path, _intent())

    assert destination == (
        tmp_path / "air/images/blank/x86_64/100g/qcow2-v1/disk.qcow2"
    )
    assert blank_disk_air_image_name(
        _intent(),
        "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    ) == "ocp-dsx-air-blank-x86_64-100g-qcow2-v1-0123456789ab"


def test_blank_disk_is_created_and_verified_atomically(tmp_path: Path) -> None:
    qemu = FakeQemuImg()

    artifact = ensure_blank_disk(tmp_path, _intent(), _run=qemu)

    assert artifact.path == blank_disk_path(tmp_path, _intent())
    assert artifact.path.read_bytes() == qemu.content
    assert artifact.size_bytes == len(qemu.content)
    assert artifact.sha256 == hashlib.sha256(qemu.content).hexdigest()
    assert _mode(artifact.path.parent) == 0o700
    assert _mode(artifact.path) == 0o600
    assert [call[1] for call in qemu.calls] == ["create", "info"]
    assert list(artifact.path.parent.glob(".disk.qcow2.*")) == []


def test_valid_cached_blank_disk_is_reused(tmp_path: Path) -> None:
    destination = blank_disk_path(tmp_path, _intent())
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"existing qcow2")
    qemu = FakeQemuImg()

    artifact = ensure_blank_disk(tmp_path, _intent(), _run=qemu)

    assert artifact.path.read_bytes() == b"existing qcow2"
    assert [call[1] for call in qemu.calls] == ["info"]
    assert _mode(destination.parent) == 0o700
    assert _mode(destination) == 0o600


def test_owned_cached_blank_disk_does_not_require_qemu_img(tmp_path: Path) -> None:
    qemu = FakeQemuImg()
    first = ensure_blank_disk(tmp_path, _intent(), _run=qemu)
    calls_after_creation = len(qemu.calls)

    second = ensure_blank_disk(
        tmp_path,
        _intent(),
        _run=lambda *args, **kwargs: (_ for _ in ()).throw(
            FileNotFoundError("qemu-img")
        ),
    )

    assert second == first
    assert len(qemu.calls) == calls_after_creation


def test_creation_failure_preserves_existing_file_and_cleans_stage(
    tmp_path: Path,
) -> None:
    destination = blank_disk_path(tmp_path, _intent())
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"invalid old file")
    qemu = FakeQemuImg(fail_create=True, malformed_info=True)

    with pytest.raises(AirImageError, match="create blank disk") as failure:
        ensure_blank_disk(tmp_path, _intent(), _run=qemu)

    assert "private backend detail" not in str(failure.value)
    assert destination.read_bytes() == b"invalid old file"
    assert list(destination.parent.glob(".disk.qcow2.*")) == []


def test_blank_disk_refuses_symlink_destination_directory(tmp_path: Path) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    destination = blank_disk_path(tmp_path, _intent())
    destination.parent.parent.mkdir(parents=True)
    destination.parent.symlink_to(actual, target_is_directory=True)

    with pytest.raises(AirImageError, match="symlink"):
        ensure_blank_disk(tmp_path, _intent(), _run=FakeQemuImg())

    assert list(actual.iterdir()) == []


def test_blank_disk_rejects_unknown_architecture(tmp_path: Path) -> None:
    intent = BlankDiskIntent(
        architecture=CpuArchitecture.UNKNOWN,
        virtual_size_gib=100,
    )

    with pytest.raises(AirImageError, match="architecture"):
        ensure_blank_disk(tmp_path, intent, _run=FakeQemuImg())


def test_missing_qemu_img_is_reported_as_dependency_error(tmp_path: Path) -> None:
    def missing(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del command, kwargs
        raise FileNotFoundError("qemu-img")

    with pytest.raises(DependencyError, match="qemu-img"):
        ensure_blank_disk(tmp_path, _intent(), _run=missing)
