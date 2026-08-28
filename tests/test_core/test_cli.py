from typer.testing import CliRunner

from ocp_dsx_air.cli.main import app

runner = CliRunner()


def test_app():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "OpenShift on NVIDIA DSX Air: deploy, operate, and open Console." in result.stdout


def test_deploy_help():
    result = runner.invoke(app, ["deploy", "--help"])
    assert result.exit_code == 0
    assert "Create Assisted cluster, Air sim, install OpenShift, download kubeconfig." in result.stdout
