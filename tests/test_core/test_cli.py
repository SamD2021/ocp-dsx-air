from pathlib import Path
from types import SimpleNamespace

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


def test_run_deploy_constructs_one_air_adapter_and_converts_timeout_once(
    tmp_path: Path,
    monkeypatch,
) -> None:
    secret_paths = {}
    for name in ("air", "assisted", "pull", "ssh", "jump"):
        path = tmp_path / name
        path.write_text(f"{name}-secret")
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
