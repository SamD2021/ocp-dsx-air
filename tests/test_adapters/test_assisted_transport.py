from collections.abc import Callable
from email.message import Message
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request

import pytest
from assisted_service_client import Configuration
from assisted_service_client.rest import ApiException

from ocp_dsx_air.adapters.assisted.transport import (
    AssistedApiTransport,
    build_configuration,
)
from ocp_dsx_air.core.exceptions import AssistedError


def _api_error(status: int, reason: str, *, body: str | None = None) -> ApiException:
    failure = ApiException(status=status, reason=reason)
    failure.body = body
    return failure


class ApiFactory:
    def __init__(self, outcomes: list[object] | None = None) -> None:
        self.tokens: list[str] = []
        self.outcomes = outcomes or []

    def __call__(self, configuration: Configuration) -> Callable[[], object]:
        token = configuration.api_key["Authorization"]
        self.tokens.append(token)

        def request() -> object:
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        return request


def test_transport_rejects_empty_offline_token_without_echoing_it() -> None:
    with pytest.raises(AssistedError, match="offline token") as raised:
        AssistedApiTransport(offline_token="   ")

    assert "   " not in str(raised.value)


def test_token_exchange_is_lazy_and_cached() -> None:
    exchanges: list[bool] = []
    factory = ApiFactory(["first", "second"])

    def exchange(*, force_refresh: bool) -> str:
        exchanges.append(force_refresh)
        return "access-token"

    transport = AssistedApiTransport(
        offline_token="offline-secret",
        _token_exchange=exchange,
        _api_factory=factory,
    )

    assert exchanges == []
    assert transport.call("first request", lambda api: api()) == "first"
    assert transport.call("second request", lambda api: api()) == "second"
    assert exchanges == [False]
    assert factory.tokens == ["access-token"]


def test_configuration_keeps_tls_verification_and_sets_ca_and_proxy(
    tmp_path: Path,
) -> None:
    ca_bundle = tmp_path / "ca.pem"
    ca_bundle.touch()

    configuration = build_configuration(
        access_token="access-token",
        api_url="https://assisted.example.test",
        ca_bundle=ca_bundle,
        proxy_url="https://proxy.example.test:8443",
    )

    assert configuration.host == "https://assisted.example.test"
    assert configuration.verify_ssl is True
    assert configuration.ssl_ca_cert == str(ca_bundle)
    assert configuration.proxy == "https://proxy.example.test:8443"
    assert configuration.api_key["Authorization"] == "access-token"
    assert configuration.api_key_prefix["Authorization"] == "Bearer"


def test_configuration_uses_standard_https_proxy_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example.test:8080")
    monkeypatch.delenv("NO_PROXY", raising=False)

    configuration = build_configuration(
        access_token="access-token",
        api_url="https://assisted.example.test",
    )

    assert configuration.proxy == "http://proxy.example.test:8080"


def test_one_unauthorized_response_refreshes_once_and_retries() -> None:
    factory = ApiFactory([ApiException(status=401, reason="Unauthorized"), "ok"])
    exchanges: list[bool] = []

    def exchange(*, force_refresh: bool) -> str:
        exchanges.append(force_refresh)
        return "refreshed" if force_refresh else "initial"

    transport = AssistedApiTransport(
        offline_token="offline-secret",
        _token_exchange=exchange,
        _api_factory=factory,
    )

    assert transport.call("list clusters", lambda api: api()) == "ok"
    assert exchanges == [False, True]
    assert factory.tokens == ["initial", "refreshed"]


def test_repeated_unauthorized_response_is_translated_without_secrets() -> None:
    secret = "offline-secret-value"
    factory = ApiFactory(
        [
            _api_error(401, "Unauthorized", body=secret),
            _api_error(401, "Unauthorized", body=secret),
        ]
    )
    transport = AssistedApiTransport(
        offline_token=secret,
        _token_exchange=lambda *, force_refresh: "access-secret-value",
        _api_factory=factory,
    )

    with pytest.raises(AssistedError, match=r"list clusters.*401") as raised:
        transport.call("list clusters", lambda api: api())

    message = str(raised.value)
    assert secret not in message
    assert "access-secret-value" not in message


@pytest.mark.parametrize(
    "failure",
    [
        _api_error(409, "Conflict", body="pull-secret-value"),
        OSError("ssh-ed25519 secret-key-value"),
    ],
)
def test_non_unauthorized_failures_are_safely_translated(failure: Exception) -> None:
    factory = ApiFactory([failure])
    transport = AssistedApiTransport(
        offline_token="offline-secret",
        _token_exchange=lambda *, force_refresh: "access-secret",
        _api_factory=factory,
    )

    with pytest.raises(AssistedError, match="create cluster") as raised:
        transport.call("create cluster", lambda api: api())

    message = str(raised.value)
    assert "pull-secret-value" not in message
    assert "secret-key-value" not in message


def test_request_timeout_is_available_to_generated_api_calls() -> None:
    transport = AssistedApiTransport(offline_token="offline", request_timeout=12.5)

    assert transport.request_timeout == 12.5


def test_external_download_uses_verified_tls_ca_timeout_and_no_oauth_header(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ca_bundle = tmp_path / "ca.pem"
    ca_bundle.touch()
    tls_context = object()
    context_calls: list[str | None] = []
    request_calls: list[tuple[Request, float, object]] = []
    response = object()

    def create_context(*, cafile: str | None = None) -> object:
        context_calls.append(cafile)
        return tls_context

    def open_url(
        request: Request,
        *,
        timeout: float,
        context: object,
    ) -> object:
        request_calls.append((request, timeout, context))
        return response

    monkeypatch.setattr("ssl.create_default_context", create_context)
    transport = AssistedApiTransport(
        offline_token="offline",
        ca_bundle=ca_bundle,
        request_timeout=12.5,
        _urlopen=open_url,
    )

    opened = transport.open_download(
        "download discovery ISO",
        "https://images.example.test/discovery.iso?signature=secret",
    )

    assert opened is response
    assert context_calls == [str(ca_bundle)]
    request, timeout, context = request_calls[0]
    assert request.full_url.startswith("https://images.example.test/")
    assert request.get_method() == "GET"
    assert request.get_header("Authorization") is None
    assert timeout == 12.5
    assert context is tls_context


def test_external_download_failure_does_not_expose_presigned_url() -> None:
    presigned_url = "https://images.example.test/discovery.iso?signature=secret"

    def fail_download(*args: Any, **kwargs: Any) -> object:
        del args, kwargs
        raise HTTPError(
            presigned_url,
            503,
            "unavailable for signature=secret",
            Message(),
            None,
        )

    transport = AssistedApiTransport(
        offline_token="offline",
        _urlopen=fail_download,
    )

    with pytest.raises(AssistedError, match=r"download discovery ISO.*503") as raised:
        transport.open_download("download discovery ISO", presigned_url)

    assert presigned_url not in str(raised.value)
    assert "signature=secret" not in str(raised.value)
