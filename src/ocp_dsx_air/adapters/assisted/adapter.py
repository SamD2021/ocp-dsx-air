"""Synchronous Assisted Installer port implementation."""

from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any, Protocol, TypeVar
from uuid import UUID

from ocp_dsx_air.adapters.assisted.mapping import (
    cluster_create_params,
    cluster_to_snapshot,
    host_to_snapshot,
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
)
from ocp_dsx_air.core.exceptions import AssistedError

_T = TypeVar("_T")


class _Transport(Protocol):
    request_timeout: float

    def call(self, operation: str, request: Callable[[Any], _T]) -> _T: ...


def _response_uuid(value: object, *, label: str) -> UUID:
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise AssistedError(f"Assisted returned an invalid {label} UUID") from exc


class AssistedInstallerAdapter:
    """Implement cluster lifecycle operations using the generated client directly."""

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
                snapshot = host_to_snapshot(host)
                existing = snapshots.get(snapshot.id)
                if existing is not None and existing != snapshot:
                    raise AssistedError(
                        f"Assisted returned conflicting observations for host "
                        f"{snapshot.id}"
                    )
                snapshots[snapshot.id] = snapshot

        return tuple(snapshots[host_id] for host_id in sorted(snapshots, key=str))

