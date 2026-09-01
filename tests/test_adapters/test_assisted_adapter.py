from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

from ocp_dsx_air.adapters.assisted.adapter import AssistedInstallerAdapter
from ocp_dsx_air.core.contracts import AssistedClusterIntent, CpuArchitecture
from ocp_dsx_air.core.exceptions import AssistedError

CLUSTER_ID = UUID("5ad7357e-6c65-46e2-bad8-cd796cc82070")
OTHER_CLUSTER_ID = UUID("f926699c-77d9-472e-aa40-ccf70e98681b")
HOST_A_ID = UUID("18b86b2e-46a7-43af-8de8-1a482cd68eb6")
HOST_B_ID = UUID("ae1012ef-c9ad-4595-aa78-63c3a553118b")
ENV_A_ID = UUID("7a0ddc45-ce1a-4d8d-ab9f-0be5fbe98d27")
ENV_B_ID = UUID("2b315554-8cf5-4e65-96b8-7478942e65d7")


def _cluster(
    cluster_id: UUID = CLUSTER_ID,
    *,
    name: str = "ocp",
    status: str = "ready",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=str(cluster_id),
        name=name,
        status=status,
        status_info="Ready to install",
        openshift_version="4.19",
        base_dns_domain="dsx.air.local",
        cpu_architecture="x86_64",
        ntp_sources="",
        high_availability_mode="Full",
        control_plane_count=3,
        user_managed_networking=False,
        machine_networks=[],
        api_vips=[],
        ingress_vips=[],
        install_started_at=None,
        install_completed_at=None,
    )


def _host(
    host_id: UUID,
    *,
    cluster_id: UUID | None = CLUSTER_ID,
    status: str = "known",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=str(host_id),
        cluster_id=str(cluster_id) if cluster_id is not None else None,
        requested_hostname=f"host-{str(host_id)[:8]}",
        status=status,
        status_info="",
        role="master",
        inventory=None,
        progress=None,
    )


def _intent() -> AssistedClusterIntent:
    return AssistedClusterIntent(
        name="ocp",
        ocp_version="4.19",
        base_dns_domain="dsx.air.local",
        architecture=CpuArchitecture.X86_64,
        ntp_sources=(),
        high_availability=True,
        control_plane_count=3,
        user_managed_networking=False,
        machine_networks=(),
        api_vips=(),
        ingress_vips=(),
    )


class FakeApi:
    def __init__(self) -> None:
        self.clusters: list[SimpleNamespace] = []
        self.cluster_details: dict[str, SimpleNamespace] = {}
        self.created = _cluster()
        self.infra_envs: list[SimpleNamespace] = []
        self.hosts: dict[str, list[SimpleNamespace]] = {}
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def v2_list_clusters(self, **kwargs: object) -> list[SimpleNamespace]:
        self.calls.append(("list_clusters", (), kwargs))
        return self.clusters

    def v2_get_cluster(
        self, cluster_id: str, **kwargs: object
    ) -> SimpleNamespace:
        self.calls.append(("get_cluster", (cluster_id,), kwargs))
        return self.cluster_details[cluster_id]

    def v2_register_cluster(
        self, params: object, **kwargs: object
    ) -> SimpleNamespace:
        self.calls.append(("register_cluster", (params,), kwargs))
        return self.created

    def v2_deregister_cluster(self, cluster_id: str, **kwargs: object) -> None:
        self.calls.append(("delete_cluster", (cluster_id,), kwargs))

    def v2_install_cluster(self, cluster_id: str, **kwargs: object) -> None:
        self.calls.append(("install_cluster", (cluster_id,), kwargs))

    def list_infra_envs(self, **kwargs: object) -> list[SimpleNamespace]:
        self.calls.append(("list_infra_envs", (), kwargs))
        return self.infra_envs

    def v2_list_hosts(
        self, infra_env_id: str, **kwargs: object
    ) -> list[SimpleNamespace]:
        self.calls.append(("list_hosts", (infra_env_id,), kwargs))
        return self.hosts[infra_env_id]


class FakeTransport:
    request_timeout = 7.5

    def __init__(self, api: FakeApi) -> None:
        self.api = api
        self.operations: list[str] = []
        self.failure: AssistedError | None = None

    def call(self, operation: str, request: Any) -> Any:
        self.operations.append(operation)
        if self.failure is not None:
            raise self.failure
        return request(self.api)


def _adapter(api: FakeApi) -> tuple[AssistedInstallerAdapter, FakeTransport]:
    transport = FakeTransport(api)
    return (
        AssistedInstallerAdapter(offline_token="offline", _transport=transport),
        transport,
    )


def test_find_cluster_returns_none_without_exact_name_match() -> None:
    api = FakeApi()
    api.clusters = [_cluster(name="ocp-other")]
    adapter, _ = _adapter(api)

    assert adapter.find_cluster("ocp") is None
    assert api.calls == [
        ("list_clusters", (), {"with_hosts": False, "_request_timeout": 7.5})
    ]


def test_find_cluster_refetches_one_exact_match_by_uuid() -> None:
    api = FakeApi()
    api.clusters = [_cluster(status="insufficient")]
    api.cluster_details[str(CLUSTER_ID)] = _cluster(status="ready")
    adapter, _ = _adapter(api)

    snapshot = adapter.find_cluster("ocp")

    assert snapshot is not None
    assert snapshot.id == CLUSTER_ID
    assert snapshot.status.value == "ready"
    assert api.calls[-1] == (
        "get_cluster",
        (str(CLUSTER_ID),),
        {"exclude_hosts": True, "_request_timeout": 7.5},
    )


def test_find_cluster_rejects_duplicate_exact_names() -> None:
    api = FakeApi()
    api.clusters = [_cluster(), _cluster(OTHER_CLUSTER_ID)]
    adapter, _ = _adapter(api)

    with pytest.raises(AssistedError, match=r"multiple.*ocp"):
        adapter.find_cluster("ocp")


def test_create_cluster_sends_payload_and_refetches_created_uuid() -> None:
    api = FakeApi()
    api.cluster_details[str(CLUSTER_ID)] = _cluster(status="pending-for-input")
    adapter, _ = _adapter(api)

    snapshot = adapter.create_cluster(
        _intent(),
        pull_secret="pull-secret",
        ssh_public_key="ssh-ed25519 public-key",
    )

    assert snapshot.id == CLUSTER_ID
    register_call = api.calls[0]
    assert register_call[0] == "register_cluster"
    payload: Any = register_call[1][0]
    assert payload.pull_secret == "pull-secret"
    assert payload.ssh_public_key == "ssh-ed25519 public-key"
    assert register_call[2] == {"_request_timeout": 7.5}
    assert api.calls[1][0] == "get_cluster"


def test_create_cluster_propagates_translated_conflict() -> None:
    api = FakeApi()
    adapter, transport = _adapter(api)
    transport.failure = AssistedError("Assisted create cluster failed (HTTP 409)")

    with pytest.raises(AssistedError, match="409"):
        adapter.create_cluster(
            _intent(),
            pull_secret="pull-secret",
            ssh_public_key="ssh-ed25519 public-key",
        )


def test_delete_and_start_are_direct_uuid_calls() -> None:
    api = FakeApi()
    adapter, _ = _adapter(api)

    adapter.delete_cluster(CLUSTER_ID)
    adapter.start_installation(CLUSTER_ID)

    assert api.calls == [
        (
            "delete_cluster",
            (str(CLUSTER_ID),),
            {"_request_timeout": 7.5},
        ),
        (
            "install_cluster",
            (str(CLUSTER_ID),),
            {"_request_timeout": 7.5},
        ),
    ]


def test_list_hosts_combines_infraenvs_deduplicates_and_sorts() -> None:
    api = FakeApi()
    api.infra_envs = [
        SimpleNamespace(id=str(ENV_B_ID)),
        SimpleNamespace(id=str(ENV_A_ID)),
    ]
    api.hosts = {
        str(ENV_B_ID): [_host(HOST_B_ID), _host(HOST_A_ID)],
        str(ENV_A_ID): [_host(HOST_A_ID)],
    }
    adapter, _ = _adapter(api)

    snapshots = adapter.list_hosts(CLUSTER_ID)

    assert [snapshot.id for snapshot in snapshots] == [HOST_A_ID, HOST_B_ID]
    assert api.calls[0] == (
        "list_infra_envs",
        (),
        {"cluster_id": str(CLUSTER_ID), "_request_timeout": 7.5},
    )


def test_list_hosts_rejects_conflicting_cluster_membership() -> None:
    api = FakeApi()
    api.infra_envs = [SimpleNamespace(id=str(ENV_A_ID))]
    api.hosts = {
        str(ENV_A_ID): [_host(HOST_A_ID, cluster_id=OTHER_CLUSTER_ID)]
    }
    adapter, _ = _adapter(api)

    with pytest.raises(AssistedError, match="membership"):
        adapter.list_hosts(CLUSTER_ID)


def test_list_hosts_rejects_conflicting_duplicate_observations() -> None:
    api = FakeApi()
    api.infra_envs = [
        SimpleNamespace(id=str(ENV_A_ID)),
        SimpleNamespace(id=str(ENV_B_ID)),
    ]
    api.hosts = {
        str(ENV_A_ID): [_host(HOST_A_ID)],
        str(ENV_B_ID): [_host(HOST_A_ID, status="error")],
    }
    adapter, _ = _adapter(api)

    with pytest.raises(AssistedError, match=r"conflicting.*host"):
        adapter.list_hosts(CLUSTER_ID)
