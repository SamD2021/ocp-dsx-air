import sys
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from ocp_dsx_air.cli.commands.console import run_console
from ocp_dsx_air.cli.commands.deploy import run_deploy
from ocp_dsx_air.cli.commands.destroy import load_destroy_spec, run_destroy
from ocp_dsx_air.cli.commands.simulation import (
    run_restart,
    run_start,
    run_status,
    run_stop,
)
from ocp_dsx_air.cli.commands.tunnel import run_tunnel
from ocp_dsx_air.core.exceptions import ConfigurationError, OcpAirError

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


def _stdin_is_interactive() -> bool:
    return sys.stdin.isatty()


def _confirm_destroy(*, simulation: str, cluster: str, assume_yes: bool) -> None:
    if assume_yes:
        return
    if not _stdin_is_interactive():
        raise ConfigurationError("Refusing noninteractive destruction without --yes")
    if not typer.confirm(
        f"Delete Air simulation {simulation!r} and Assisted cluster {cluster!r}?"
    ):
        raise typer.Abort()


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
    typer.echo(f"Connect: ocp-air tunnel --spec {spec}")
    typer.echo(f"Console: ocp-air console --spec {spec}")


@app.command("destroy")
def destroy_cmd(
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
    cluster_name: str | None = typer.Option(
        None,
        "--cluster",
        help="Override cluster.name.",
    ),
    yes: bool = typer.Option(False, "--yes", help="Skip the destruction prompt."),
) -> None:
    """Delete the complete remote lab described by a spec."""
    try:
        resolved = load_destroy_spec(spec, sim=sim, cluster=cluster_name)
        _confirm_destroy(
            simulation=resolved.simulation.name,
            cluster=resolved.cluster.name,
            assume_yes=yes,
        )
        run_destroy(resolved)
    except (OcpAirError, ValidationError, OSError, ValueError) as exc:
        typer.echo(f"Destroy failed: {exc}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo("Destroy complete.")


@app.command("status")
def status_cmd(
    spec: Annotated[
        Path,
        typer.Option(
            "--spec",
            exists=True,
            readable=True,
            help="Deployed lab YAML/TOML/JSON spec.",
        ),
    ],
) -> None:
    """Report NVIDIA Air simulation state without changing it."""
    try:
        run_status(spec, announce=typer.echo)
    except (OcpAirError, ValidationError, OSError, ValueError) as exc:
        typer.echo(f"Status failed: {exc}", err=True)
        raise typer.Exit(code=1) from None


@app.command("start")
def start_cmd(
    spec: Annotated[
        Path,
        typer.Option(
            "--spec",
            exists=True,
            readable=True,
            help="Deployed lab YAML/TOML/JSON spec.",
        ),
    ],
    timeout: int = typer.Option(
        30,
        "--timeout",
        min=1,
        help="Minutes to wait for the simulation to become active.",
    ),
) -> None:
    """Start or resume a managed simulation and wait until it is active."""
    try:
        run_start(spec, timeout_seconds=timeout * 60, announce=typer.echo)
    except (OcpAirError, ValidationError, OSError, ValueError) as exc:
        typer.echo(f"Start failed: {exc}", err=True)
        raise typer.Exit(code=1) from None


@app.command("stop")
def stop_cmd(
    spec: Annotated[
        Path,
        typer.Option(
            "--spec",
            exists=True,
            readable=True,
            help="Deployed lab YAML/TOML/JSON spec.",
        ),
    ],
    wait: bool = typer.Option(
        False,
        "--wait",
        help="Wait for checkpointing and shutdown to finish.",
    ),
    timeout: int | None = typer.Option(
        None,
        "--timeout",
        min=1,
        help="Minutes to wait; valid only with --wait (default: 30).",
    ),
) -> None:
    """Request a checkpoint-preserving simulation shutdown."""
    try:
        if timeout is not None and not wait:
            raise ConfigurationError("--timeout requires --wait")
        run_stop(
            spec,
            wait=wait,
            timeout_seconds=(timeout or 30) * 60,
            announce=typer.echo,
        )
    except (OcpAirError, ValidationError, OSError, ValueError) as exc:
        typer.echo(f"Stop failed: {exc}", err=True)
        raise typer.Exit(code=1) from None


@app.command("restart")
def restart_cmd(
    spec: Annotated[
        Path,
        typer.Option(
            "--spec",
            exists=True,
            readable=True,
            help="Deployed lab YAML/TOML/JSON spec.",
        ),
    ],
    timeout: int = typer.Option(
        60,
        "--timeout",
        min=1,
        help="Total minutes to wait for shutdown and startup.",
    ),
) -> None:
    """Checkpoint, stop, and start a managed simulation."""
    try:
        run_restart(spec, timeout_seconds=timeout * 60, announce=typer.echo)
    except (OcpAirError, ValidationError, OSError, ValueError) as exc:
        typer.echo(f"Restart failed: {exc}", err=True)
        raise typer.Exit(code=1) from None


@app.command("console")
def console_cmd(
    spec: Annotated[
        Path,
        typer.Option(
            "--spec", exists=True, readable=True, help="Deployed lab YAML/TOML/JSON spec."
        ),
    ],
    browser: Annotated[
        Path | None,
        typer.Option("--browser", help="Supported Chromium-family executable."),
    ] = None,
    socks_port: int = typer.Option(
        1080, "--socks-port", min=1, max=65535, help="Local SOCKS proxy port."
    ),
    print_only: bool = typer.Option(
        False, "--print-only", help="Print commands without launching them."
    ),
    start: bool = typer.Option(
        False,
        "--start",
        help="Start an inactive managed simulation before connecting.",
    ),
) -> None:
    """Open the private OpenShift Console through the Air jump host."""
    try:
        run_console(
            spec,
            browser_override=browser,
            socks_port=socks_port,
            print_only=print_only,
            start=start,
            announce=typer.echo,
        )
    except (OcpAirError, ValidationError, OSError, ValueError) as exc:
        typer.echo(f"Console failed: {exc}", err=True)
        raise typer.Exit(code=1) from None


@app.command("tunnel")
def tunnel_cmd(
    spec: Annotated[
        Path,
        typer.Option(
            "--spec", exists=True, readable=True, help="Deployed lab YAML/TOML/JSON spec."
        ),
    ],
    local_port: int = typer.Option(
        6443, "--local-port", min=1, max=65535, help="Local API port."
    ),
    start: bool = typer.Option(
        False,
        "--start",
        help="Start an inactive managed simulation before connecting.",
    ),
) -> None:
    """Open a foreground SSH tunnel to the private OpenShift API."""
    try:
        run_tunnel(
            spec,
            local_port=local_port,
            start=start,
            announce=typer.echo,
        )
    except (OcpAirError, ValidationError, OSError, ValueError) as exc:
        typer.echo(f"Tunnel failed: {exc}", err=True)
        raise typer.Exit(code=1) from None


def main():
    app()


if __name__ == "__main__":
    main()
