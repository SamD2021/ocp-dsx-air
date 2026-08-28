from pathlib import Path

import typer

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
    spec: Path,
    sim: str | None = typer.Option(None, "--sim", help="Override simulation.name."),
    cluster_name: str | None = typer.Option(None, "--cluster", help="Override cluster.name."),
    control_plane: int | None = typer.Option(None, "--control-plane", help="Override control_plane.count."),
    workers: int | None = typer.Option(None, "--workers", help="Override workers.count."),
    ocp_version: str | None = typer.Option(None, "--ocp-version", help="Override cluster.version."),
    replace: bool = typer.Option(False, "--replace", help="Destroy spec sim+cluster, then deploy."),
    discovery_timeout: int | None = typer.Option(
        None,
        "--discovery-timeout",
        help="Minutes to wait for host discovery (default: max(20, 8 per host)).",
    ),
) -> None:
    """Create Assisted cluster, Air sim, install OpenShift, download kubeconfig."""
    spec = typer.Option(..., "--spec", exists=True, readable=True, help="Lab YAML/TOML/JSON spec.")

    pass
    # deploy.run_deploy(
    #     spec_path=spec,
    #     sim=sim,
    #     cluster=cluster_name,
    #     control_plane=control_plane,
    #     workers=workers,
    #     ocp_version=ocp_version,
    #     replace=replace,
    #     discovery_timeout=discovery_timeout,
    # )


def main():
    app()


if __name__ == "__main__":
    main()
