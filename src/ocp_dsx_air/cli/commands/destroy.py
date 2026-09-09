"""Production composition root for the destroy command."""

from pathlib import Path

from ocp_dsx_air.adapters.air import NvidiaAirAdapter
from ocp_dsx_air.adapters.assisted import AssistedInstallerAdapter
from ocp_dsx_air.cli.commands.deploy import _read_secret
from ocp_dsx_air.cli.reporting import CliDeploymentReporter
from ocp_dsx_air.core.common import cache_dir
from ocp_dsx_air.core.runtime import SystemClock
from ocp_dsx_air.core.workflows import destroy_lab
from ocp_dsx_air.models.resolution import resolve_deploy_intent
from ocp_dsx_air.models.spec import LabSpec, load_spec


def load_destroy_spec(
    spec_path: Path,
    *,
    sim: str | None = None,
    cluster: str | None = None,
) -> LabSpec:
    """Load a destroy target and apply the supported name overrides."""
    return load_spec(spec_path).merge(sim=sim, cluster=cluster)


def run_destroy(spec: LabSpec) -> None:
    """Construct the required service adapters and destroy one resolved lab."""
    air_api_key = _read_secret(
        spec.auth.air_api_key_file,
        field="auth.air_api_key_file",
    )
    assisted_token = _read_secret(
        spec.auth.ai_offlinetoken_file,
        field="auth.ai_offlinetoken_file",
    )
    intent = resolve_deploy_intent(
        spec,
        cache_root=cache_dir() / spec.simulation.name,
    )
    destroy_lab(
        intent,
        assisted=AssistedInstallerAdapter(assisted_token),
        air=NvidiaAirAdapter(api_key=air_api_key),
        reporter=CliDeploymentReporter(),
        clock=SystemClock(),
    )
