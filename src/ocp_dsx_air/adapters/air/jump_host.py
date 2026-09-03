"""SSH-side jump-host setup without NVIDIA Air SDK dependencies."""

import shlex
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import pexpect

from ocp_dsx_air.core.exceptions import JumpHostError
from ocp_dsx_air.core.jumphost import cluster_dns_host_block, merge_hosts_file
from ocp_dsx_air.models.runtime import ClusterNetworkConfig


@dataclass(frozen=True, slots=True)
class JumpHostTarget:
    host: str
    port: int
    username: str
    initial_password: str


class JumpHostExecutor(Protocol):
    def ensure_ready(
        self,
        target: JumpHostTarget,
        network: ClusterNetworkConfig,
        *,
        new_password: str,
        timeout_seconds: float,
    ) -> None: ...


_Run = Callable[..., subprocess.CompletedProcess[str]]


class SshJumpHostExecutor:
    """Prepare a jump host using the Air-injected SSH public key."""

    def __init__(self, *, _run: _Run = subprocess.run) -> None:
        self._run = _run

    @staticmethod
    def _command(target: JumpHostTarget, remote: str) -> list[str]:
        return [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "ConnectTimeout=10",
            "-p",
            str(target.port),
            f"{target.username}@{target.host}",
            remote,
        ]

    def _run_ssh(
        self,
        target: JumpHostTarget,
        remote: str,
        *,
        stdin: str | None = None,
        timeout: float = 30,
    ) -> subprocess.CompletedProcess[str]:
        try:
            return self._run(
                self._command(target, remote),
                input=stdin,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise JumpHostError("Could not run the jump-host SSH client") from exc

    def _probe(self, target: JumpHostTarget) -> tuple[bool, bool]:
        result = self._run_ssh(target, "echo jump-host-ok", timeout=15)
        combined = f"{result.stdout}\n{result.stderr}".lower()
        ready = result.returncode == 0 and "jump-host-ok" in result.stdout
        password_expired = (
            "password has expired" in combined
            or "password change required" in combined
        )
        return ready, password_expired

    @staticmethod
    def _rotate_password(
        target: JumpHostTarget,
        new_password: str,
        *,
        timeout_seconds: float,
    ) -> None:
        command = " ".join(
            shlex.quote(part)
            for part in [
                "ssh",
                "-tt",
                "-o",
                "StrictHostKeyChecking=accept-new",
                "-p",
                str(target.port),
                f"{target.username}@{target.host}",
            ]
        )
        child: pexpect.spawn | None = None
        try:
            child = pexpect.spawn(
                command,
                encoding="utf-8",
                timeout=timeout_seconds,
            )
            prompt = child.expect(
                [
                    r"(?i)are you sure you want to continue connecting",
                    r"(?i)current password:",
                    r"(?i)password:",
                ]
            )
            if prompt == 0:
                child.sendline("yes")
                child.expect([r"(?i)current password:", r"(?i)password:"])
            child.sendline(target.initial_password)
            child.expect(r"(?i)new password:")
            child.sendline(new_password)
            child.expect(r"(?i)retype new password:")
            child.sendline(new_password)
            child.expect([r"(?i)password updated successfully", r"[$#] "])
            child.sendline("exit")
            child.close()
        except (pexpect.EOF, pexpect.TIMEOUT) as exc:
            raise JumpHostError("Jump-host password rotation failed") from exc
        finally:
            if child is not None and child.isalive():
                child.close(force=True)

    def _configure_dns(
        self,
        target: JumpHostTarget,
        network: ClusterNetworkConfig,
    ) -> None:
        read = self._run_ssh(target, "cat /etc/hosts")
        if read.returncode != 0:
            raise JumpHostError("Could not read the jump-host hosts file")
        merged = merge_hosts_file(read.stdout, cluster_dns_host_block(network))
        if merged == read.stdout:
            return
        write = self._run_ssh(
            target,
            "sudo -n tee /etc/hosts >/dev/null",
            stdin=merged,
        )
        if write.returncode != 0:
            write = self._run_ssh(
                target,
                "tee /etc/hosts >/dev/null",
                stdin=merged,
            )
        if write.returncode != 0:
            raise JumpHostError("Could not update the jump-host hosts file")

    def ensure_ready(
        self,
        target: JumpHostTarget,
        network: ClusterNetworkConfig,
        *,
        new_password: str,
        timeout_seconds: float,
    ) -> None:
        if not new_password:
            raise JumpHostError("The jump-host password must not be empty")
        deadline = time.monotonic() + timeout_seconds
        while True:
            ready, password_expired = self._probe(target)
            if ready:
                break
            if password_expired:
                self._rotate_password(
                    target,
                    new_password,
                    timeout_seconds=max(1, deadline - time.monotonic()),
                )
                continue
            if time.monotonic() >= deadline:
                raise JumpHostError("Timed out waiting for jump-host SSH")
            time.sleep(min(5, max(0, deadline - time.monotonic())))
        self._configure_dns(target, network)
