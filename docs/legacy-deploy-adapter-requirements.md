# Legacy deploy adapter requirements

This is a factual migration checklist for the greenfield `deploy` path in
`dsx-air-ocp`. It records externally visible behavior and third-party API
usage; it does not prescribe workflow policy.

## End-to-end behavior

| Stage | Required behavior | Legacy source | Migration note |
| --- | --- | --- | --- |
| Preflight | Validate auth files and probe the Assisted API before remote mutation. Require `qemu-img` only when a blank image must be built. | `src/dsx_air/cli/commands/deploy.py` | Do not preserve the global environment mutation or unconditional tool check. |
| Assisted cluster | Find by exact name. Create with OCP version, x86_64 architecture, pull secret, SSH key, base DNS domain, NTP source, and SNO/HA settings. HA also sets control-plane count, managed networking, and API/Ingress VIPs. | `scripts/00_create_discovery_iso.py` | Existing resources must be inspected for compatibility rather than merely reused by name. |
| Infraenv and ISO | Find/create `<cluster>_infra-env`, bind it to the cluster, request a minimal ISO, and download it to a deterministic cache path. | `scripts/00_create_discovery_iso.py` | The legacy `--force` path deletes remote objects inside `ailib`; replacement policy belongs in core. |
| Air discovery image | Find by exact name, upload the ISO when absent, and wait for `upload_status == COMPLETE`. | `scripts/upload_discovery_iso.py` | Avoid overwriting a completed image that another simulation may reference. |
| Air blank image | Create a sparse 100 GiB qcow2 with `qemu-img`, upload it as a VM image, and wait for completion. | `scripts/upload_blank_disk.py` | Skip local image creation when a compatible completed Air image already exists. |
| Simulation | Import the generated manifest with `attempt_start=True`, wait for `ACTIVE`, and enumerate topology nodes separately from Air-managed OOB nodes. | `scripts/01_create_simulation.py`, `scripts/air_common.py` | Reuse requires more than the legacy node-count check: compare node names and material configuration. |
| Jump host | Reuse or create an SSH service on `oob-mgmt-server:eth0`, clear the forced first-login password change, verify key-based non-interactive SSH, and merge cluster DNS into `/etc/hosts`. | `scripts/air_common.py` | Passwords and command output must never enter events or exceptions. |
| Discovery | Poll cluster-scoped hosts, extract OOB `192.168.200.0/24` addresses, match exact requested/inventory hostnames to topology names, and pin roles by host UUID. | `scripts/06_wait_for_host_ipv4.py`, `scripts/assisted_common.py` | Never map UUID-only hosts by arrival order. NTP-related `insufficient` remains a warning. |
| Install | Wait for all required hosts to become `known`/`ready`, configure machine networking and HA VIPs, start a `ready` cluster, resume an `installing` cluster, and poll to `installed`. | `scripts/07_install_cluster.py` | The supported blank-disk path keeps `['hd', 'cdrom']`; legacy boot toggling is excluded. |
| Credentials | Refresh the Assisted token, then download kubeconfig and kubeadmin password to the lab cache. | `scripts/07_install_cluster.py` | The library returns paths rather than printing the next command. |

## SDK calls the adapters must isolate

### Assisted Installer (`ailib.AssistedClient`)

- Construction uses `AssistedClient(url="https://api.openshift.com", offlinetoken=..., quiet=...)`.
- Required calls are `list_clusters`, `create_cluster`, `delete_cluster`,
  `list_infra_envs`, `create_infra_env`, `delete_infra_env`, `download_iso`,
  `get_cluster_id`, `list_hosts`, `update_host`, `update_cluster`,
  `start_cluster`, `download_kubeconfig`, and `download_kubeadminpassword`.
- Full cluster observation currently uses
  `client.v2_get_cluster(cluster_id=...).to_dict()`.
- `ailib` accepts either pull-secret JSON or a path and normalizes a path
  internally. The new adapter should receive resolved JSON so file handling
  remains outside the SDK boundary.
- Access tokens can expire during long polls. A 401 requires one refresh and
  retry; raw mutation of client internals should remain adapter-private.

### NVIDIA Air (`nv-air-sdk`)

- Construct with `AirApi.with_api_key(api_key=...)`.
- Import with
  `api.simulations.import_from_simulation_manifest(simulation_manifest=..., attempt_start=True)`.
- Image operations use exact-name search, `api.images.create`, `upload`,
  `clear_upload`, `refresh`, and completion polling.
- Simulation operations use `refresh`, `start`, `shutdown`, `delete`, node
  listing, and checkpoint listing/deletion.
- `Simulation.create_service` supports the v3 `node_name` and
  `interface_name` arguments. The service port field is `node_port`; the SDK
  also maps the older `dest_port` name for compatibility.
- Node recovery/configuration uses `rebuild`, `update(cdrom=..., advanced=...)`,
  and `refresh`. Updating boot/CD-ROM settings requires an inactive simulation
  and completed/deleted checkpoints.

## Snapshot data required by core decisions

Adapters need only expose the fields below; raw SDK objects and dictionaries
should not cross the port boundary.

- Assisted cluster: ID, name, status/status-info, OCP version, HA mode,
  control-plane count, user-managed networking flag, machine networks, API and
  Ingress VIPs, and whether installation started/completed.
- Infraenv: ID, name, bound cluster ID, OCP version, image type, and ISO
  availability.
- Assisted host: ID, requested hostname, inventory hostname, status,
  status-info, role, OOB IPv4 addresses, and current install stage/progress.
- Air image: ID, exact name, provider/type, logical purpose, and upload status.
- Air simulation: ID, name, lifecycle state, and checkpoint states.
- Air node: ID, name, lifecycle state, CPU, memory, storage, OS image, CD-ROM
  image, boot order, and CPU mode.
- Jump-host service: ID, worker FQDN, worker port, node port, and service type.

## Behavior to leave behind

- Importing numbered scripts by modifying `sys.path` or launching them through
  `subprocess`.
- Passing desired state through process-wide environment variables.
- Raising `SystemExit` or printing from library code.
- Timestamp-based discovery image names, which create a new image on every
  rerun without identifying the infraenv that produced it.
- Treating a matching resource name or node count as proof of compatibility.
- Catching every exception and silently converting inspection failures into
  “no issue.” Unknown remote state must be visible to the workflow.
- The legacy `03_boot_to_disk.py` CD-ROM detach/HD-only flow.

## Known migration traps

- The old CLI documents `--discovery-timeout` in minutes, the standalone poll
  script accepts seconds, and the current new command stores it directly as
  seconds. The new CLI must normalize minutes exactly once.
- Some legacy remediation text says discovery should use CD-ROM-first boot;
  the validated blank-disk design is permanently `['hd', 'cdrom']`.
- Existing cluster status `installed` is not sufficient for success: the Air
  simulation must still be present and compatible before credentials are
  returned.
- A completed shared image must not be cleared/reuploaded during ordinary
  reconciliation.
- Assisted role updates are valid only in `discovering`, `known`,
  `disconnected`, `insufficient`, or `pending-for-input`; failures while
  pinning one host must not stop discovery of the others.
