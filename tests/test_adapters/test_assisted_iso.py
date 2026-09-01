import os
import stat
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

from ocp_dsx_air.adapters.assisted.adapter import AssistedInstallerAdapter
from ocp_dsx_air.core.exceptions import AssistedError

INFRAENV_ID = UUID("7a0ddc45-ce1a-4d8d-ab9f-0be5fbe98d27")
PRESIGNED_URL = "https://images.example.test/discovery.iso?signature=secret"


class FakeResponse:
    def __init__(
        self,
        content: bytes,
        *,
        failure_after_reads: int | None = None,
    ) -> None:
        self._content = content
        self._offset = 0
        self._reads = 0
        self._failure_after_reads = failure_after_reads
        self.closed = False

    def read(self, size: int) -> bytes:
        if (
            self._failure_after_reads is not None
            and self._reads >= self._failure_after_reads
        ):
            raise OSError("stream interrupted with secret response details")
        self._reads += 1
        chunk = self._content[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk

    def close(self) -> None:
        self.closed = True


class IsoApi:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def get_infra_env_download_url(
        self,
        infraenv_id: str,
        **kwargs: object,
    ) -> SimpleNamespace:
        self.calls.append(("get_download_url", (infraenv_id,), kwargs))
        return SimpleNamespace(url=PRESIGNED_URL)


class IsoTransport:
    request_timeout = 7.5

    def __init__(self, response: FakeResponse | Exception) -> None:
        self.api = IsoApi()
        self.response = response
        self.opened_urls: list[str] = []

    def call(self, operation: str, request: Any) -> Any:
        del operation
        return request(self.api)

    def open_download(self, operation: str, url: str) -> FakeResponse:
        del operation
        self.opened_urls.append(url)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _adapter(transport: IsoTransport) -> AssistedInstallerAdapter:
    return AssistedInstallerAdapter(offline_token="offline", _transport=transport)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_download_discovery_iso_atomically_writes_owner_only_file(
    tmp_path: Path,
) -> None:
    response = FakeResponse(b"discovery-iso-bytes")
    transport = IsoTransport(response)
    destination = tmp_path / "cache" / "discovery.iso"

    result = _adapter(transport).download_discovery_iso(
        INFRAENV_ID,
        destination,
    )

    assert result == destination
    assert destination.read_bytes() == b"discovery-iso-bytes"
    assert _mode(destination.parent) == 0o700
    assert _mode(destination) == 0o600
    assert response.closed is True
    assert transport.opened_urls == [PRESIGNED_URL]
    assert transport.api.calls == [
        (
            "get_download_url",
            (str(INFRAENV_ID),),
            {"_request_timeout": 7.5},
        )
    ]
    assert list(destination.parent.glob(".discovery.iso.*")) == []


def test_download_discovery_iso_enforces_modes_and_replaces_prior_file(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "cache" / "discovery.iso"
    destination.parent.mkdir()
    destination.write_bytes(b"old iso")
    os.chmod(destination.parent, 0o755)
    os.chmod(destination, 0o644)

    _adapter(IsoTransport(FakeResponse(b"new iso"))).download_discovery_iso(
        INFRAENV_ID,
        destination,
    )

    assert destination.read_bytes() == b"new iso"
    assert _mode(destination.parent) == 0o700
    assert _mode(destination) == 0o600


@pytest.mark.parametrize("failure_kind", ["open", "stream", "empty"])
def test_download_failure_preserves_prior_iso_and_cleans_temporary_file(
    tmp_path: Path,
    failure_kind: str,
) -> None:
    destination = tmp_path / "cache" / "discovery.iso"
    destination.parent.mkdir()
    destination.write_bytes(b"old iso")
    response: FakeResponse | Exception
    if failure_kind == "open":
        response = AssistedError("Assisted ISO download failed (HTTP 503)")
    elif failure_kind == "stream":
        response = FakeResponse(b"partial", failure_after_reads=1)
    else:
        response = FakeResponse(b"")

    with pytest.raises(AssistedError, match="ISO"):
        _adapter(IsoTransport(response)).download_discovery_iso(
            INFRAENV_ID,
            destination,
        )

    assert destination.read_bytes() == b"old iso"
    assert list(destination.parent.glob(".discovery.iso.*")) == []
    if isinstance(response, FakeResponse):
        assert response.closed is True


def test_download_discovery_iso_refuses_symlink_destination_directory(
    tmp_path: Path,
) -> None:
    actual_directory = tmp_path / "actual"
    actual_directory.mkdir()
    linked_directory = tmp_path / "linked"
    linked_directory.symlink_to(actual_directory, target_is_directory=True)

    with pytest.raises(AssistedError, match="symlink"):
        _adapter(IsoTransport(FakeResponse(b"iso"))).download_discovery_iso(
            INFRAENV_ID,
            linked_directory / "discovery.iso",
        )

    assert list(actual_directory.iterdir()) == []
