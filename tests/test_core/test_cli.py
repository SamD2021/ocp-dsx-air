import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from ocp_dsx_air.cli import main
from ocp_dsx_air.cli.commands import deploy as deploy_module
from ocp_dsx_air.cli.main import app
from ocp_dsx_air.core.contracts import CredentialPaths
from ocp_dsx_air.core.exceptions import AssistedError

runner = CliRunner()


def test_app():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "OpenShift on NVIDIA DSX Air: deploy, operate, and open Console." in result.stdout


def test_deploy_help():
    result = runner.invoke(app, ["deploy", "--help"])
    assert result.exit_code == 0
    assert "Create Assisted cluster, Air sim, install OpenShift, download kubeconfig." in result.stdout


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
