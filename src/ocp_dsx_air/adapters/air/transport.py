"""Authenticated NVIDIA Air SDK construction and error translation."""

from collections.abc import Callable
from datetime import timedelta
from http.client import responses as http_status_reasons
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import urlparse

import requests
from air_sdk import AirApi, const
from air_sdk.exceptions import AirError as SdkAirError

from ocp_dsx_air.core.exceptions import AirError

DEFAULT_AIR_API_URL = const.AIR_API_URL
DEFAULT_AIR_REQUEST_TIMEOUT = 30.0

_T = TypeVar("_T")
_ApiFactory = Callable[..., Any]


def _default_api_factory(
    *,
    api_key: str,
    api_url: str,
    auto_patch: bool,
) -> AirApi:
    return AirApi.with_api_key(
        api_key=api_key,
        api_url=api_url,
        auto_patch=auto_patch,
    )


class AirApiTransport:
    """Own a verified Air SDK client and sanitize boundary failures."""

    def __init__(
        self,
        *,
        api_key: str,
        api_url: str = DEFAULT_AIR_API_URL,
        ca_bundle: Path | None = None,
        request_timeout: float = DEFAULT_AIR_REQUEST_TIMEOUT,
        _api_factory: _ApiFactory = _default_api_factory,
    ) -> None:
        if not api_key.strip():
            raise AirError("A non-empty NVIDIA Air API key is required")
        parsed_url = urlparse(api_url)
        if parsed_url.scheme != "https" or not parsed_url.netloc:
            raise AirError("NVIDIA Air API URL must use HTTPS")
        if request_timeout <= 0:
            raise AirError("NVIDIA Air request timeout must be positive")

        self._api_key = api_key
        self._api_url = api_url
        self._ca_bundle = ca_bundle
        self.request_timeout = request_timeout
        self._api_factory = _api_factory
        self._api: Any | None = None

    def _get_api(self) -> Any:
        if self._api is None:
            api = self._api_factory(
                api_key=self._api_key,
                api_url=self._api_url,
                auto_patch=False,
            )
            client: Any = api.client
            client.verify = str(self._ca_bundle) if self._ca_bundle is not None else True
            client.trust_env = True
            timeout = timedelta(seconds=self.request_timeout)
            client.connect_timeout = timeout
            client.read_timeout = timeout
            self._api = api
        return self._api

    @staticmethod
    def _translate_failure(operation: str, failure: Exception) -> AirError:
        status: int | None = None
        if isinstance(failure, SdkAirError):
            status = failure.status_code
        elif isinstance(failure, requests.RequestException):
            response = failure.response
            if response is not None:
                status = response.status_code

        if status is not None:
            reason = http_status_reasons.get(status, "HTTP error")
            return AirError(
                f"NVIDIA Air {operation} failed (HTTP {status}: {reason})"
            )
        return AirError(
            f"NVIDIA Air {operation} failed ({type(failure).__name__})"
        )

    def call(self, operation: str, request: Callable[[Any], _T]) -> _T:
        """Run one SDK operation and expose only sanitized failure metadata."""
        try:
            return request(self._get_api())
        except AirError:
            raise
        except Exception as failure:
            raise self._translate_failure(operation, failure) from failure
