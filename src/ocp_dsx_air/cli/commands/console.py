"""Open the OpenShift web console through an Air jump-host SOCKS proxy."""

from __future__ import annotations

import os
import shlex
import shutil
import socket
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ocp_dsx_air.adapters.air import NvidiaAirAdapter
from ocp_dsx_air.cli.commands.access import resolve_active_jump_host
from ocp_dsx_air.cli.commands.deploy import _read_secret
from ocp_dsx_air.core.common import cache_dir
from ocp_dsx_air.core.contracts import JumpHostSnapshot
from ocp_dsx_air.core.exceptions import ConfigurationError, ConsoleError
from ocp_dsx_air.models.spec import load_spec

_BROWSER_NAMES = (
    "chromium",
    "chromium-browser",
    "google-chrome",
    "google-chrome-stable",
    "microsoft-edge",
    "microsoft-edge-stable",
    "brave-browser",
    "brave-browser-stable",
)
_BROWSER_BASENAMES = frozenset(_BROWSER_NAMES)
_FLATPAK_CHROMIUM_ID = "org.chromium.Chromium"


@dataclass(frozen=True, slots=True)
class BrowserLaunch:
    command: tuple[str, ...]
    name: str


def _supported_browser(path: Path) -> bool:
    try:
        return path.is_file() and os.access(path, os.X_OK) and path.resolve().name in _BROWSER_BASENAMES
    except OSError:
        return False


def _desktop_entry_paths(desktop_id: str) -> tuple[Path, ...]:
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    data_dirs = os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share")
    return tuple(
        root / "applications" / desktop_id
        for root in (data_home, *(Path(item) for item in data_dirs.split(":") if item))
    )


def _desktop_entry_executable(desktop_id: str) -> Path | None:
    if not desktop_id or Path(desktop_id).name != desktop_id:
        return None
    for entry in _desktop_entry_paths(desktop_id):
        try:
            lines = entry.read_text().splitlines()
        except OSError:
            continue
        in_desktop_entry = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                in_desktop_entry = stripped == "[Desktop Entry]"
                continue
            if not in_desktop_entry or not stripped.startswith("Exec="):
                continue
            try:
                tokens = shlex.split(stripped.removeprefix("Exec="))
            except ValueError:
                return None
            if not tokens or tokens[0] == "env":
                return None
            executable = shutil.which(tokens[0])
            return Path(executable) if executable else None
    return None


def _system_default_browser() -> Path | None:
    try:
        result = subprocess.run(
            ["xdg-settings", "get", "default-web-browser"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return _desktop_entry_executable(result.stdout.strip())


def _native_browser(path: Path) -> BrowserLaunch:
    resolved = path.resolve()
    return BrowserLaunch((str(resolved),), resolved.name)


def _flatpak_chromium() -> BrowserLaunch | None:
    flatpak = shutil.which("flatpak")
    if flatpak is None:
        return None
    try:
        result = subprocess.run(
            [flatpak, "info", _FLATPAK_CHROMIUM_ID],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return BrowserLaunch((flatpak, "run", _FLATPAK_CHROMIUM_ID), "Chromium (Flatpak)")


def find_browser(override: Path | None = None) -> BrowserLaunch:
    """Return an explicitly selected, default, or PATH Chromium browser."""
    if override is not None:
        candidate = override.expanduser()
        if not _supported_browser(candidate):
            raise ConfigurationError("--browser must identify a supported Chromium-family executable")
        return _native_browser(candidate)

    default = _system_default_browser()
    if default is not None and _supported_browser(default):
        return _native_browser(default)
    for name in _BROWSER_NAMES:
        executable = shutil.which(name)
        if executable is not None:
            candidate = Path(executable)
            if _supported_browser(candidate):
                return _native_browser(candidate)
    flatpak = _flatpak_chromium()
    if flatpak is not None:
        return flatpak
    raise ConfigurationError("No supported browser found; install Chromium, Chrome, Edge, or Brave, or pass --browser")


def _ssh_arguments(jump_host: JumpHostSnapshot, *, socks_port: int) -> list[str]:
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
        "-D",
        f"127.0.0.1:{socks_port}",
        f"{jump_host.username}@{jump_host.host}",
    ]


def _browser_arguments(
    browser: BrowserLaunch,
    *,
    profile: Path,
    socks_port: int,
    cluster_domain: str,
    api_vip: str,
    ingress_vip: str,
    url: str,
) -> list[str]:
    resolver_rules = f"MAP api.{cluster_domain} {api_vip}, MAP *.apps.{cluster_domain} {ingress_vip}, EXCLUDE localhost"
    return [
        *browser.command,
        f"--user-data-dir={profile}",
        f"--proxy-server=socks5://127.0.0.1:{socks_port}",
        f"--host-resolver-rules={resolver_rules}",
        "--disable-features=AsyncDns",
        "--dns-over-https-mode=off",
        "--no-first-run",
        "--no-default-browser-check",
        url,
    ]


def _port_open(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.2)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def _ensure_port_available(port: int) -> None:
    try:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
    except OSError as exc:
        raise ConsoleError(f"Local SOCKS port {port} is unavailable; choose another with --socks-port") from exc


def _stop_process(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def run_console(
    spec_path: Path,
    *,
    browser_override: Path | None = None,
    socks_port: int = 1080,
    print_only: bool = False,
    start: bool = False,
    announce: Callable[[str], None] = print,
) -> None:
    """Launch a browser and own its SOCKS session until either process exits."""
    if not 1 <= socks_port <= 65535:
        raise ConfigurationError("Local SOCKS port must be between 1 and 65535")
    spec = load_spec(spec_path)
    browser = find_browser(browser_override)
    api_key = _read_secret(spec.auth.air_api_key_file, field="auth.air_api_key_file")
    air = NvidiaAirAdapter(api_key=api_key)
    jump_host = resolve_active_jump_host(
        air,
        spec.simulation.name,
        spec_path=spec_path,
        start=start,
        require_reachable=not print_only,
        error_type=ConsoleError,
        announce=announce,
    )

    cluster_domain = f"{spec.cluster.name}.{spec.cluster.base_dns_domain}"
    url = f"https://console-openshift-console.apps.{cluster_domain}"
    profile = cache_dir() / spec.simulation.name / "browser-profile"
    password = cache_dir() / spec.simulation.name / "credentials" / "kubeadmin-password"
    ssh_arguments = _ssh_arguments(jump_host, socks_port=socks_port)
    browser_arguments = _browser_arguments(
        browser,
        profile=profile,
        socks_port=socks_port,
        cluster_domain=cluster_domain,
        api_vip=str(spec.cluster.api_vips[0]),
        ingress_vip=str(spec.cluster.ingress_vips[0]),
        url=url,
    )
    announce(f"Console: {url}")
    announce("Username: kubeadmin")
    announce(f"Password file: {password}")
    if print_only:
        announce(shlex.join(ssh_arguments))
        announce(shlex.join(browser_arguments))
        return

    _ensure_port_available(socks_port)
    try:
        if profile.is_symlink():
            raise ConsoleError("Browser profile path cannot be a symbolic link")
        profile.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not profile.is_dir():
            raise ConsoleError("Browser profile path must be a directory")
        profile.chmod(0o700)
    except ConsoleError:
        raise
    except OSError as exc:
        raise ConsoleError("Could not prepare the browser profile") from exc

    ssh_process: subprocess.Popen[bytes] | None = None
    browser_process: subprocess.Popen[bytes] | None = None
    try:
        try:
            ssh_process = subprocess.Popen(
                ssh_arguments,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except FileNotFoundError:
            raise ConfigurationError("SSH client not found") from None
        except OSError as exc:
            raise ConsoleError("Could not launch the SSH SOCKS proxy") from exc
        deadline = time.monotonic() + 20
        while not _port_open(socks_port):
            return_code = ssh_process.poll()
            if return_code is not None:
                raise ConsoleError(f"SSH SOCKS proxy exited with status {return_code} before becoming ready")
            if time.monotonic() >= deadline:
                raise ConsoleError("Timed out waiting for the SSH SOCKS proxy")
            time.sleep(0.2)
        try:
            browser_process = subprocess.Popen(
                browser_arguments,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
            raise ConsoleError("Could not launch the console browser") from exc
        announce("Console session is ready. Press Ctrl-C to close it.")
        while browser_process.poll() is None:
            return_code = ssh_process.poll()
            if return_code is not None:
                raise ConsoleError(f"SSH SOCKS proxy exited with status {return_code}")
            time.sleep(0.5)
    except KeyboardInterrupt:
        announce("Stopping Console session.")
    finally:
        _stop_process(browser_process)
        _stop_process(ssh_process)
