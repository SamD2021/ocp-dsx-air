"""Synchronous Assisted Installer port implementation."""

import os
import tempfile
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any, Protocol, TypeVar
from urllib.parse import urlparse
from uuid import UUID

from assisted_service_client import models

from ocp_dsx_air.adapters.assisted.mapping import (
    cluster_create_params,
    cluster_to_snapshot,
    host_to_snapshot,
    infraenv_create_params,
    infraenv_to_snapshot,
)
from ocp_dsx_air.adapters.assisted.transport import (
    DEFAULT_ASSISTED_API_URL,
    DEFAULT_OAUTH_TOKEN_URL,
    DEFAULT_REQUEST_TIMEOUT,
    AssistedApiTransport,
)
from ocp_dsx_air.core.contracts import (
    AssistedClusterIntent,
    AssistedClusterSnapshot,
    AssistedHostSnapshot,
    AssistedInfraEnvIntent,
    AssistedInfraEnvSnapshot,
    CredentialPaths,
    OpenShiftNodeRole,
)
from ocp_dsx_air.core.exceptions import AssistedError

_T = TypeVar("_T")


class _Transport(Protocol):
    request_timeout: float

    def call(self, operation: str, request: Callable[[Any], _T]) -> _T: ...

    def open_download(self, operation: str, url: str) -> Any: ...


def _response_uuid(value: object, *, label: str) -> UUID:
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise AssistedError(f"Assisted returned an invalid {label} UUID") from exc


class AssistedInstallerAdapter:
    """Implement Assisted lifecycle operations using the generated client."""

    def __init__(
        self,
        offline_token: str,
        *,
        api_url: str = DEFAULT_ASSISTED_API_URL,
        oauth_token_url: str = DEFAULT_OAUTH_TOKEN_URL,
        ca_bundle: Path | None = None,
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
        _transport: _Transport | None = None,
    ) -> None:
        self._transport = _transport or AssistedApiTransport(
            offline_token=offline_token,
            api_url=api_url,
            oauth_token_url=oauth_token_url,
            ca_bundle=ca_bundle,
            request_timeout=request_timeout,
        )

    def _get_cluster(self, cluster_id: UUID) -> AssistedClusterSnapshot:
        cluster = self._transport.call(
            "get cluster",
            lambda api: api.v2_get_cluster(
                str(cluster_id),
                exclude_hosts=True,
                _request_timeout=self._transport.request_timeout,
            ),
        )
        return cluster_to_snapshot(cluster)

    def find_cluster(self, name: str) -> AssistedClusterSnapshot | None:
        clusters = self._transport.call(
            "list clusters",
            lambda api: api.v2_list_clusters(
                with_hosts=False,
                _request_timeout=self._transport.request_timeout,
            ),
        )
        try:
            exact = [cluster for cluster in clusters if cluster.name == name]
        except (TypeError, AttributeError) as exc:
            raise AssistedError("Assisted returned an invalid cluster list") from exc
        if not exact:
            return None
        if len(exact) > 1:
            raise AssistedError(
                f"Assisted returned multiple clusters named {name!r}"
            )
        return self._get_cluster(
            _response_uuid(getattr(exact[0], "id", None), label="cluster")
        )

    def create_cluster(
        self,
        intent: AssistedClusterIntent,
        *,
        pull_secret: str,
        ssh_public_key: str,
    ) -> AssistedClusterSnapshot:
        params = cluster_create_params(
            intent,
            pull_secret=pull_secret,
            ssh_public_key=ssh_public_key,
        )
        created = self._transport.call(
            "create cluster",
            lambda api: api.v2_register_cluster(
                params,
                _request_timeout=self._transport.request_timeout,
            ),
        )
        cluster_id = _response_uuid(
            getattr(created, "id", None),
            label="created cluster",
        )
        return self._get_cluster(cluster_id)

    def delete_cluster(self, cluster_id: UUID) -> None:
        self._transport.call(
            "delete cluster",
            lambda api: api.v2_deregister_cluster(
                str(cluster_id),
                _request_timeout=self._transport.request_timeout,
            ),
        )

    def _iso_download_url(self, infraenv_id: UUID) -> str:
        presigned = self._transport.call(
            "get InfraEnv image download URL",
            lambda api: api.get_infra_env_download_url(
                str(infraenv_id),
                _request_timeout=self._transport.request_timeout,
            ),
        )
        raw_url = getattr(presigned, "url", None)
        if not isinstance(raw_url, str) or not raw_url.strip():
            raise AssistedError("Assisted returned an invalid InfraEnv download URL")
        parsed = urlparse(raw_url.strip())
        if parsed.scheme != "https" or not parsed.netloc:
            raise AssistedError("Assisted returned an invalid InfraEnv download URL")
        return raw_url.strip()

    def _get_infraenv(self, infraenv_id: UUID) -> AssistedInfraEnvSnapshot:
        infraenv = self._transport.call(
            "get InfraEnv",
            lambda api: api.get_infra_env(
                str(infraenv_id),
                _request_timeout=self._transport.request_timeout,
            ),
        )
        download_url = getattr(infraenv, "download_url", None)
        if download_url is not None and not isinstance(download_url, str):
            raise AssistedError("Assisted returned an invalid InfraEnv download state")
        iso_available = bool(download_url and download_url.strip())
        if iso_available:
            self._iso_download_url(infraenv_id)
        return infraenv_to_snapshot(infraenv, iso_available=iso_available)

    def find_infraenv(self, name: str) -> AssistedInfraEnvSnapshot | None:
        infraenvs = self._transport.call(
            "list InfraEnvs",
            lambda api: api.list_infra_envs(
                _request_timeout=self._transport.request_timeout,
            ),
        )
        try:
            exact = [infraenv for infraenv in infraenvs if infraenv.name == name]
        except (TypeError, AttributeError) as exc:
            raise AssistedError("Assisted returned an invalid InfraEnv list") from exc
        if not exact:
            return None
        if len(exact) > 1:
            raise AssistedError(
                f"Assisted returned multiple InfraEnvs named {name!r}"
            )
        return self._get_infraenv(
            _response_uuid(getattr(exact[0], "id", None), label="InfraEnv")
        )

    def create_infraenv(
        self,
        intent: AssistedInfraEnvIntent,
        *,
        pull_secret: str,
    ) -> AssistedInfraEnvSnapshot:
        params = infraenv_create_params(intent, pull_secret=pull_secret)
        created = self._transport.call(
            "create InfraEnv",
            lambda api: api.register_infra_env(
                params,
                _request_timeout=self._transport.request_timeout,
            ),
        )
        infraenv_id = _response_uuid(
            getattr(created, "id", None),
            label="created InfraEnv",
        )
        return self._get_infraenv(infraenv_id)

    def delete_infraenv(self, infraenv_id: UUID) -> None:
        self._transport.call(
            "delete InfraEnv",
            lambda api: api.deregister_infra_env(
                str(infraenv_id),
                _request_timeout=self._transport.request_timeout,
            ),
        )

    def download_discovery_iso(
        self,
        infraenv_id: UUID,
        destination: Path,
    ) -> Path:
        """Stream a fresh signed discovery ISO into an atomic owner-only file."""
        destination_dir = destination.parent
        if destination_dir.is_symlink():
            raise AssistedError("Discovery ISO destination directory cannot be a symlink")

        temporary_path: Path | None = None
        try:
            destination_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            if destination_dir.is_symlink():
                raise AssistedError(
                    "Discovery ISO destination directory cannot be a symlink"
                )
            if not destination_dir.is_dir():
                raise AssistedError("Discovery ISO destination must be a directory")
            destination_dir.chmod(0o700)

            download_url = self._iso_download_url(infraenv_id)
            descriptor, temporary_name = tempfile.mkstemp(
                dir=destination_dir,
                prefix=f".{destination.name}.",
            )
            temporary_path = Path(temporary_name)
            bytes_written = 0
            with os.fdopen(descriptor, "wb") as staged_file:
                os.fchmod(staged_file.fileno(), 0o600)
                with closing(
                    self._transport.open_download(
                        "download discovery ISO",
                        download_url,
                    )
                ) as response:
                    while chunk := response.read(1024 * 1024):
                        staged_file.write(chunk)
                        bytes_written += len(chunk)
                if bytes_written == 0:
                    raise AssistedError(
                        "Assisted discovery ISO download returned an empty file"
                    )
                staged_file.flush()
                os.fsync(staged_file.fileno())

            os.replace(temporary_path, destination)
            temporary_path = None
            return destination
        except AssistedError:
            raise
        except Exception as exc:
            raise AssistedError("Assisted discovery ISO download failed") from exc
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def start_installation(self, cluster_id: UUID) -> None:
        self._transport.call(
            "start installation",
            lambda api: api.v2_install_cluster(
                str(cluster_id),
                _request_timeout=self._transport.request_timeout,
            ),
        )

    def list_hosts(
        self,
        cluster_id: UUID,
    ) -> tuple[AssistedHostSnapshot, ...]:
        infra_envs = self._transport.call(
            "list cluster infraenvs",
            lambda api: api.list_infra_envs(
                cluster_id=str(cluster_id),
                _request_timeout=self._transport.request_timeout,
            ),
        )
        try:
            infra_env_ids = [
                _response_uuid(getattr(infra_env, "id", None), label="infraenv")
                for infra_env in infra_envs
            ]
        except TypeError as exc:
            raise AssistedError("Assisted returned an invalid infraenv list") from exc

        snapshots: dict[UUID, AssistedHostSnapshot] = {}
        for infra_env_id in infra_env_ids:
            hosts = self._transport.call(
                "list infraenv hosts",
                lambda api, infra_env_id=infra_env_id: api.v2_list_hosts(
                    str(infra_env_id),
                    _request_timeout=self._transport.request_timeout,
                ),
            )
            try:
                host_iterator = iter(hosts)
            except TypeError as exc:
                raise AssistedError("Assisted returned an invalid host list") from exc
            for host in host_iterator:
                observed_cluster_id = getattr(host, "cluster_id", None)
                if observed_cluster_id is not None and _response_uuid(
                    observed_cluster_id,
                    label="host cluster",
                ) != cluster_id:
                    raise AssistedError(
                        "Assisted returned conflicting host cluster membership"
                    )
                snapshot = host_to_snapshot(host, infraenv_id=infra_env_id)
                existing = snapshots.get(snapshot.id)
                if existing is not None and existing != snapshot:
                    raise AssistedError(
                        f"Assisted returned conflicting observations for host "
                        f"{snapshot.id}"
                    )
                snapshots[snapshot.id] = snapshot

        return tuple(snapshots[host_id] for host_id in sorted(snapshots, key=str))

    def update_host_role(
        self,
        infraenv_id: UUID,
        host_id: UUID,
        role: OpenShiftNodeRole,
    ) -> AssistedHostSnapshot:
        if role is OpenShiftNodeRole.UNKNOWN:
            raise AssistedError("Cannot assign an unknown OpenShift host role")
        host = self._transport.call(
            "update host role",
            lambda api: api.v2_update_host(
                str(infraenv_id),
                str(host_id),
                models.HostUpdateParams(host_role=role.value),
                _request_timeout=self._transport.request_timeout,
            ),
        )
        return host_to_snapshot(host, infraenv_id=infraenv_id)

    def _stage_credential(
        self,
        cluster_id: UUID,
        destination_dir: Path,
        file_name: str,
    ) -> Path:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=destination_dir,
            prefix=f".{file_name}.",
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as staged_file:
                with closing(
                    self._transport.call(
                        "download credentials",
                        lambda api: api.v2_download_cluster_credentials(
                            str(cluster_id),
                            file_name,
                            _preload_content=False,
                            _request_timeout=self._transport.request_timeout,
                        ),
                    )
                ) as response:
                    while chunk := response.read(1024 * 1024):
                        staged_file.write(chunk)
                os.fchmod(staged_file.fileno(), 0o600)
                staged_file.flush()
                os.fsync(staged_file.fileno())
            return temporary_path
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise

    def download_credentials(
        self,
        cluster_id: UUID,
        destination_dir: Path,
    ) -> CredentialPaths:
        """Stage both credentials before atomically replacing owner-only files."""
        if destination_dir.is_symlink():
            raise AssistedError("Credential destination directory cannot be a symlink")

        staged_paths: list[Path] = []
        try:
            destination_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            if destination_dir.is_symlink():
                raise AssistedError(
                    "Credential destination directory cannot be a symlink"
                )
            if not destination_dir.is_dir():
                raise AssistedError("Credential destination must be a directory")
            destination_dir.chmod(0o700)

            kubeconfig_stage = self._stage_credential(
                cluster_id,
                destination_dir,
                "kubeconfig",
            )
            staged_paths.append(kubeconfig_stage)
            password_stage = self._stage_credential(
                cluster_id,
                destination_dir,
                "kubeadmin-password",
            )
            staged_paths.append(password_stage)

            kubeconfig = destination_dir / "kubeconfig"
            kubeadmin_password = destination_dir / "kubeadmin-password"
            os.replace(kubeconfig_stage, kubeconfig)
            os.replace(password_stage, kubeadmin_password)
            kubeconfig.chmod(0o600)
            kubeadmin_password.chmod(0o600)
            if not kubeconfig.is_file() or not kubeadmin_password.is_file():
                raise AssistedError("Assisted credential replacement did not complete")
            return CredentialPaths(
                kubeconfig=kubeconfig,
                kubeadmin_password=kubeadmin_password,
            )
        except AssistedError:
            raise
        except (OSError, TypeError, AttributeError) as exc:
            raise AssistedError("Assisted credentials download failed") from exc
        finally:
            for staged_path in staged_paths:
                staged_path.unlink(missing_ok=True)
