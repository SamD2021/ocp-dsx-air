"""Lab spec: simulation + cluster + auth file pointers."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Self

import yaml
from pydantic import BaseModel, Field

from ocp_dsx_air.core.common import cache_dir

_ENV_VAR = re.compile(r"\$\{([^}]+)\}")


class NodePool(BaseModel):
    count: int
    cpu: int = 16
    memory_mb: int = 65536
    disk_gb: int = 100


class ClusterSpec(BaseModel):
    name: str
    version: str
    control_plane: NodePool
    workers: NodePool = Field(default_factory=lambda: NodePool(count=0, cpu=8, memory_mb=32768, disk_gb=100))


class AuthSpec(BaseModel):
    air_api_key_file: str | None = None
    ai_offlinetoken_file: str | None = None
    pull_secret_file: str | None = None
    ssh_public_key_file: str | None = None


class SimulationSpec(BaseModel):
    name: str


class LabSpec(BaseModel):
    simulation: SimulationSpec
    cluster: ClusterSpec
    auth: AuthSpec = Field(default_factory=AuthSpec)

    def merge(
        self,
        *,
        sim: str | None = None,
        cluster: str | None = None,
        control_plane: int | None = None,
        workers: int | None = None,
        ocp_version: str | None = None,
    ) -> Self:
        data = self.model_dump()
        if sim is not None:
            data["simulation"]["name"] = sim
        if cluster is not None:
            data["cluster"]["name"] = cluster
        if control_plane is not None:
            data["cluster"]["control_plane"]["count"] = control_plane
        if workers is not None:
            data["cluster"]["workers"]["count"] = workers
        if ocp_version is not None:
            data["cluster"]["version"] = ocp_version
        return type(self).model_validate(data)

    @property
    def expected_hosts(self) -> int:
        return self.cluster.control_plane.count + self.cluster.workers.count

    @property
    def profile(self) -> str:
        if self.cluster.control_plane.count > 1 or self.cluster.workers.count:
            return "multinode"
        return "sno"


def expand_path(raw: str) -> Path:
    """Expand ~ and ${ENV} in a path string (not secret values)."""

    def _sub(match: re.Match[str]) -> str:
        name = match.group(1)
        value = os.environ.get(name)
        if not value:
            raise SystemExit(f"Environment variable {name} is unset in path {raw!r}.")
        return value

    expanded = _ENV_VAR.sub(_sub, raw)
    return Path(expanded).expanduser()


def load_spec(path: Path) -> LabSpec:
    text = path.read_text()
    suffix = path.suffix.lower()
    data: Any
    if suffix in {".yaml", ".yml"}:
        data = yaml.safe_load(text)
    elif suffix == ".json":
        data = json.loads(text)
    elif suffix == ".toml":
        import tomllib

        data = tomllib.loads(text)
    else:
        raise SystemExit(f"Unsupported spec format: {path.suffix} (use yaml, toml, or json)")
    if not isinstance(data, dict):
        raise SystemExit(f"Spec {path} must be a mapping.")
    return LabSpec.model_validate(data)


def activate_spec(spec_path: Path | None) -> LabSpec | None:
    """Load a lab spec into env (CLUSTER_NAME, SIMULATION_NAME, auth files)."""
    if spec_path is None:
        return None

    spec = load_spec(spec_path)
    preflight_auth(spec)
    topo = cache_dir() / spec.simulation.name / "topology.json"
    apply_to_environ(spec, topology_path=topo if topo.is_file() else None)
    return spec


def apply_to_environ(spec: LabSpec, *, topology_path: Path | None = None) -> None:
    """Export spec into env vars numbered scripts already read."""
    os.environ["CLUSTER_NAME"] = spec.cluster.name
    os.environ["SIMULATION_NAME"] = spec.simulation.name
    os.environ["OCP_VERSION"] = spec.cluster.version
    os.environ["CLUSTER_PROFILE"] = spec.profile
    os.environ["CONTROL_PLANE_COUNT"] = str(spec.cluster.control_plane.count)
    os.environ["EXPECTED_HOSTS"] = str(spec.expected_hosts)
    if topology_path is not None:
        os.environ["TOPOLOGY_PATH"] = str(topology_path)
    mapping = (
        ("air_api_key_file", "AIR_API_KEY_FILE"),
        ("ai_offlinetoken_file", "AI_OFFLINETOKEN_FILE"),
        ("pull_secret_file", "PULL_SECRET_PATH"),
        ("ssh_public_key_file", "SSH_PUBLIC_KEY_PATH"),
    )
    for field, env_name in mapping:
        raw = getattr(spec.auth, field)
        if raw:
            os.environ[env_name] = str(expand_path(raw))


def preflight_auth(spec: LabSpec) -> None:
    """Fail immediately if spec auth files are missing."""
    checks = (
        ("auth.air_api_key_file", spec.auth.air_api_key_file, "Air API key"),
        ("auth.ai_offlinetoken_file", spec.auth.ai_offlinetoken_file, "Assisted Installer offline token"),
        ("auth.pull_secret_file", spec.auth.pull_secret_file, "pull secret"),
        ("auth.ssh_public_key_file", spec.auth.ssh_public_key_file, "SSH public key"),
    )
    for key, raw, what in checks:
        if not raw:
            continue
        path = expand_path(raw)
        if not path.is_file():
            raise SystemExit(f"{what} file not found ({key}): {path}")
        if not path.read_text().strip() and key != "auth.pull_secret_file":
            raise SystemExit(f"{what} file is empty ({key}): {path}")
        if key == "auth.pull_secret_file" and path.stat().st_size == 0:
            raise SystemExit(f"{what} file is empty ({key}): {path}")
