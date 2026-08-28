import time

from air_sdk import AirApi
from air_sdk.endpoints.nodes import Node
from air_sdk.endpoints.simulations import Simulation

from ocp_dsx_air.core.exceptions import AirSimError

# Standard NVIDIA Air out-of-band infrastructure that should be ignored when mapping OCP nodes.
_OOB_INFRA_NODES = frozenset({"oob-mgmt-server", "oob-mgmt-switch-leaf-1"})


def get_simulation(api: AirApi, name: str) -> Simulation:
    sim_name = name
    sims = list(api.simulations.list(search=sim_name))
    matches = [s for s in sims if s.name == sim_name]
    if not matches:
        raise AirSimError(f"No simulation named {sim_name!r} found.")
    return matches[0]


def get_topology_nodes(sim: Simulation) -> list[Node]:
    """Return OCP nodes from the simulation (exclude Air-managed OOB infra)."""
    return [node for node in sim.nodes.list() if node.name not in _OOB_INFRA_NODES]


def get_node(sim: Simulation, name: str) -> Node:
    node_name = name
    for node in sim.nodes.list():
        if node.name == node_name:
            return node
    raise AirSimError(f"No node named {node_name!r} found in simulation {sim.name!r}.")


def wait_for_sim_state(sim: Simulation, *states: str, timeout: int = 180, interval: int = 4) -> None:
    deadline = time.monotonic() + timeout
    while True:
        sim.refresh()
        print(f"  simulation {sim.name!r} state: {sim.state!r}")
        if sim.state in states:
            return
        if time.monotonic() > deadline:
            raise AirSimError(
                f"Timed out after {timeout}s waiting for simulation state in {states} (last seen: {sim.state!r})."
            )
        time.sleep(interval)


def stop_simulation_and_clear_checkpoints(sim: Simulation) -> None:
    """Stop the simulation (if running) and delete any checkpoints so the
    node can be safely patched afterwards."""
    if sim.state != "INACTIVE":
        print(f"Stopping simulation {sim.name!r} ...")
        sim.shutdown()
        wait_for_sim_state(sim, "INACTIVE", timeout=240)
    else:
        print(f"Simulation {sim.name!r} is already INACTIVE.")

    checkpoints = list(sim.checkpoints.list())
    if not checkpoints:
        return

    print(f"Clearing {len(checkpoints)} checkpoint(s) before patching the node ...")
    deadline = time.monotonic() + 300
    for cp in checkpoints:
        while True:
            cp.refresh()
            state = getattr(cp, "state", None)
            if state in {"COMPLETE", "DELETED"}:
                break
            if time.monotonic() > deadline:
                raise AirSimError(
                    f"Timed out waiting for checkpoint {cp.id} to become COMPLETE (last state: {state!r})."
                )
            time.sleep(3)
        if getattr(cp, "state", None) == "DELETED":
            continue
        try:
            cp.delete()
            print(f"  deleted checkpoint {cp.id} ({cp.name})")
        except Exception as exc:
            raise AirSimError(
                f"Could not delete checkpoint {cp.id}: {exc}. "
                "Disk state may revert on next start — refusing to continue."
            ) from exc


def start_simulation(sim: Simulation) -> None:
    print(f"Starting simulation {sim.name!r} ...")
    sim.start()
    wait_for_sim_state(sim, "ACTIVE", timeout=180)


def boot_node_to_disk(sim: Simulation, node_name: str, *, force: bool = False) -> None:
    """Legacy: detach cdrom and set hd-only boot.

      The blank-disk topology pattern (README.md) keeps boot ``["hd", "cdrom"]``
    forever — blank hd falls through to the discovery ISO, and a bootable
      install wins on hd automatically. Stopping the sim to toggle boot/cdrom
      can revert disk state via checkpoints and leaves a non-bootable hd with
      no cdrom fallback ("No bootable device").

      This function is kept for manual recovery only; pass ``force=True`` to run.
    """
    if not force:
        print(
            "Skipping boot-to-disk: blank-disk topology uses permanent boot "
            '["hd", "cdrom"] — no boot/cdrom toggle needed. See README.md. '
            "If the node shows 'No bootable device', run "
            "09_recover_to_discovery.py instead."
        )
        return
    node = get_node(sim, node_name)
    print(f"Current state: node.cdrom={node.cdrom!r} advanced.boot={node.advanced.get('boot')!r}")

    stop_simulation_and_clear_checkpoints(sim)

    print("Setting boot order to hd-only ...")
    advanced = dict(node.advanced or {})
    advanced["boot"] = "hd"
    if not advanced.get("cpu_mode"):
        advanced["cpu_mode"] = "host-passthrough"
    node.update(advanced=advanced)
    node.refresh()
    print(f"  advanced now: {node.advanced}")

    print("Detaching cdrom ...")
    node.update(cdrom=None)
    node.refresh()
    print(f"  cdrom now: {node.cdrom}")

    start_simulation(sim)
    print("Node will boot from disk on its next reboot.")
