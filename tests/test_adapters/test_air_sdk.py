"""HTTP-to-SDK boundary checks with synthetic network responses."""

import json

import pytest
import requests

from ocp_dsx_air.adapters.air.transport import AirApiTransport
from ocp_dsx_air.core.exceptions import AirError


@pytest.mark.parametrize("endpoint", ["simulations", "images"])
def test_sdk_lists_decode_empty_http_page(monkeypatch, endpoint):
    def send(self, request, **kwargs):
        assert request.headers["Authorization"] == "Bearer synthetic-key"
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps({"count": 0, "next": None, "previous": None, "results": []}).encode()
        response.request = request
        response.url = request.url
        return response

    monkeypatch.setattr(requests.Session, "send", send)
    transport = AirApiTransport(api_key="synthetic-key")
    assert transport.call("list", lambda api: list(getattr(api, endpoint).list())) == []


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_real_sdk_http_failures_are_sanitized(monkeypatch, status):
    def send(self, request, **kwargs):
        response = requests.Response()
        response.status_code = status
        response._content = b'{"detail": "synthetic-key secret-response"}'
        response.request = request
        response.url = request.url
        return response

    monkeypatch.setattr(requests.Session, "send", send)
    transport = AirApiTransport(api_key="synthetic-key")
    with pytest.raises(AirError) as failure:
        transport.call("list simulations", lambda api: list(api.simulations.list()))
    assert str(status) in str(failure.value)
    assert "synthetic-key" not in str(failure.value)
    assert "secret-response" not in str(failure.value)
