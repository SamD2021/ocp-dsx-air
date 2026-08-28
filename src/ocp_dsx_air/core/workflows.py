from ocp_dsx_air.models.runtime import DeployContext
from ocp_dsx_air.core import simulation, iso, images, cluster

# (You will create those ^ modules as you migrate the vertical slices)


def deploy_lab(ctx: DeployContext, replace: bool = False) -> None:
    """The master orchestrator for a greenfield lab deployment."""

    print(f"Starting deployment for simulation {ctx.sim_name!r}...")

    # 1. Existing Simulation Check
    # (Uses your new simulation.py functions, passing explicit strings)
    # existing = simulation.check_existing(ctx.sim_name)

    # 2. ISO & Images
    # (Delegates to iso.py and images.py, passing explicit paths/creds)
    # iso_path = ctx.cache_dir / "discovery.iso"
    # iso.create_discovery_iso(iso_path, ctx.network, ctx.creds)
    # images.upload_discovery_iso(iso_path, ctx.cdrom_image_name, ctx.creds.air_api_key)
    # images.upload_blank_disk(ctx.cache_dir / "blank-100g.qcow2")

    # 3. Create Simulation
    # simulation.create_from_manifest(...)

    # 4. Jump Host & Network Prep
    # jumphost.ensure_ready(...)

    # 5. Cluster Installation Wait
    # cluster.wait_for_discovery(ctx.expected_hosts, ctx.discovery_timeout_s)

    print(f"Deployment complete. Kubeconfig saved to {ctx.cache_dir}")
