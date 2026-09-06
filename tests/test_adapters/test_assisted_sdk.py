"""Exercise the installed generated SDK, not a hand-written endpoint fake."""

import json
from types import SimpleNamespace

import pytest
from assisted_service_client import ApiClient, api

from ocp_dsx_air.adapters.assisted.transport import AssistedApiClient
from ocp_dsx_air.core.exceptions import AssistedError


@pytest.mark.parametrize("alias", ["InfraEnvList", "ClusterList", "HostList"])
def test_generated_array_alias_regression(alias):
    response = SimpleNamespace(data=b"[{}]")
    # Empty Swagger alias models leave response dictionaries untyped.
    original = ApiClient()
    assert original.deserialize(response, alias) == [{}]
    client = AssistedApiClient()
    assert client.deserialize(SimpleNamespace(data=b"[]"), alias) == []


@pytest.mark.parametrize("payload", [None, {}, {"items": []}, [None], ["secret"], "secret"])
def test_invalid_list_shapes_are_not_absence(payload):
    client = AssistedApiClient()
    with pytest.raises(AssistedError, match="invalid list shape"):
        client.deserialize(SimpleNamespace(data=json.dumps(payload)), "InfraEnvList")


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("list_infra_envs", ()),
        ("v2_list_clusters", ()),
        ("v2_list_hosts", ("00000000-0000-0000-0000-000000000001",)),
    ],
)
def test_endpoint_http_response_uses_corrected_deserializer(monkeypatch, method, args):
    client = AssistedApiClient()
    response = SimpleNamespace(data=b"[]", status=200, getheaders=lambda: {})
    monkeypatch.setattr(client, "request", lambda *a, **kw: response)
    assert getattr(api.InstallerApi(client), method)(*args) == []


def test_nonempty_infraenv_response_preserves_fields():
    payload = [
        {
            "kind": "InfraEnv",
            "id": "00000000-0000-0000-0000-000000000001",
            "href": "/v2/infra-envs/test",
            "name": "synthetic",
            "type": "full-iso",
            "updated_at": "2026-01-01T00:00:00Z",
            "created_at": "2026-01-01T00:00:00Z",
            "pull_secret_set": True,
        }
    ]
    client = AssistedApiClient()
    result = client.deserialize(SimpleNamespace(data=json.dumps(payload)), "InfraEnvList")
    assert result[0].name == "synthetic"
    assert result[0].pull_secret_set is True


@pytest.mark.parametrize(
    ("timestamp", "expected"),
    [
        (None, False),
        ("0001-01-01T00:00:00Z", False),
        ("2026-01-01T00:00:00Z", True),
    ],
)
def test_install_markers_use_real_sdk_datetime_decoding(timestamp, expected):
    from ocp_dsx_air.adapters.assisted.mapping import _timestamp_is_set

    client = AssistedApiClient()
    decoded = client.deserialize(SimpleNamespace(data=json.dumps(timestamp)), "datetime")
    assert _timestamp_is_set(decoded) is expected


@pytest.mark.parametrize("model", ["Cluster", "Host"])
def test_nonempty_sdk_models_reach_snapshot_mapping(model):
    from datetime import datetime
    from uuid import UUID

    from ocp_dsx_air.adapters.assisted.mapping import cluster_to_snapshot, host_to_snapshot
    from tests.test_adapters.test_assisted_mapping import _cluster, _host

    def encode(value):
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, UUID):
            return str(value)
        if hasattr(value, "to_dict"):
            return value.to_dict()
        return vars(value)

    value = _cluster() if model == "Cluster" else _host()
    payload = vars(value) | {"kind": model, "href": "/synthetic", "image_info": {}}
    client = AssistedApiClient()
    decoded = client.deserialize(SimpleNamespace(data=json.dumps([payload], default=encode)), model + "List")[0]
    if model == "Cluster":
        snapshot = cluster_to_snapshot(decoded)
        assert snapshot.name == value.name
        assert snapshot.machine_networks == ("192.168.200.0/24", "192.168.201.0/24")
    else:
        snapshot = host_to_snapshot(decoded, infraenv_id=UUID(int=1))
        assert snapshot.id == UUID(str(value.id))
