"""Foreground SSH tunnel for accessing an installed cluster API."""

from __future__ import annotations

import os
import socket
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

import yaml

from ocp_dsx_air.adapters.air import NvidiaAirAdapter
from ocp_dsx_air.cli.commands.deploy import _read_secret
from ocp_dsx_air.core.common import cache_dir
from ocp_dsx_air.core.contracts import AirSimulationStatus, JumpHostSnapshot
from ocp_dsx_air.core.exceptions import ConfigurationError, JumpHostError
from ocp_dsx_air.models.spec import load_spec


def _tunneled_kubeconfig(source: Path, *, local_port: int, api_hostname: str) -> Path:
    destination = source.with_name("kubeconfig.tunnel")
    try:
        if source.is_symlink() or not source.is_file():
            raise ConfigurationError(f"Kubeconfig is unavailable at {source}; run ocp-air deploy first")
        config = yaml.safe_load(source.read_text())
        clusters = config["clusters"]
        if not isinstance(clusters, list) or not clusters:
            raise TypeError
        for entry in clusters:
            cluster = entry["cluster"]
            cluster["server"] = f"https://127.0.0.1:{local_port}"
            cluster["tls-server-name"] = api_hostname
            cluster.pop("insecure-skip-tls-verify", None)
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, staged_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
        staged = Path(staged_name)
        try:
            with os.fdopen(fd, "w") as stream:
                yaml.safe_dump(config, stream, sort_keys=False)
            os.chmod(staged, 0o600)
            os.replace(staged, destination)
        finally:
            staged.unlink(missing_ok=True)
    except ConfigurationError:
        raise
    except (OSError, KeyError, TypeError, yaml.YAMLError) as exc:
        raise ConfigurationError("Could not create the tunneled kubeconfig") from exc
    return destination


def _ssh_arguments(jump_host: JumpHostSnapshot, *, api_vip: str, local_port: int) -> list[str]:
    return [
        "ssh",
        "-N",
        "-T",
        "-o",
        "BatchMode=yes",
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "ServerAliveInterval=30",
        "-o",
        "ServerAliveCountMax=3",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-p",
        str(jump_host.port),
        "-L",
        f"127.0.0.1:{local_port}:{api_vip}:6443",
        f"{jump_host.username}@{jump_host.host}",
    ]


def _ensure_local_port_available(port: int) -> None:
    try:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
    except OSError as exc:
        raise ConfigurationError(f"Local port {port} is unavailable; choose another with --local-port") from exc


def run_tunnel(
    spec_path: Path,
    *,
    local_port: int = 6443,
    announce: Callable[[str], None] = print,
) -> None:
    """Resolve the deployed lab and keep its API tunnel open until interrupted."""
    if not 1 <= local_port <= 65535:
        raise ConfigurationError("Local tunnel port must be between 1 and 65535")
    spec = load_spec(spec_path)
    api_key = _read_secret(spec.auth.air_api_key_file, field="auth.air_api_key_file")
    air = NvidiaAirAdapter(api_key=api_key)
    simulation = air.find_simulation(spec.simulation.name)
    if simulation is None:
        raise JumpHostError(f"NVIDIA Air simulation {spec.simulation.name!r} does not exist")
    if simulation.status is not AirSimulationStatus.ACTIVE:
        raise JumpHostError(f"NVIDIA Air simulation {spec.simulation.name!r} is not active")
    jump_host = air.find_jump_host(simulation.id)
    if jump_host is None:
        raise JumpHostError("The jump-host SSH service is not ready")

    api_vip = str(spec.cluster.api_vips[0])
    api_hostname = f"api.{spec.cluster.name}.{spec.cluster.base_dns_domain}"
    source = cache_dir() / spec.simulation.name / "credentials" / "kubeconfig"
    kubeconfig = _tunneled_kubeconfig(source, local_port=local_port, api_hostname=api_hostname)
    _ensure_local_port_available(local_port)
    arguments = _ssh_arguments(jump_host, api_vip=api_vip, local_port=local_port)
    try:
        process = subprocess.Popen(arguments)
    except FileNotFoundError:
        raise ConfigurationError("SSH client not found") from None
    except OSError as exc:
        raise JumpHostError("Could not launch the SSH API tunnel") from exc

    announce(f"API tunnel: https://127.0.0.1:{local_port} -> {api_vip}:6443")
    announce(f"Kubeconfig: {kubeconfig}")
    announce(f"Run: KUBECONFIG={kubeconfig} oc get nodes")
    announce("Press Ctrl-C to stop the tunnel.")
    try:
        return_code = process.wait()
    except KeyboardInterrupt:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        return
    if return_code != 0:
        raise JumpHostError(f"SSH API tunnel exited with status {return_code}")
