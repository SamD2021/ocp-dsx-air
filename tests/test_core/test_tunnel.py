import stat
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
import yaml

from ocp_dsx_air.cli.commands import access, tunnel
from ocp_dsx_air.core.contracts import AirSimulationStatus, JumpHostSnapshot
from ocp_dsx_air.core.exceptions import ConfigurationError, JumpHostError

from ..support.deployment import FakeClock


@pytest.fixture(autouse=True)
def _reachable_jump_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(access, "_tcp_reachable", lambda jump_host: True)
    monkeypatch.setattr(access, "SystemClock", FakeClock)


def _write_spec(path: Path) -> None:
    path.write_text(
        """simulation: {name: dsx-lab}
cluster:
  name: ocp
  version: "4.19"
  base_dns_domain: example.test
  machine_networks: [192.168.200.0/24]
  api_vips: [192.168.200.10]
  control_plane: {count: 1}
auth:
  air_api_key_file: op://Private/Air/credential
"""
    )


class FakeProcess:
    def __init__(self, return_code: int = 0) -> None:
        self.return_code = return_code
        self.terminated = False

    def wait(self, timeout: float | None = None) -> int:
        return self.return_code

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        pass


class InterruptingProcess(FakeProcess):
    def wait(self, timeout: float | None = None) -> int:
        if not self.terminated:
            raise KeyboardInterrupt
        return 0


def test_run_tunnel_creates_verified_kubeconfig_and_foreground_forward(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec_path = tmp_path / "spec.yaml"
    _write_spec(spec_path)
    credentials = tmp_path / "cache" / "dsx-lab" / "credentials"
    credentials.mkdir(parents=True)
    source = credentials / "kubeconfig"
    source.write_text(
        yaml.safe_dump(
            {
                "apiVersion": "v1",
                "clusters": [
                    {
                        "name": "ocp",
                        "cluster": {
                            "server": "https://api.ocp.example.test:6443",
                            "certificate-authority-data": "synthetic-ca",
                        },
                    }
                ],
            }
        )
    )
    simulation = SimpleNamespace(id=UUID(int=1), status=AirSimulationStatus.ACTIVE)
    jump = JumpHostSnapshot(UUID(int=2), "worker.example.test", 22022, "ubuntu")
    air = SimpleNamespace(
        find_simulation=lambda name: simulation,
        find_jump_host=lambda simulation_id: jump,
    )
    monkeypatch.setattr(tunnel, "_read_secret", lambda *args, **kwargs: "api-key")
    monkeypatch.setattr(tunnel, "NvidiaAirAdapter", lambda **kwargs: air)
    monkeypatch.setattr(tunnel, "cache_dir", lambda: tmp_path / "cache")
    monkeypatch.setattr(tunnel, "_ensure_local_port_available", lambda port: None)
    process = FakeProcess()
    calls: list[list[str]] = []
    monkeypatch.setattr(
        tunnel.subprocess,
        "Popen",
        lambda args: calls.append(args) or process,
    )
    messages: list[str] = []

    tunnel.run_tunnel(spec_path, local_port=7443, announce=messages.append)

    destination = credentials / "kubeconfig.tunnel"
    config = yaml.safe_load(destination.read_text())
    cluster = config["clusters"][0]["cluster"]
    assert cluster["server"] == "https://127.0.0.1:7443"
    assert cluster["tls-server-name"] == "api.ocp.example.test"
    assert cluster["certificate-authority-data"] == "synthetic-ca"
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert "127.0.0.1:7443:192.168.200.10:6443" in calls[0]
    assert "ubuntu@worker.example.test" in calls[0]
    assert any("KUBECONFIG=" in message for message in messages)


@pytest.mark.parametrize(
    ("simulation", "jump_host", "message"),
    [
        (None, None, "does not exist"),
        (
            SimpleNamespace(id=UUID(int=1), status=AirSimulationStatus.INACTIVE),
            None,
            "is INACTIVE",
        ),
        (
            SimpleNamespace(id=UUID(int=1), status=AirSimulationStatus.ACTIVE),
            None,
            "Timed out waiting",
        ),
    ],
)
def test_run_tunnel_fails_before_ssh_when_lab_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    simulation: object,
    jump_host: object,
    message: str,
) -> None:
    spec_path = tmp_path / "spec.yaml"
    _write_spec(spec_path)
    air = SimpleNamespace(
        find_simulation=lambda name: simulation,
        find_jump_host=lambda simulation_id: jump_host,
    )
    monkeypatch.setattr(tunnel, "_read_secret", lambda *args, **kwargs: "api-key")
    monkeypatch.setattr(tunnel, "NvidiaAirAdapter", lambda **kwargs: air)
    monkeypatch.setattr(
        tunnel.subprocess,
        "Popen",
        lambda args: pytest.fail("SSH must not start"),
    )

    with pytest.raises(JumpHostError, match=message):
        tunnel.run_tunnel(spec_path)


def test_tunneled_kubeconfig_rejects_missing_source(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="run ocp-air deploy"):
        tunnel._tunneled_kubeconfig(tmp_path / "kubeconfig", local_port=6443, api_hostname="api.example")


def test_tunnel_reports_sanitized_ssh_exit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spec_path = tmp_path / "spec.yaml"
    _write_spec(spec_path)
    credentials = tmp_path / "cache" / "dsx-lab" / "credentials"
    credentials.mkdir(parents=True)
    (credentials / "kubeconfig").write_text("clusters: [{name: ocp, cluster: {server: old}}]\n")
    simulation = SimpleNamespace(id=UUID(int=1), status=AirSimulationStatus.ACTIVE)
    air = SimpleNamespace(
        find_simulation=lambda name: simulation,
        find_jump_host=lambda simulation_id: JumpHostSnapshot(UUID(int=2), "worker.example.test", 22022, "ubuntu"),
    )
    monkeypatch.setattr(tunnel, "_read_secret", lambda *args, **kwargs: "api-key")
    monkeypatch.setattr(tunnel, "NvidiaAirAdapter", lambda **kwargs: air)
    monkeypatch.setattr(tunnel, "cache_dir", lambda: tmp_path / "cache")
    monkeypatch.setattr(tunnel, "_ensure_local_port_available", lambda port: None)
    monkeypatch.setattr(tunnel.subprocess, "Popen", lambda args: FakeProcess(255))

    with pytest.raises(JumpHostError, match="status 255") as error:
        tunnel.run_tunnel(spec_path, announce=lambda message: None)
    assert "api-key" not in str(error.value)


def test_tunnel_terminates_ssh_on_keyboard_interrupt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spec_path = tmp_path / "spec.yaml"
    _write_spec(spec_path)
    credentials = tmp_path / "cache" / "dsx-lab" / "credentials"
    credentials.mkdir(parents=True)
    (credentials / "kubeconfig").write_text("clusters: [{name: ocp, cluster: {server: old}}]\n")
    simulation = SimpleNamespace(id=UUID(int=1), status=AirSimulationStatus.ACTIVE)
    air = SimpleNamespace(
        find_simulation=lambda name: simulation,
        find_jump_host=lambda simulation_id: JumpHostSnapshot(UUID(int=2), "worker.example.test", 22022, "ubuntu"),
    )
    process = InterruptingProcess()
    monkeypatch.setattr(tunnel, "_read_secret", lambda *args, **kwargs: "api-key")
    monkeypatch.setattr(tunnel, "NvidiaAirAdapter", lambda **kwargs: air)
    monkeypatch.setattr(tunnel, "cache_dir", lambda: tmp_path / "cache")
    monkeypatch.setattr(tunnel, "_ensure_local_port_available", lambda port: None)
    monkeypatch.setattr(tunnel.subprocess, "Popen", lambda args: process)

    tunnel.run_tunnel(spec_path, announce=lambda message: None)

    assert process.terminated is True
