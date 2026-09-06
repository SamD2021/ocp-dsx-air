import importlib.util
import json
import os
from pathlib import Path

import pytest

module_spec = importlib.util.spec_from_file_location(
    "live_probe", Path(__file__).parents[2] / "tools" / "live_probe.py"
)
assert module_spec is not None
assert module_spec.loader is not None
live_probe = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(live_probe)


@pytest.mark.parametrize("fail", [False, True])
def test_probe_suppresses_output_and_exception_details(capfd, fail):
    def operation():
        print("sensitive-token", flush=True)
        os.write(2, b"signed-url-sensitive")
        if fail:
            raise ValueError("sensitive-token")
        return {"sensitive-token": "secret"}

    ok, _ = live_probe.probe("synthetic", operation)
    output = capfd.readouterr()
    assert "sensitive" not in output.out + output.err
    record = json.loads(output.out)
    assert record["outcome"] == ("failed" if fail else "passed")
    assert ok is not fail


def test_status_is_numeric_only():
    error = ValueError("secret")
    error.status = "secret"
    assert live_probe.failure_status(error) is None
    error.status = 403
    assert live_probe.failure_status(error) == 403


@pytest.mark.parametrize(
    "method",
    [
        "delete_cluster",
        "start_installation",
        "delete_infraenv",
        "upload_image",
        "delete_image",
        "delete_simulation",
        "start_simulation",
        "shutdown_simulation",
        "ensure_jump_host",
    ],
)
def test_unowned_mutations_are_refused(tmp_path, method):
    from types import SimpleNamespace
    from uuid import UUID

    def forbidden(*args, **kwargs):
        pytest.fail("Unowned mutation reached adapter")

    journal = live_probe.Journal(tmp_path / "state.json", lambda record: None)
    adapter = live_probe.OwnedAdapter(SimpleNamespace(**{method: forbidden}), journal)
    with pytest.raises(RuntimeError, match="unowned"):
        getattr(adapter, method)(UUID(int=1))


def test_creation_is_journaled_before_caller_mapping(tmp_path):
    from types import SimpleNamespace
    from uuid import UUID

    journal = live_probe.Journal(tmp_path / "state.json", lambda record: None)
    transport = live_probe.ObservedTransport(
        SimpleNamespace(call=lambda operation, request: SimpleNamespace(id=UUID(int=1))), journal
    )
    transport.call("create cluster", lambda api: None)
    reloaded = live_probe.Journal(tmp_path / "state.json", lambda record: None)
    assert reloaded.owns("cluster", UUID(int=1))
    assert (tmp_path / "state.json").stat().st_mode & 0o077 == 0
