import subprocess

import pytest

from ocp_dsx_air.adapters.air.jump_host import JumpHostTarget, SshJumpHostExecutor
from ocp_dsx_air.core.exceptions import JumpHostError
from ocp_dsx_air.core.jumphost import cluster_dns_host_block, merge_hosts_file
from ocp_dsx_air.models.runtime import ClusterNetworkConfig


def test_cluster_dns_block_and_merge_are_idempotent() -> None:
    network = ClusterNetworkConfig(
        cluster_name="ocp",
        base_dns_domain="example.test",
        api_vip="192.0.2.10",
        ingress_vip="192.0.2.11",
    )
    block = cluster_dns_host_block(network)

    merged = merge_hosts_file("127.0.0.1 localhost\n", block)

    assert "192.0.2.10 api.ocp.example.test api-int.ocp.example.test" in merged
    assert "192.0.2.11 console-openshift-console.apps.ocp.example.test" in merged
    assert merge_hosts_file(merged, block) == merged


def test_merge_hosts_file_replaces_only_managed_block() -> None:
    old = """127.0.0.1 localhost
# BEGIN ocp-dsx-air
192.0.2.1 old.example.test
# END ocp-dsx-air
203.0.113.2 preserved.example.test
"""

    result = merge_hosts_file(
        old,
        "# BEGIN ocp-dsx-air\n192.0.2.10 new.example.test\n# END ocp-dsx-air\n",
    )

    assert "old.example.test" not in result
    assert "new.example.test" in result
    assert "preserved.example.test" in result


def test_executor_rotates_expired_password_then_configures_dns(monkeypatch) -> None:
    responses = iter(
        [
            subprocess.CompletedProcess([], 1, "", "Password has expired"),
            subprocess.CompletedProcess([], 0, "jump-host-ok\n", ""),
            subprocess.CompletedProcess([], 0, "127.0.0.1 localhost\n", ""),
            subprocess.CompletedProcess([], 0, "", ""),
        ]
    )
    writes: list[str | None] = []

    def run(*args, **kwargs):
        writes.append(kwargs.get("input"))
        return next(responses)

    rotated: list[str] = []
    monkeypatch.setattr(
        SshJumpHostExecutor,
        "_rotate_password",
        staticmethod(
            lambda target, new_password, timeout_seconds: rotated.append(new_password)
        ),
    )
    executor = SshJumpHostExecutor(_run=run)

    executor.ensure_ready(
        JumpHostTarget("jump.example.test", 22022, "ubuntu", "factory"),
        ClusterNetworkConfig(
            "ocp",
            "example.test",
            "192.0.2.10",
            "192.0.2.11",
        ),
        new_password="replacement",
        timeout_seconds=30,
    )

    assert rotated == ["replacement"]
    assert writes[-1] is not None
    assert "api.ocp.example.test" in writes[-1]


def test_executor_does_not_include_ssh_output_in_errors() -> None:
    responses = iter(
        [
            subprocess.CompletedProcess([], 0, "jump-host-ok\n", ""),
            subprocess.CompletedProcess([], 1, "", "sensitive-output"),
        ]
    )
    executor = SshJumpHostExecutor(_run=lambda *args, **kwargs: next(responses))

    with pytest.raises(JumpHostError) as raised:
        executor.ensure_ready(
            JumpHostTarget("jump.example.test", 22022, "ubuntu", "factory"),
            ClusterNetworkConfig(
                "ocp",
                "example.test",
                "192.0.2.10",
                "192.0.2.11",
            ),
            new_password="replacement",
            timeout_seconds=30,
        )

    assert "sensitive-output" not in str(raised.value)
    assert "factory" not in str(raised.value)
    assert "replacement" not in str(raised.value)
