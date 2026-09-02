from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from air_sdk.exceptions import AirUnexpectedResponse

from ocp_dsx_air.adapters.air.transport import AirApiTransport
from ocp_dsx_air.core.exceptions import AirError, AirImageError


class ApiFactory:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.api = SimpleNamespace(
            client=SimpleNamespace(
                verify=None,
                trust_env=False,
                connect_timeout=None,
                read_timeout=None,
            )
        )

    def __call__(self, **kwargs: object) -> Any:
        self.calls.append(kwargs)
        return self.api


def test_transport_builds_verified_non_autopatching_sdk_client(
    tmp_path: Path,
) -> None:
    ca_bundle = tmp_path / "ca.pem"
    ca_bundle.touch()
    factory = ApiFactory()
    transport = AirApiTransport(
        api_key="nvapi-secret",
        api_url="https://air.example.test/api/",
        ca_bundle=ca_bundle,
        request_timeout=17.5,
        _api_factory=factory,
    )

    result = transport.call("inspect Air", lambda api: api)

    assert result is factory.api
    assert factory.calls == [
        {
            "api_key": "nvapi-secret",
            "api_url": "https://air.example.test/api/",
            "auto_patch": False,
        }
    ]
    assert factory.api.client.verify == str(ca_bundle)
    assert factory.api.client.trust_env is True
    assert factory.api.client.connect_timeout == timedelta(seconds=17.5)
    assert factory.api.client.read_timeout == timedelta(seconds=17.5)


def test_transport_uses_system_ca_when_bundle_is_absent() -> None:
    factory = ApiFactory()
    transport = AirApiTransport(api_key="nvapi-secret", _api_factory=factory)

    transport.call("inspect Air", lambda api: api)

    assert factory.api.client.verify is True


@pytest.mark.parametrize("api_key", ["", "   "])
def test_transport_rejects_empty_api_key(api_key: str) -> None:
    with pytest.raises(AirError, match="non-empty"):
        AirApiTransport(api_key=api_key)


@pytest.mark.parametrize("request_timeout", [0, -1])
def test_transport_rejects_nonpositive_timeout(request_timeout: float) -> None:
    with pytest.raises(AirError, match="positive"):
        AirApiTransport(api_key="nvapi-secret", request_timeout=request_timeout)


def test_transport_rejects_unverified_api_url() -> None:
    with pytest.raises(AirError, match="HTTPS"):
        AirApiTransport(
            api_key="nvapi-secret",
            api_url="http://air.example.test/api",
        )


def test_sdk_http_failure_is_translated_without_response_body_or_key() -> None:
    transport = AirApiTransport(
        api_key="nvapi-secret",
        _api_factory=ApiFactory(),
    )

    def fail(api: object) -> None:
        del api
        raise AirUnexpectedResponse(
            "response body contains nvapi-secret and pull-secret",
            status_code=503,
        )

    with pytest.raises(AirError, match=r"list images.*HTTP 503") as failure:
        transport.call("list images", fail)

    message = str(failure.value)
    assert "nvapi-secret" not in message
    assert "pull-secret" not in message
    assert "response body" not in message


def test_unexpected_failure_exposes_only_exception_type() -> None:
    transport = AirApiTransport(
        api_key="nvapi-secret",
        _api_factory=ApiFactory(),
    )

    def fail(api: object) -> None:
        del api
        raise RuntimeError("private nvapi-secret detail")

    with pytest.raises(AirError, match=r"inspect simulation.*RuntimeError") as failure:
        transport.call("inspect simulation", fail)

    assert "private" not in str(failure.value)
    assert "nvapi-secret" not in str(failure.value)


def test_domain_air_failure_is_not_wrapped() -> None:
    transport = AirApiTransport(
        api_key="nvapi-secret",
        _api_factory=ApiFactory(),
    )
    expected = AirImageError("Invalid image model")

    def fail(api: object) -> None:
        del api
        raise expected

    with pytest.raises(AirImageError) as failure:
        transport.call("map image", fail)

    assert failure.value is expected
