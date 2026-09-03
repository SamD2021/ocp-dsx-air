from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from ocp_dsx_air.cli.commands.deploy import run_deploy
from ocp_dsx_air.core.exceptions import OcpAirError

# 1. The Typer Initialization
app = typer.Typer(
    name="ocp-air",
    help="OpenShift on NVIDIA DSX Air: deploy, operate, and open Console.",
    no_args_is_help=True,
    add_completion=False,
)


@app.callback()
def global_setup():
    """Global setup for ocp-air."""
    pass


@app.command("deploy")
def deploy_cmd(
    spec: Annotated[
        Path,
        typer.Option(
            "--spec",
            exists=True,
            readable=True,
            help="Lab YAML/TOML/JSON spec.",
        ),
    ],
    sim: str | None = typer.Option(None, "--sim", help="Override simulation.name."),
    cluster_name: str | None = typer.Option(None, "--cluster", help="Override cluster.name."),
    control_plane: int | None = typer.Option(None, "--control-plane", help="Override control_plane.count."),
    workers: int | None = typer.Option(None, "--workers", help="Override workers.count."),
    ocp_version: str | None = typer.Option(None, "--ocp-version", help="Override cluster.version."),
    replace: bool = typer.Option(False, "--replace", help="Destroy spec sim+cluster, then deploy."),
    discovery_timeout: int | None = typer.Option(
        None,
        "--discovery-timeout",
        min=1,
        help="Minutes to wait for host discovery (default: max(20, 8 per host)).",
    ),
) -> None:
    """Create Assisted cluster, Air sim, install OpenShift, download kubeconfig."""
    try:
        result = run_deploy(
            spec,
            sim=sim,
            cluster=cluster_name,
            control_plane=control_plane,
            workers=workers,
            ocp_version=ocp_version,
            replace=replace,
            discovery_timeout_minutes=discovery_timeout,
        )
    except (OcpAirError, ValidationError, OSError, ValueError) as exc:
        typer.echo(f"Deployment failed: {exc}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"Kubeconfig: {result.credentials.kubeconfig}")
    typer.echo(f"Kubeadmin password: {result.credentials.kubeadmin_password}")


def main():
    app()


if __name__ == "__main__":
    main()
