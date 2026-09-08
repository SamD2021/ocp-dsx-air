from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from ocp_dsx_air.cli.commands import console
from ocp_dsx_air.core.contracts import AirSimulationStatus, JumpHostSnapshot
from ocp_dsx_air.core.exceptions import ConfigurationError, ConsoleError


def _executable(path: Path, name: str) -> Path:
    executable = path / name
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o755)
    return executable


def _write_spec(path: Path) -> None:
    path.write_text(
        """simulation: {name: dsx-lab}
cluster:
  name: ocp
  version: "4.19"
  base_dns_domain: example.test
  machine_networks: [192.168.200.0/24]
  api_vips: [192.168.200.10]
  ingress_vips: [192.168.200.11]
  control_plane: {count: 1}
auth:
  air_api_key_file: op://Private/Air/credential
"""
    )


def test_find_browser_prefers_explicit_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    override = _executable(tmp_path, "chromium")
    monkeypatch.setattr(console, "_system_default_browser", lambda: pytest.fail("unused"))

    assert console.find_browser(override).command == (str(override.resolve()),)


def test_find_browser_uses_supported_system_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    default = _executable(tmp_path, "google-chrome-stable")
    monkeypatch.setattr(console, "_system_default_browser", lambda: default)
    monkeypatch.setattr(console.shutil, "which", lambda name: pytest.fail("unused"))

    assert console.find_browser().command == (str(default.resolve()),)


def test_find_browser_falls_back_from_unsupported_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    unsupported = _executable(tmp_path, "firefox")
    fallback = _executable(tmp_path, "brave-browser")
    monkeypatch.setattr(console, "_system_default_browser", lambda: unsupported)
    monkeypatch.setattr(
        console.shutil,
        "which",
        lambda name: str(fallback) if name == "brave-browser" else None,
    )

    assert console.find_browser().command == (str(fallback.resolve()),)


def test_find_browser_rejects_unsupported_override(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="Chromium-family"):
        console.find_browser(_executable(tmp_path, "firefox"))


def test_find_browser_fails_when_no_supported_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(console, "_system_default_browser", lambda: None)
    monkeypatch.setattr(console.shutil, "which", lambda name: None)

    with pytest.raises(ConfigurationError, match="No supported browser"):
        console.find_browser()


def test_find_browser_falls_back_to_flatpak_chromium(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(console, "_system_default_browser", lambda: None)
    monkeypatch.setattr(
        console.shutil,
        "which",
        lambda name: "/usr/bin/flatpak" if name == "flatpak" else None,
    )
    monkeypatch.setattr(
        console.subprocess,
        "run",
        lambda args, **kwargs: SimpleNamespace(returncode=0),
    )

    browser = console.find_browser()

    assert browser.command == (
        "/usr/bin/flatpak",
        "run",
        "org.chromium.Chromium",
    )


def test_desktop_entry_resolution_does_not_execute_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    applications = tmp_path / "applications"
    applications.mkdir()
    browser = _executable(tmp_path, "chromium")
    (applications / "chromium.desktop").write_text(f"[Desktop Entry]\nName=Browser\nExec={browser} --new-window %U\n")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_DIRS", "")
    monkeypatch.setattr(console.shutil, "which", lambda name: name)

    assert console._desktop_entry_executable("chromium.desktop") == browser
    assert console._desktop_entry_executable("../unsafe.desktop") is None


def test_desktop_entry_rejects_shell_wrapper_and_malformed_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    applications = tmp_path / "applications"
    applications.mkdir()
    (applications / "wrapped.desktop").write_text("[Desktop Entry]\nExec=env TOKEN=value chromium %U\n")
    (applications / "malformed.desktop").write_text("[Desktop Entry]\nExec='unterminated\n")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_DIRS", "")

    assert console._desktop_entry_executable("wrapped.desktop") is None
    assert console._desktop_entry_executable("malformed.desktop") is None


def test_browser_arguments_configure_proxy_routes_and_profile(tmp_path: Path) -> None:
    arguments = console._browser_arguments(
        console.BrowserLaunch(("/usr/bin/chromium",), "chromium"),
        profile=tmp_path / "profile",
        socks_port=1081,
        cluster_domain="ocp.example.test",
        api_vip="192.168.200.10",
        ingress_vip="192.168.200.11",
        url="https://console-openshift-console.apps.ocp.example.test",
    )
    joined = " ".join(arguments)

    assert "--proxy-server=socks5://127.0.0.1:1081" in arguments
    assert "MAP api.ocp.example.test 192.168.200.10" in joined
    assert "MAP *.apps.ocp.example.test 192.168.200.11" in joined
    assert f"--user-data-dir={tmp_path / 'profile'}" in arguments
    assert not any("ignore-certificate" in item for item in arguments)
    assert not any("ozone-platform" in item for item in arguments)


class FakeProcess:
    def __init__(self, return_code: int | None) -> None:
        self.return_code = return_code
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.return_code

    def wait(self, timeout: float | None = None) -> int:
        self.return_code = 0
        return 0

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True


class InterruptingProcess(FakeProcess):
    interrupted = False

    def poll(self) -> int | None:
        if not self.interrupted:
            self.interrupted = True
            raise KeyboardInterrupt
        return self.return_code


def _configure_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, SimpleNamespace]:
    spec = tmp_path / "spec.yaml"
    _write_spec(spec)
    browser = _executable(tmp_path, "chromium")
    simulation = SimpleNamespace(id=UUID(int=1), status=AirSimulationStatus.ACTIVE)
    air = SimpleNamespace(
        find_simulation=lambda name: simulation,
        find_jump_host=lambda simulation_id: JumpHostSnapshot(UUID(int=2), "worker.example.test", 22022, "ubuntu"),
    )
    monkeypatch.setattr(
        console,
        "find_browser",
        lambda override: console.BrowserLaunch((str(browser),), "chromium"),
    )
    monkeypatch.setattr(console, "_read_secret", lambda *args, **kwargs: "api-key")
    monkeypatch.setattr(console, "NvidiaAirAdapter", lambda **kwargs: air)
    monkeypatch.setattr(console, "cache_dir", lambda: tmp_path / "cache")
    return spec, browser, air


def test_print_only_emits_safe_commands_without_launching(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spec, browser, _ = _configure_runtime(tmp_path, monkeypatch)
    messages: list[str] = []
    monkeypatch.setattr(console.subprocess, "Popen", lambda args: pytest.fail("must not launch"))

    console.run_console(spec, print_only=True, announce=messages.append)

    assert any("ssh -N -T" in message for message in messages)
    assert any(str(browser) in message for message in messages)
    assert any("Password file:" in message for message in messages)
    assert not (tmp_path / "cache" / "dsx-lab" / "browser-profile").exists()
    assert "api-key" not in "\n".join(messages)


def test_console_launches_browser_then_stops_proxy_on_normal_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec, _, _ = _configure_runtime(tmp_path, monkeypatch)
    ssh = FakeProcess(None)
    browser = FakeProcess(0)
    processes = iter((ssh, browser))
    calls: list[tuple[list[str], dict[str, object]]] = []
    monkeypatch.setattr(console, "_ensure_port_available", lambda port: None)
    monkeypatch.setattr(console, "_port_open", lambda port: True)
    monkeypatch.setattr(
        console.subprocess,
        "Popen",
        lambda args, **kwargs: calls.append((args, kwargs)) or next(processes),
    )

    console.run_console(spec, announce=lambda message: None)

    assert "127.0.0.1:1080" in calls[0][0]
    assert calls[0][0].count("-L") == 0
    assert calls[0][0].count("-D") == 1
    assert all(call[1]["start_new_session"] is True for call in calls)
    assert all(call[1]["stdout"] is console.subprocess.DEVNULL for call in calls)
    assert all(call[1]["stderr"] is console.subprocess.DEVNULL for call in calls)
    assert ssh.terminated is True
    profile = tmp_path / "cache" / "dsx-lab" / "browser-profile"
    assert profile.is_dir()
    assert profile.stat().st_mode & 0o777 == 0o700


def test_console_reports_early_ssh_exit_without_launching_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec, _, _ = _configure_runtime(tmp_path, monkeypatch)
    ssh = FakeProcess(255)
    monkeypatch.setattr(console, "_ensure_port_available", lambda port: None)
    monkeypatch.setattr(console, "_port_open", lambda port: False)
    monkeypatch.setattr(console.subprocess, "Popen", lambda args, **kwargs: ssh)

    with pytest.raises(ConsoleError, match="status 255") as error:
        console.run_console(spec, announce=lambda message: None)
    assert "api-key" not in str(error.value)


def test_console_stops_proxy_when_browser_launch_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spec, _, _ = _configure_runtime(tmp_path, monkeypatch)
    ssh = FakeProcess(None)
    calls = 0

    def launch(arguments: list[str], **kwargs: object) -> FakeProcess:
        nonlocal calls
        calls += 1
        if calls == 1:
            return ssh
        raise OSError("sensitive browser failure")

    monkeypatch.setattr(console, "_ensure_port_available", lambda port: None)
    monkeypatch.setattr(console, "_port_open", lambda port: True)
    monkeypatch.setattr(console.subprocess, "Popen", launch)

    with pytest.raises(ConsoleError, match="Could not launch") as error:
        console.run_console(spec, announce=lambda message: None)
    assert "sensitive" not in str(error.value)
    assert ssh.terminated is True


def test_console_stops_both_processes_on_keyboard_interrupt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spec, _, _ = _configure_runtime(tmp_path, monkeypatch)
    ssh = FakeProcess(None)
    browser = InterruptingProcess(None)
    processes = iter((ssh, browser))
    messages: list[str] = []
    monkeypatch.setattr(console, "_ensure_port_available", lambda port: None)
    monkeypatch.setattr(console, "_port_open", lambda port: True)
    monkeypatch.setattr(console.subprocess, "Popen", lambda args, **kwargs: next(processes))

    console.run_console(spec, announce=messages.append)

    assert ssh.terminated is True
    assert browser.terminated is True
    assert "Stopping Console session." in messages


def test_console_rejects_occupied_socks_port() -> None:
    import socket

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        with pytest.raises(ConsoleError, match="unavailable"):
            console._ensure_port_available(port)


def test_console_rejects_symlink_browser_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spec, _, _ = _configure_runtime(tmp_path, monkeypatch)
    profile = tmp_path / "cache" / "dsx-lab" / "browser-profile"
    profile.parent.mkdir(parents=True)
    profile.symlink_to(tmp_path, target_is_directory=True)
    monkeypatch.setattr(console, "_ensure_port_available", lambda port: None)
    monkeypatch.setattr(console.subprocess, "Popen", lambda args: pytest.fail("must not launch"))

    with pytest.raises(ConsoleError, match="symbolic link"):
        console.run_console(spec, announce=lambda message: None)


@pytest.mark.parametrize(
    ("simulation", "jump_host", "message"),
    [
        (None, None, "does not exist"),
        (
            SimpleNamespace(id=UUID(int=1), status=AirSimulationStatus.INACTIVE),
            None,
            "not active",
        ),
        (
            SimpleNamespace(id=UUID(int=1), status=AirSimulationStatus.ACTIVE),
            None,
            "not ready",
        ),
    ],
)
def test_console_fails_before_processes_for_unavailable_lab(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    simulation: object,
    jump_host: object,
    message: str,
) -> None:
    spec, _, _ = _configure_runtime(tmp_path, monkeypatch)
    air = SimpleNamespace(
        find_simulation=lambda name: simulation,
        find_jump_host=lambda simulation_id: jump_host,
    )
    monkeypatch.setattr(console, "NvidiaAirAdapter", lambda **kwargs: air)
    monkeypatch.setattr(console.subprocess, "Popen", lambda args: pytest.fail("must not launch"))

    with pytest.raises(ConsoleError, match=message):
        console.run_console(spec, announce=lambda message: None)
