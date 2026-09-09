import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from ocp_dsx_air.cli import main
from ocp_dsx_air.cli.commands import deploy as deploy_module, destroy as destroy_module
from ocp_dsx_air.cli.main import app
from ocp_dsx_air.core.contracts import CredentialPaths, DeployIntent
from ocp_dsx_air.core.exceptions import AssistedError, ConfigurationError

runner = CliRunner()


def test_app():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "OpenShift on NVIDIA DSX Air: deploy, operate, and open Console." in result.stdout


def test_deploy_help():
    result = runner.invoke(app, ["deploy", "--help"])
    assert result.exit_code == 0
    assert "Create Assisted cluster, Air sim, install OpenShift, download kubeconfig." in result.stdout


def test_destroy_help() -> None:
    result = runner.invoke(app, ["destroy", "--help"])

    assert result.exit_code == 0
    assert "complete remote lab" in result.stdout


def test_destroy_command_passes_overrides_and_skips_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec_path = tmp_path / "lab.yaml"
    spec_path.write_text("placeholder")
    resolved = SimpleNamespace(
        simulation=SimpleNamespace(name="override-sim"),
        cluster=SimpleNamespace(name="override-cluster"),
    )
    loads: list[tuple[Path, str | None, str | None]] = []
    destroyed: list[object] = []
    monkeypatch.setattr(
        main,
        "load_destroy_spec",
        lambda path, *, sim, cluster: (
            loads.append((path, sim, cluster)) or resolved
        ),
    )
    monkeypatch.setattr(main, "run_destroy", destroyed.append)

    result = runner.invoke(
        app,
        [
            "destroy",
            "--spec",
            str(spec_path),
            "--sim",
            "override-sim",
            "--cluster",
            "override-cluster",
            "--yes",
        ],
    )

    assert result.exit_code == 0
    assert loads == [(spec_path, "override-sim", "override-cluster")]
    assert destroyed == [resolved]
    assert "Destroy complete." in result.stdout


def test_destroy_command_requires_yes_when_noninteractive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec_path = tmp_path / "lab.yaml"
    spec_path.write_text("placeholder")
    resolved = SimpleNamespace(
        simulation=SimpleNamespace(name="dsx-lab"),
        cluster=SimpleNamespace(name="ocp"),
    )
    monkeypatch.setattr(main, "load_destroy_spec", lambda *args, **kwargs: resolved)
    monkeypatch.setattr(main, "_stdin_is_interactive", lambda: False)
    monkeypatch.setattr(
        main,
        "run_destroy",
        lambda _: pytest.fail("Destroy must not run without confirmation"),
    )

    result = runner.invoke(app, ["destroy", "--spec", str(spec_path)])

    assert result.exit_code == 1
    assert "without --yes" in result.stderr


@pytest.mark.parametrize(("answer", "destroyed"), [("y\n", True), ("n\n", False)])
def test_destroy_command_prompts_on_an_interactive_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    answer: str,
    destroyed: bool,
) -> None:
    spec_path = tmp_path / "lab.yaml"
    spec_path.write_text("placeholder")
    resolved = SimpleNamespace(
        simulation=SimpleNamespace(name="dsx-lab"),
        cluster=SimpleNamespace(name="ocp"),
    )
    calls: list[object] = []
    monkeypatch.setattr(main, "load_destroy_spec", lambda *args, **kwargs: resolved)
    monkeypatch.setattr(main, "_stdin_is_interactive", lambda: True)
    monkeypatch.setattr(main, "run_destroy", calls.append)

    result = runner.invoke(
        app,
        ["destroy", "--spec", str(spec_path)],
        input=answer,
    )

    assert (result.exit_code == 0) is destroyed
    assert calls == ([resolved] if destroyed else [])
    assert "dsx-lab" in result.output
    assert "ocp" in result.output


def test_tunnel_help() -> None:
    result = runner.invoke(app, ["tunnel", "--help"])
    assert result.exit_code == 0
    assert "foreground SSH tunnel" in result.stdout


def test_console_help() -> None:
    result = runner.invoke(app, ["console", "--help"])
    assert result.exit_code == 0
    assert "private OpenShift Console" in result.stdout


def test_console_command_passes_browser_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = tmp_path / "lab.yaml"
    spec.write_text("simulation: {name: ignored}\ncluster: {name: ignored}\n")
    calls: list[tuple[Path, dict[str, object]]] = []
    monkeypatch.setattr(
        main,
        "run_console",
        lambda path, **kwargs: calls.append((path, kwargs)),
    )

    result = runner.invoke(
        app,
        [
            "console",
            "--spec",
            str(spec),
            "--browser",
            "/opt/chromium",
            "--socks-port",
            "1081",
            "--print-only",
        ],
    )

    assert result.exit_code == 0
    assert calls == [
        (
            spec,
            {
                "browser_override": Path("/opt/chromium"),
                "socks_port": 1081,
                "print_only": True,
                "announce": main.typer.echo,
            },
        )
    ]


def test_deploy_command_passes_overrides_and_renders_result(
    tmp_path: Path,
    monkeypatch,
) -> None:
    spec = tmp_path / "lab.yaml"
    spec.write_text("simulation: {name: ignored}\ncluster: {name: ignored}\n")
    calls: list[tuple[Path, dict[str, object]]] = []
    kubeconfig = tmp_path / "kubeconfig"
    password = tmp_path / "kubeadmin-password"

    def fake_run(path: Path, **kwargs: object):
        calls.append((path, kwargs))
        return SimpleNamespace(credentials=CredentialPaths(kubeconfig, password))

    monkeypatch.setattr(main, "run_deploy", fake_run)

    result = runner.invoke(
        app,
        [
            "deploy",
            "--spec",
            str(spec),
            "--sim",
            "override-sim",
            "--discovery-timeout",
            "17",
        ],
    )

    assert result.exit_code == 0
    assert calls[0][1]["sim"] == "override-sim"
    assert calls[0][1]["discovery_timeout_minutes"] == 17
    assert f"Kubeconfig: {kubeconfig}" in result.stdout


def test_deploy_command_translates_domain_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    spec = tmp_path / "lab.yaml"
    spec.write_text("simulation: {name: ignored}\ncluster: {name: ignored}\n")
    monkeypatch.setattr(
        main,
        "run_deploy",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssistedError("lookup failed")),
    )

    result = runner.invoke(app, ["deploy", "--spec", str(spec)])

    assert result.exit_code == 1
    assert "Deployment failed: lookup failed" in result.stderr


@pytest.mark.parametrize("source", ["files", "references", "mixed"])
def test_run_deploy_constructs_one_air_adapter_and_converts_timeout_once(
    source: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    secret_paths = {}
    values = {
        "air": "air-secret",
        "assisted": "assisted-secret",
        "pull": '{\n  "auths": {}\n}',
        "ssh": "ssh-ed25519 AAAA synthetic-key",
        "jump": " password with spaces ",
    }
    references = {}
    calls = []

    def fake_op(args, **kwargs):
        calls.append(args)
        assert args[:3] == ["op", "read", "--no-newline"]
        assert kwargs == {
            "capture_output": True,
            "text": True,
            "timeout": 120,
            "check": False,
        }
        return subprocess.CompletedProcess(args, 0, references[args[3]] + "\r\n", "")

    monkeypatch.setattr(deploy_module.subprocess, "run", fake_op)
    for name in ("air", "assisted", "pull", "ssh", "jump"):
        path = tmp_path / name
        if source == "references" or (source == "mixed" and name in {"pull", "ssh"}):
            reference = f"op://Private/Test Item/{name}"
            references[reference] = values[name]
            secret_paths[name] = reference
        else:
            path.write_text(values[name] + "\r\n")
            secret_paths[name] = path
    spec = tmp_path / "lab.yaml"
    spec.write_text(
        f"""simulation:
  name: dsx-lab
cluster:
  name: ocp
  version: "4.19"
  control_plane: {{count: 1}}
auth:
  air_api_key_file: {secret_paths["air"]}
  ai_offlinetoken_file: {secret_paths["assisted"]}
  pull_secret_file: {secret_paths["pull"]}
  ssh_public_key_file: {secret_paths["ssh"]}
  jump_host_password_file: {secret_paths["jump"]}
"""
    )
    assisted = object()
    air = object()
    monkeypatch.setattr(deploy_module, "cache_dir", lambda: tmp_path / "cache")
    monkeypatch.setattr(
        deploy_module,
        "AssistedInstallerAdapter",
        lambda token: assisted,
    )
    monkeypatch.setattr(
        deploy_module,
        "NvidiaAirAdapter",
        lambda *, api_key: air,
    )
    captured = {}

    def fake_deploy(intent, **kwargs):
        captured["intent"] = intent
        captured.update(kwargs)
        return SimpleNamespace(credentials=CredentialPaths(tmp_path / "k", tmp_path / "p"))

    monkeypatch.setattr(deploy_module, "deploy_lab", fake_deploy)

    deploy_module.run_deploy(spec, discovery_timeout_minutes=17)

    assert captured["intent"].timeouts.discovery_seconds == 17 * 60
    assert captured["air"] is air
    assert captured["jump_host"] is air
    assert captured["assisted"] is assisted

    assert len(calls) == len(references)
    credentials = captured["credentials"]
    assert credentials.air_api_key == values["air"]
    assert credentials.ai_offline_token == values["assisted"]
    assert credentials.pull_secret == values["pull"]
    assert credentials.ssh_public_key == values["ssh"]
    assert credentials.jump_host_password == values["jump"]


def test_run_destroy_reads_only_service_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ocp_dsx_air.models.spec import LabSpec

    spec = LabSpec.model_validate(
        {
            "simulation": {"name": "dsx-lab"},
            "cluster": {
                "name": "ocp",
                "version": "4.19",
                "control_plane": {"count": 1},
            },
            "auth": {
                "air_api_key_file": "op://Private/Test/air",
                "ai_offlinetoken_file": "op://Private/Test/assisted",
            },
        }
    )
    reads: list[tuple[str | None, str]] = []

    def fake_read(path: str | None, *, field: str) -> str:
        reads.append((path, field))
        return f"resolved-{field}"

    assisted = object()
    air = object()
    captured: dict[str, object] = {}
    monkeypatch.setattr(destroy_module, "_read_secret", fake_read)
    monkeypatch.setattr(destroy_module, "cache_dir", lambda: tmp_path / "cache")

    def fake_assisted(token: str) -> object:
        captured["assisted_token"] = token
        return assisted

    def fake_air(*, api_key: str) -> object:
        captured["air_api_key"] = api_key
        return air

    monkeypatch.setattr(destroy_module, "AssistedInstallerAdapter", fake_assisted)
    monkeypatch.setattr(destroy_module, "NvidiaAirAdapter", fake_air)

    def fake_destroy(intent, **kwargs):
        captured["intent"] = intent
        captured.update(kwargs)

    monkeypatch.setattr(destroy_module, "destroy_lab", fake_destroy)

    destroy_module.run_destroy(spec)

    assert reads == [
        ("op://Private/Test/air", "auth.air_api_key_file"),
        ("op://Private/Test/assisted", "auth.ai_offlinetoken_file"),
    ]
    assert captured["air_api_key"] == "resolved-auth.air_api_key_file"
    assert captured["assisted_token"] == "resolved-auth.ai_offlinetoken_file"
    assert captured["air"] is air
    assert captured["assisted"] is assisted
    assert isinstance(captured["intent"], DeployIntent)
    assert captured["intent"].simulation_name == "dsx-lab"


def test_load_destroy_spec_applies_name_overrides(tmp_path: Path) -> None:
    spec_path = tmp_path / "lab.yaml"
    spec_path.write_text(
        """simulation: {name: original-sim}
cluster:
  name: original-cluster
  version: "4.19"
  control_plane: {count: 1}
"""
    )

    spec = destroy_module.load_destroy_spec(
        spec_path,
        sim="replacement-sim",
        cluster="replacement-cluster",
    )

    assert spec.simulation.name == "replacement-sim"
    assert spec.cluster.name == "replacement-cluster"


def test_destroy_credential_failure_prevents_client_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ocp_dsx_air.models.spec import LabSpec

    spec = LabSpec.model_validate(
        {
            "simulation": {"name": "dsx-lab"},
            "cluster": {
                "name": "ocp",
                "version": "4.19",
                "control_plane": {"count": 1},
            },
            "auth": {
                "air_api_key_file": "op://Private/Test/air",
                "ai_offlinetoken_file": "op://Private/Test/assisted",
            },
        }
    )

    def fake_read(path: str | None, *, field: str) -> str:
        if field == "auth.ai_offlinetoken_file":
            raise ConfigurationError(
                "1Password read failed (auth.ai_offlinetoken_file)"
            )
        return "synthetic-secret"

    def forbidden(*args, **kwargs):
        pytest.fail("Service clients must not be constructed after credential failure")

    monkeypatch.setattr(destroy_module, "_read_secret", fake_read)
    monkeypatch.setattr(destroy_module, "AssistedInstallerAdapter", forbidden)
    monkeypatch.setattr(destroy_module, "NvidiaAirAdapter", forbidden)

    with pytest.raises(
        ConfigurationError,
        match=r"auth\.ai_offlinetoken_file",
    ) as raised:
        destroy_module.run_destroy(spec)

    assert "synthetic-secret" not in str(raised.value)


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        (FileNotFoundError("sensitive details"), "not found"),
        (PermissionError("sensitive details"), "Could not run"),
        (subprocess.TimeoutExpired("op", 120, output="sensitive details"), "timed out"),
        (subprocess.CompletedProcess([], 1, "sensitive details", "sensitive details"), "read failed"),
        (subprocess.CompletedProcess([], 0, " \r\n\t", ""), "empty credential"),
    ],
)
@pytest.mark.parametrize(
    "field",
    ["air_api_key_file", "ai_offlinetoken_file", "pull_secret_file", "ssh_public_key_file", "jump_host_password_file"],
)
def test_op_failure_stops_deployment_without_disclosing_output(
    tmp_path: Path,
    monkeypatch,
    failure,
    message: str,
    field: str,
) -> None:
    from ocp_dsx_air.models.spec import AuthSpec, LabSpec

    spec = LabSpec.model_validate(
        {
            "simulation": {"name": "test"},
            "cluster": {"name": "test", "version": "4.19", "control_plane": {"count": 1}},
            "auth": {name: f"op://Private/Test/{name}" for name in AuthSpec.model_fields},
        }
    )
    monkeypatch.setattr(deploy_module, "load_spec", lambda _: spec)

    def fake_op(args, **kwargs):
        if args[-1].endswith("/" + field):
            if isinstance(failure, Exception):
                raise failure
            return failure
        return subprocess.CompletedProcess(args, 0, "synthetic value", "")

    def forbidden(*args, **kwargs):
        pytest.fail("Credential resolution must finish before constructing service clients")

    monkeypatch.setattr(deploy_module.subprocess, "run", fake_op)
    monkeypatch.setattr(deploy_module, "AssistedInstallerAdapter", forbidden)
    monkeypatch.setattr(deploy_module, "NvidiaAirAdapter", forbidden)
    path = tmp_path / "placeholder.yaml"
    path.write_text("placeholder")
    result = runner.invoke(app, ["deploy", "--spec", str(path)])
    assert result.exit_code == 1
    assert message in result.stderr
    assert f"auth.{field}" in result.stderr
    assert "sensitive details" not in result.output


def test_reference_preflight_does_not_access_filesystem(monkeypatch) -> None:
    from ocp_dsx_air.models import spec as spec_module

    spec = spec_module.LabSpec.model_validate(
        {
            "simulation": {"name": "test"},
            "cluster": {"name": "test", "version": "4.19", "control_plane": {"count": 1}},
            "auth": dict.fromkeys(spec_module.AuthSpec.model_fields, "op://Private/Test/value"),
        }
    )

    def forbidden(*args, **kwargs):
        pytest.fail("References must not be treated as filesystem paths")

    monkeypatch.setattr(spec_module, "expand_path", forbidden)
    spec_module.preflight_auth(spec)
