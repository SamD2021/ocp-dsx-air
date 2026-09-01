

import json
import ssl
from collections.abc import Callable
from http.client import responses as http_status_reasons
from pathlib import Path
from typing import Any, TypeVar
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, getproxies, proxy_bypass, urlopen

from assisted_service_client import ApiClient, Configuration, api
from assisted_service_client.rest import ApiException

from ocp_dsx_air.core.exceptions import AssistedError

DEFAULT_ASSISTED_API_URL = "https://api.openshift.com/api/assisted-install"
DEFAULT_OAUTH_TOKEN_URL = (
    "https://sso.redhat.com/auth/realms/redhat-external/"
    "protocol/openid-connect/token"
)
DEFAULT_REQUEST_TIMEOUT = 30.0

# We use TypeVar to define a generic type for the request function.
# The benefit here is that unlike Union, TypeVar remembers and matches the input type with the output type.
_T = TypeVar("_T")

# We create these callable types so that we can use a function such as `call` that can accept both these
# function signatures and make it easier to test with mock objects
_ApiFactory = Callable[[Configuration], Any]
_TokenExchange = Callable[..., str]
_UrlOpen = Callable[..., Any]


def build_configuration(
    *,
    access_token: str,
    api_url: str,
    ca_bundle: Path | None = None,
    proxy_url: str | None = None,
) -> Configuration:
    """Build a generated-client configuration without weakening TLS."""
    configuration = Configuration()
    configuration.host = api_url.rstrip("/")
    configuration.verify_ssl = True
    configuration.api_key["Authorization"] = access_token
    configuration.api_key_prefix["Authorization"] = "Bearer"
    generated_configuration: Any = configuration
    if ca_bundle is not None:
        generated_configuration.ssl_ca_cert = str(ca_bundle)

    if proxy_url is None:
        parsed = urlparse(configuration.host)
        if parsed.hostname and not proxy_bypass(parsed.hostname):
            proxies = getproxies()
            proxy_url = proxies.get(parsed.scheme) or proxies.get("https")
    generated_configuration.proxy = proxy_url
    return configuration


def _default_api_factory(configuration: Configuration) -> api.InstallerApi:
    return api.InstallerApi(ApiClient(configuration))


def _oauth_exchange(
    *,
    offline_token: str,
    token_url: str,
    ca_bundle: Path | None,
    timeout: float,
    force_refresh: bool,
) -> str:
    del force_refresh
    request = Request(
        token_url,
        data=urlencode(
            {
                "grant_type": "refresh_token",
                "client_id": "cloud-services",
                "refresh_token": offline_token,
            }
        ).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    context = ssl.create_default_context(
        cafile=str(ca_bundle) if ca_bundle is not None else None
    )
    try:
        with urlopen(request, timeout=timeout, context=context) as response:
            payload = json.load(response)
    except HTTPError as exc:
        raise AssistedError(
            f"OAuth token exchange failed (HTTP {exc.code}: {exc.reason})"
        ) from exc
    except (URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        raise AssistedError(
            f"OAuth token exchange failed ({type(exc).__name__})"
        ) from exc

    access_token = payload.get("access_token") if isinstance(payload, dict) else None
    if not isinstance(access_token, str) or not access_token.strip():
        raise AssistedError("OAuth token exchange returned no access token")
    return access_token.strip()


class AssistedApiTransport:
    """Own token lifetime and translate failures at the vendor boundary."""

    def __init__(
        self,
        *,
        offline_token: str,
        api_url: str = DEFAULT_ASSISTED_API_URL,
        oauth_token_url: str = DEFAULT_OAUTH_TOKEN_URL,
        ca_bundle: Path | None = None,
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
        _token_exchange: _TokenExchange | None = None,
        _api_factory: _ApiFactory = _default_api_factory,
        _urlopen: _UrlOpen = urlopen,
    ) -> None:
        if not offline_token.strip():
            raise AssistedError("A non-empty Assisted offline token is required")
        if request_timeout <= 0:
            raise AssistedError("Assisted request timeout must be positive")

        self._offline_token = offline_token
        self._api_url = api_url
        self._oauth_token_url = oauth_token_url
        self._ca_bundle = ca_bundle
        self.request_timeout = request_timeout
        self._api_factory = _api_factory
        self._token_exchange = _token_exchange
        self._urlopen = _urlopen
        self._access_token: str | None = None
        self._api: Any | None = None

    def _exchange_token(self, *, force_refresh: bool) -> str:
        if self._token_exchange is not None:
            token = self._token_exchange(force_refresh=force_refresh)
        else:
            token = _oauth_exchange(
                offline_token=self._offline_token,
                token_url=self._oauth_token_url,
                ca_bundle=self._ca_bundle,
                timeout=self.request_timeout,
                force_refresh=force_refresh,
            )
        if not isinstance(token, str) or not token.strip():
            raise AssistedError("OAuth token exchange returned no access token")
        return token.strip()

    def _get_api(self, *, force_refresh: bool = False) -> Any:
        if force_refresh or self._access_token is None:
            self._access_token = self._exchange_token(force_refresh=force_refresh)
            self._api = None
        if self._api is None:
            configuration = build_configuration(
                access_token=self._access_token,
                api_url=self._api_url,
                ca_bundle=self._ca_bundle,
            )
            self._api = self._api_factory(configuration)
        return self._api

    def _translate_failure(self, operation: str, failure: Exception) -> AssistedError:
        if isinstance(failure, ApiException):
            status = failure.status if failure.status is not None else "unknown"
            reason = str(failure.reason or "API error").splitlines()[0][:120]
            for secret in (self._offline_token, self._access_token):
                if secret and secret in reason:
                    reason = "API error"
            return AssistedError(
                f"Assisted {operation} failed (HTTP {status}: {reason})"
            )
        return AssistedError(
            f"Assisted {operation} failed ({type(failure).__name__})"
        )

    def call(self, operation: str, request: Callable[[Any], _T]) -> _T:
        """Call the API, refreshing once after the first HTTP 401 response."""
        try:
            api_instance = self._get_api()
            return request(api_instance)
        except ApiException as first_failure:
            if first_failure.status != 401:
                raise self._translate_failure(operation, first_failure) from first_failure
        except AssistedError:
            raise
        except Exception as failure:
            raise self._translate_failure(operation, failure) from failure

        try:
            refreshed_api = self._get_api(force_refresh=True)
            return request(refreshed_api)
        except AssistedError:
            raise
        except Exception as failure:
            raise self._translate_failure(operation, failure) from failure

    def open_download(self, operation: str, url: str) -> Any:
        """Open a signed HTTPS download without attaching OAuth credentials."""
        request = Request(url, method="GET")
        try:
            context = ssl.create_default_context(
                cafile=str(self._ca_bundle) if self._ca_bundle is not None else None
            )
            return self._urlopen(
                request,
                timeout=self.request_timeout,
                context=context,
            )
        except HTTPError as failure:
            reason = http_status_reasons.get(failure.code, "HTTP error")
            raise AssistedError(
                f"Assisted {operation} failed (HTTP {failure.code}: {reason})"
            ) from failure
        except AssistedError:
            raise
        except (URLError, OSError, ValueError) as failure:
            raise AssistedError(
                f"Assisted {operation} failed ({type(failure).__name__})"
            ) from failure
