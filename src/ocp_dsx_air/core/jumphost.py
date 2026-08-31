import subprocess
import time
from dataclasses import dataclass
from typing import Final

import pexpect
from air_sdk.endpoints import Simulation
from air_sdk.endpoints.nodes import Node
from air_sdk.endpoints.services import Service

from ocp_dsx_air.core.exceptions import JumpHostError
from ocp_dsx_air.core.simulation import get_node
from ocp_dsx_air.models.runtime import ClusterNetworkConfig

# Platform-specific constants for NVIDIA Air's Out-of-Band infrastructure
_OOB_SERVER_NAME: Final = "oob-mgmt-server"
_OOB_SERVER_INTERFACE: Final = "eth0"
_JUMP_HOST_SERVICE_NAME: Final = "dsx-air-ssh"


@dataclass
class JumpHostTarget:
    service: Service
    server: Node

    @property
    def host(self) -> str | None:
        return self.service.worker_fqdn

    @property
    def port(self) -> int | None:
        return self.service.worker_port

    @property
    def default_username(self) -> str:
        return getattr(self.server.image, "default_username", None) or "ubuntu"

    @property
    def default_password(self) -> str:
        return getattr(self.server.image, "default_password", None) or "nvidia"

    @property
    def base_ssh_cmd(self) -> str:
        return f"ssh -tt -o StrictHostKeyChecking=accept-new -p {self.port} {self.default_username}@{self.host}"

    def ssh_run(
        self,
        remote: str,
        *,
        stdin: str | None = None,
        timeout: int = 30,
    ) -> subprocess.CompletedProcess[str]:
        cmd = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "ConnectTimeout=10",
            "-p",
            str(self.port),
            f"{self.default_username}@{self.host}",
            remote,
        ]
        return subprocess.run(
            cmd,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )


def jump_host_ssh_probe(
    target: JumpHostTarget,
    *,
    timeout: int = 20,
) -> tuple[bool, str]:
    """Return (ready, reason). ready=True when non-interactive SSH works."""
    try:
        result = target.ssh_run("echo jump-host-ok", timeout=timeout)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return False, str(exc)

    combined = f"{result.stdout}\n{result.stderr}".lower()
    if result.returncode == 0 and "jump-host-ok" in result.stdout:
        return True, "ok"
    if "password has expired" in combined or "password change required" in combined:
        return False, "password_expired"
    if "connection refused" in combined or "no route to host" in combined:
        return False, "not_reachable"
    detail = (result.stderr or result.stdout or "").strip()
    return False, detail or f"ssh exit {result.returncode}"


def _wait_for_jump_host_ssh(
    target: JumpHostTarget,
    *,
    timeout: int = 120,
    interval: int = 5,
) -> None:
    deadline = time.monotonic() + timeout
    last_reason = "unknown"
    while time.monotonic() < deadline:
        ready, reason = jump_host_ssh_probe(target, timeout=15)
        if ready or reason == "password_expired":
            return
        last_reason = reason
        time.sleep(interval)
    raise JumpHostError(
        f"Timed out after {timeout}s waiting for jump host SSH on {target.base_ssh_cmd!r} (last seen: {last_reason!r})."
    )


def bootstrap_jump_host_password(
    target: JumpHostTarget,
    new_password: str,
    *,
    timeout: int = 60,
) -> None:
    """Clear NVIDIA Air's mandatory first-login password change on oob-mgmt-server.

    Fresh oob-mgmt-server VMs ship with default user ``ubuntu`` / password
    ``nvidia`` and refuse to run commands until the password is changed.
    """
    ready, reason = jump_host_ssh_probe(target, timeout=15)
    if ready:
        print(f"Jump host {target.server.name!r} already accepts non-interactive SSH.")
        return

    if reason not in ("password_expired", "not_reachable"):
        print(f"Jump host SSH probe: {reason!r} — waiting for SSH to come up...")
        _wait_for_jump_host_ssh(target, timeout=120)
        ready, reason = jump_host_ssh_probe(target, timeout=15)
        if ready:
            print(f"Jump host {target.server.name!r} already accepts non-interactive SSH.")
            return

    print(
        f"Bootstrapping jump host password for {target.default_username}@{target.host}:{target.port} "
        f"(factory password -> new password) ..."
    )

    ssh_cmd = target.base_ssh_cmd
    child: pexpect.spawn | None = None
    try:
        # 1. Spawn the fake terminal
        child = pexpect.spawn(ssh_cmd, encoding="utf-8", timeout=timeout)
        # 2. Handle Initial Login (Catching optional host key warnings)
        index = child.expect(
            [r"(?i)are you sure you want to continue connecting", r"(?i)current password:", r"(?i)password:"]
        )

        if index == 0:
            child.sendline("yes")
            # Wait for actual password prompt after accepting key
            child.expect([r"(?i)current password:", r"(?i)password:"])
            child.sendline(target.default_password)
        else:
            child.sendline(target.default_password)

        # 3. Handle Forced Password Rotation
        child.expect(r"New password:")
        child.sendline(new_password)

        child.expect(r"Retype new password:")
        child.sendline(new_password)

        # 4. Verify Success (Matches PAM success message or a standard shell prompt)
        child.expect([r"password updated successfully", r"ubuntu@.*[$#]", r"\$ "])
        # Cleanly close the SSH session
        child.sendline("exit")
        child.close()

    except (pexpect.EOF, pexpect.TIMEOUT) as e:
        if child is not None and isinstance(child.before, str):
            last_output = child.before.strip()
        else:
            last_output = "None"
        raise JumpHostError(
            f"SSH connection closed unexpectedly during password bootstrap. Last seen output: {last_output}"
        ) from e
    # Final Verification Probe
    ready, reason = jump_host_ssh_probe(target, timeout=15)
    if not ready:
        raise JumpHostError(
            f"Jump host password bootstrap appeared to succeed, but non-interactive SSH still fails: {reason!r}"
        )
    print(f"Jump host {target.server.name!r} is ready for non-interactive SSH.")


def _cluster_dns_host_block(network: ClusterNetworkConfig) -> str:
    """Hosts-file block for SOCKS DNS on the jump host."""
    _hosts_begin = "# BEGIN ocp-dsx-air"
    _hosts_end = "# END ocp-dsx-air"
    api_names = f"api.{network.cluster_domain} api-int.{network.cluster_domain}"
    ingress_names = (
        f"console-openshift-console.{network.apps_domain} "
        f"oauth-openshift.{network.apps_domain} "
        f"downloads-openshift-console.{network.apps_domain} "
        f"canary-openshift-ingress-canary.{network.apps_domain}"
    )

    return f"{_hosts_begin}\n{network.api_vip} {api_names}\n{network.ingress_vip} {ingress_names}\n{_hosts_end}\n"


def ensure_jump_host_cluster_dns(target: JumpHostTarget, network: ClusterNetworkConfig) -> None:
    """Idempotently write cluster API/Console names into jump-host /etc/hosts."""
    block = _cluster_dns_host_block(network)

    read = target.ssh_run("cat /etc/hosts")
    if read.returncode != 0:
        raise JumpHostError(
            f"Could not read jump host /etc/hosts for Console DNS: {(read.stderr or read.stdout or '').strip()}"
        )

    merged = merge_hosts_file(read.stdout, block)
    if merged == read.stdout:
        print("Jump host /etc/hosts already has cluster DNS names.")
        return

    write = target.ssh_run("sudo -n tee /etc/hosts >/dev/null", stdin=merged)
    if write.returncode != 0:
        write = target.ssh_run("tee /etc/hosts >/dev/null", stdin=merged)

    if write.returncode != 0:
        raise JumpHostError(
            "Could not update jump host /etc/hosts (need passwordless sudo). "
            f"{(write.stderr or write.stdout or '').strip()}"
        )

    print(f"Jump host /etc/hosts updated for SOCKS DNS (api={network.api_vip} apps={network.ingress_vip}).")


def merge_hosts_file(existing: str, block: str) -> str:
    """Replace or append the dsx-air-ocp hosts block."""

    _hosts_begin = "# BEGIN ocp-dsx-air"
    _hosts_end = "# END ocp-dsx-air"
    start = existing.find(_hosts_begin)
    end = existing.find(_hosts_end)
    body = block if block.endswith("\n") else block + "\n"
    if start != -1 and end != -1 and end >= start:
        after = existing[end + len(_hosts_end) :].lstrip("\n")
        return existing[:start].rstrip("\n") + "\n" + body + after
    text = existing.rstrip("\n")
    if text:
        text += "\n"
    return text + body


def ensure_jump_host_service(sim: Simulation, service_name: str = _JUMP_HOST_SERVICE_NAME) -> tuple[Service, Node]:
    """Idempotently expose oob-mgmt-server's SSH port as an Air Service.

    Reuses an existing SSH service on that interface if one is already
    there (so re-running this doesn't create duplicates), otherwise
    creates one. Returns (service, oob_mgmt_server_node) so callers can
    build the ssh command and know which user to log in as.
    """
    server = get_node(sim, _OOB_SERVER_NAME)

    iface = next((i for i in server.interfaces.list() if i.name == _OOB_SERVER_INTERFACE), None)
    if iface is None:
        raise JumpHostError(f"No {_OOB_SERVER_INTERFACE!r} interface found on node {_OOB_SERVER_NAME!r}.")

    existing = next((svc for svc in iface.services.list() if svc.node_port == 22), None)
    if existing is not None:
        return existing, server

    service = sim.create_service(
        name=service_name,
        node_name=_OOB_SERVER_NAME,
        interface_name=_OOB_SERVER_INTERFACE,
        node_port=22,
        service_type="SSH",
    )
    return service, server


def ensure_jump_host_ready(
    target: JumpHostTarget,
    network_config: ClusterNetworkConfig,
    new_password: str = "dsx-air",
    *,
    skip_bootstrap: bool = False,
) -> None:
    """Expose oob-mgmt-server SSH, clear passwords, and inject DNS."""

    if not skip_bootstrap:
        bootstrap_jump_host_password(target, new_password)

    ready, reason = jump_host_ssh_probe(target, timeout=15)
    if ready:
        ensure_jump_host_cluster_dns(target, network_config)
    else:
        raise JumpHostError(
            f"Jump host password bootstrap appeared to succeed, but non-interactive SSH still fails: {reason!r}"
        )
