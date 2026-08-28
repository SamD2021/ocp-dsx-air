"""Render an Air simulation manifest from a LabSpec."""

import json
from pathlib import Path

from ocp_dsx_air.models.spec import LabSpec, NodePool

DEFAULT_OS_IMAGE = "blank-100g"


def node_names(spec: LabSpec) -> list[str]:
    names = [f"ocp-cp-{i}" for i in range(spec.cluster.control_plane.count)]
    names.extend(f"ocp-worker-{i}" for i in range(spec.cluster.workers.count))
    return names


def _node_body(pool: NodePool, *, cdrom: str) -> dict:
    return {
        "cpu": pool.cpu,
        "memory": pool.memory_mb,
        "storage": pool.disk_gb,
        "nic_model": "virtio",
        "cpu_mode": "host-passthrough",
        "cpu_options": [],
        "secureboot": False,
        "os": DEFAULT_OS_IMAGE,
        "storage_pci": None,
        "pxehost": False,
        "cdrom": cdrom,
        "boot": ["hd", "cdrom"],
        "features": {"uefi": False},
    }


def render_manifest(spec: LabSpec, *, cdrom: str) -> dict:
    nodes: dict[str, dict] = {}
    for i in range(spec.cluster.control_plane.count):
        nodes[f"ocp-cp-{i}"] = _node_body(spec.cluster.control_plane, cdrom=cdrom)
    for i in range(spec.cluster.workers.count):
        nodes[f"ocp-worker-{i}"] = _node_body(spec.cluster.workers, cdrom=cdrom)
    return {
        "format": "JSON",
        "ztp": None,
        "content": {"nodes": nodes, "links": []},
        "name": spec.simulation.name,
    }


def write_manifest(spec: LabSpec, dest: Path, *, cdrom: str) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(render_manifest(spec, cdrom=cdrom), indent=4) + "\n")
    return dest
