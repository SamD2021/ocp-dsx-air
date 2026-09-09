"""Production composition root for the deploy command."""

import subprocess
from pathlib import Path

from ocp_dsx_air.adapters.air import NvidiaAirAdapter
from ocp_dsx_air.adapters.assisted import AssistedInstallerAdapter
from ocp_dsx_air.cli.reporting import CliDeploymentReporter
from ocp_dsx_air.core.common import cache_dir
from ocp_dsx_air.core.contracts import DeploymentResult
from ocp_dsx_air.core.exceptions import ConfigurationError
from ocp_dsx_air.core.runtime import SystemClock
from ocp_dsx_air.core.workflows import deploy_lab
from ocp_dsx_air.models.resolution import resolve_deploy_intent
from ocp_dsx_air.models.runtime import ResolvedCredentials
from ocp_dsx_air.models.spec import expand_path, load_spec, preflight_auth


def _read_secret(path_str: str | None, *, field: str) -> str:
    if not path_str:
        raise ConfigurationError(f"Missing required credential ({field})")
    if not path_str.startswith("op://"):
        try:
            value = expand_path(path_str).read_text().rstrip("\r\n")
        except FileNotFoundError:
            raise ConfigurationError(f"Credential file not found ({field})") from None
        except OSError:
            raise ConfigurationError(f"Could not read credential file ({field})") from None
        if not value.strip():
            raise ConfigurationError(f"Credential file is empty ({field})")
        return value
    try:
        result = subprocess.run(
            ["op", "read", "--no-newline", path_str],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except FileNotFoundError:
        raise ConfigurationError(f"1Password CLI (op) not found ({field})") from None
    except subprocess.TimeoutExpired:
        raise ConfigurationError(f"1Password read timed out ({field})") from None
    except (OSError, subprocess.SubprocessError):
        raise ConfigurationError(f"Could not run 1Password CLI ({field})") from None
    if result.returncode != 0:
        raise ConfigurationError(f"1Password read failed ({field}); check CLI authentication and the field reference")
    value = result.stdout.rstrip("\r\n")
    if not value.strip():
        raise ConfigurationError(f"1Password returned an empty credential ({field})")
    return value


def run_deploy(
    spec_path: Path,
    *,
    sim: str | None = None,
    cluster: str | None = None,
    control_plane: int | None = None,
    workers: int | None = None,
    ocp_version: str | None = None,
    replace: bool = False,
    discovery_timeout_minutes: int | None = None,
) -> DeploymentResult:
    """Resolve configuration, construct adapters, and execute deployment."""
    if discovery_timeout_minutes is not None and discovery_timeout_minutes <= 0:
        raise ValueError("Discovery timeout must be positive")
    spec = load_spec(spec_path).merge(
        sim=sim,
        cluster=cluster,
        control_plane=control_plane,
        workers=workers,
        ocp_version=ocp_version,
    )
    preflight_auth(spec)
    credentials = ResolvedCredentials(
        air_api_key=_read_secret(spec.auth.air_api_key_file, field="auth.air_api_key_file"),
        ai_offline_token=_read_secret(spec.auth.ai_offlinetoken_file, field="auth.ai_offlinetoken_file"),
        pull_secret=_read_secret(spec.auth.pull_secret_file, field="auth.pull_secret_file"),
        ssh_public_key=_read_secret(spec.auth.ssh_public_key_file, field="auth.ssh_public_key_file"),
        jump_host_password=_read_secret(spec.auth.jump_host_password_file, field="auth.jump_host_password_file"),
    )
    intent = resolve_deploy_intent(
        spec,
        cache_root=cache_dir() / spec.simulation.name,
        discovery_timeout_seconds=(discovery_timeout_minutes * 60 if discovery_timeout_minutes is not None else None),
    )
    assisted = AssistedInstallerAdapter(credentials.ai_offline_token)
    air = NvidiaAirAdapter(api_key=credentials.air_api_key)
    return deploy_lab(
        intent,
        credentials=credentials,
        assisted=assisted,
        air=air,
        jump_host=air,
        reporter=CliDeploymentReporter(),
        clock=SystemClock(),
        replace=replace,
    )
