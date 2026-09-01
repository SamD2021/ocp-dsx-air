import os
import stat
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from ocp_dsx_air.adapters.assisted import AssistedInstallerAdapter
from ocp_dsx_air.core.exceptions import AssistedError

CLUSTER_ID = UUID("5ad7357e-6c65-46e2-bad8-cd796cc82070")


class FakeResponse:
    def __init__(self, content: bytes, *, failure: Exception | None = None) -> None:
        self._content = content
        self._offset = 0
        self._failure = failure
        self.closed = False

    def read(self, size: int) -> bytes:
        if self._failure is not None:
            raise self._failure
        chunk = self._content[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk

    def close(self) -> None:
        self.closed = True


class CredentialApi:
    def __init__(self) -> None:
        self.responses: dict[str, FakeResponse | Exception] = {
            "kubeconfig": FakeResponse(b"apiVersion: v1\n"),
            "kubeadmin-password": FakeResponse(b"correct horse battery staple\n"),
        }
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def v2_download_cluster_credentials(
        self,
        cluster_id: str,
        file_name: str,
        **kwargs: object,
    ) -> FakeResponse:
        self.calls.append((cluster_id, file_name, kwargs))
        response = self.responses[file_name]
        if isinstance(response, Exception):
            raise response
        return response


class FakeTransport:
    request_timeout = 4.0

    def __init__(self, api: CredentialApi) -> None:
        self.api = api

    def call(self, operation: str, request: Any) -> Any:
        try:
            return request(self.api)
        except AssistedError:
            raise
        except Exception as exc:
            raise AssistedError(f"Assisted {operation} failed") from exc


def _adapter(api: CredentialApi) -> AssistedInstallerAdapter:
    return AssistedInstallerAdapter(
        offline_token="offline",
        _transport=FakeTransport(api),
    )


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_download_credentials_writes_conventional_owner_only_paths(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "auth"
    api = CredentialApi()

    paths = _adapter(api).download_credentials(CLUSTER_ID, destination)

    assert paths.kubeconfig == destination / "kubeconfig"
    assert paths.kubeadmin_password == destination / "kubeadmin-password"
    assert paths.kubeconfig.read_bytes() == b"apiVersion: v1\n"
    assert paths.kubeadmin_password.read_bytes() == (
        b"correct horse battery staple\n"
    )
    assert _mode(destination) == 0o700
    assert _mode(paths.kubeconfig) == 0o600
    assert _mode(paths.kubeadmin_password) == 0o600
    assert [call[1] for call in api.calls] == [
        "kubeconfig",
        "kubeadmin-password",
    ]
    assert all(
        call[2] == {"_preload_content": False, "_request_timeout": 4.0}
        for call in api.calls
    )


def test_download_credentials_enforces_modes_and_replaces_prior_files(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "auth"
    destination.mkdir(mode=0o777)
    kubeconfig = destination / "kubeconfig"
    password = destination / "kubeadmin-password"
    kubeconfig.write_bytes(b"old kubeconfig")
    password.write_bytes(b"old password")
    os.chmod(kubeconfig, 0o666)
    os.chmod(password, 0o666)

    paths = _adapter(CredentialApi()).download_credentials(CLUSTER_ID, destination)

    assert paths.kubeconfig.read_bytes() == b"apiVersion: v1\n"
    assert paths.kubeadmin_password.read_bytes().startswith(b"correct horse")
    assert _mode(destination) == 0o700
    assert _mode(paths.kubeconfig) == 0o600
    assert _mode(paths.kubeadmin_password) == 0o600


def test_second_download_failure_does_not_replace_either_existing_file(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "auth"
    destination.mkdir()
    kubeconfig = destination / "kubeconfig"
    password = destination / "kubeadmin-password"
    kubeconfig.write_bytes(b"old kubeconfig")
    password.write_bytes(b"old password")
    api = CredentialApi()
    api.responses["kubeadmin-password"] = OSError("download failed")

    with pytest.raises(AssistedError, match="credentials"):
        _adapter(api).download_credentials(CLUSTER_ID, destination)

    assert kubeconfig.read_bytes() == b"old kubeconfig"
    assert password.read_bytes() == b"old password"
    assert sorted(path.name for path in destination.iterdir()) == [
        "kubeadmin-password",
        "kubeconfig",
    ]


def test_read_failure_closes_response_and_cleans_temporary_files(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "auth"
    api = CredentialApi()
    failing_response = FakeResponse(b"", failure=OSError("stream failed"))
    api.responses["kubeconfig"] = failing_response

    with pytest.raises(AssistedError, match="credentials"):
        _adapter(api).download_credentials(CLUSTER_ID, destination)

    assert failing_response.closed is True
    assert list(destination.iterdir()) == []


def test_download_credentials_refuses_symlink_destination(tmp_path: Path) -> None:
    real_destination = tmp_path / "real-auth"
    real_destination.mkdir()
    symlink = tmp_path / "auth-link"
    symlink.symlink_to(real_destination, target_is_directory=True)

    with pytest.raises(AssistedError, match="symlink"):
        _adapter(CredentialApi()).download_credentials(CLUSTER_ID, symlink)

    assert list(real_destination.iterdir()) == []

