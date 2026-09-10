# Lab spec reference

The `--spec` option accepts YAML (`.yaml` or `.yml`), JSON (`.json`), and
TOML (`.toml`). The top level must be a mapping with `simulation`, `cluster`,
and optionally `auth`.

Memory values use MiB. Disk values use GiB in the desired node configuration.

## Complete YAML example

```yaml
simulation:
  name: example-ocp-lab
  links:
    - endpoints:
        - { node: ocp-control-plane-0, network_pci: nic1, interface: p0 }
        - { node: ocp-control-plane-1, network_pci: nic1, interface: p0 }
cluster:
  name: ocp
  version: "4.19"
  architecture: x86_64
  base_dns_domain: dsx.air.local
  ntp_sources: []
  machine_networks: [192.168.200.0/24]
  cluster_networks:
    - { cidr: 10.128.0.0/14, host_prefix: 23 }
  service_networks: [172.30.0.0/16]
  api_vips: [192.168.200.10]
  ingress_vips: [192.168.200.11]
  control_plane:
    count: 3
    cpu: 16
    memory_mb: 65536
    disk_gb: 100
    hardware:
      boot_order: [hd, cdrom]
      cpu_mode: host-passthrough
      nic_model: virtio
      uefi: false
      secureboot: false
      emulation_type: HOST
      network_pci:
        - { name: nic1, emulation_type: NIC_ETHERNET, model: connectx7 }
  workers:
    count: 1
    cpu: 8
    memory_mb: 32768
    disk_gb: 100
auth:
  air_api_key_file: "op://Private/DSX Air/api-key"
  ai_offlinetoken_file: "op://Private/Red Hat/offline-token"
  pull_secret_file: "op://Private/Red Hat/pull-secret"
  ssh_public_key_file: "op://Private/My SSH Key/public key"
  jump_host_password_file: "op://Private/DSX Air/jump-host-password"
```

This example enables `HOST` emulation and a ConnectX Ethernet device on the
control-plane pool. Remove `links`, `emulation_type`, and `network_pci` when
this hardware is not required.

**Note:** NIC emulation requires the DSX Air organization to have access to its respective `SimX` image such as `simx-nic-X.XX-XXXX_{ARCH}` 

## Simulation


| Field   | Type   | Required or default | Rules                                            |
| ------- | ------ | ------------------- | ------------------------------------------------ |
| `name`  | string | Required            | Exact Air simulation name.                       |
| `links` | list   | `[]`                | Each link contains exactly two unique endpoints. |


A link endpoint requires `node` and `interface`. `node` must match a
resolved node name, and `interface` must contain text. Optional `network_pci`
must name a PCI device in that node's pool. A link cannot connect an endpoint to
itself. The same node, PCI-device, and interface tuple can appear only once.

## Cluster


| Field              | Type              | Required or default                                  | Rules                                                                                                              |
| ------------------ | ----------------- | ---------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| `name`             | string            | Required                                             | Assisted cluster name and generated-node prefix.                                                                   |
| `version`          | string            | Required                                             | Requested OpenShift version. A minor request can match a stable patch of the same minor; a patch request is exact. |
| `architecture`     | enum              | `x86_64`                                             | Supported values: `x86_64` and `arm64`.                                                                            |
| `base_dns_domain`  | string            | `dsx.air.local`                                      | Combined with `name` for the cluster domain.                                                                       |
| `ntp_sources`      | list of strings   | `[]`                                                 | Passed to the cluster and InfraEnv.                                                                                |
| `machine_networks` | list of CIDRs     | `[192.168.200.0/24]`                                 | At least one canonical IPv4 network.                                                                               |
| `cluster_networks` | list of objects   | `[{cidr: 10.128.0.0/14, host_prefix: 23}]`           | Canonical IPv4 CIDR; host prefix must exceed its prefix and be at most 32.                                         |
| `service_networks` | list of CIDRs     | `[172.30.0.0/16]`                                    | At least one canonical IPv4 network.                                                                               |
| `api_vips`         | list of addresses | `[192.168.200.10]`                                   | IPv4 addresses inside a machine network and distinct from Ingress VIPs.                                            |
| `ingress_vips`     | list of addresses | `[192.168.200.11]`                                   | IPv4 addresses inside a machine network and distinct from API VIPs.                                                |
| `control_plane`    | node pool         | Required                                             | `count` must be 1 or 3.                                                                                            |
| `workers`          | node pool         | `{count: 0, cpu: 8, memory_mb: 32768, disk_gb: 100}` | Workers require three control-plane nodes.                                                                         |


The deployment uses managed networking. It sends configured VIPs for three
control-plane nodes. For a single-node cluster, it derives both addresses from
the discovered control-plane host.

## Node pools


| Field       | Type         | Required or default | Rules                                                                                          |
| ----------- | ------------ | ------------------- | ---------------------------------------------------------------------------------------------- |
| `count`     | integer      | Required            | Zero or greater at model level; topology rules constrain control-plane count.                  |
| `names`     | list or null | Generated           | If set, length equals `count`. Names are unique lowercase DNS labels of at most 63 characters. |
| `cpu`       | integer      | `16`                | Greater than zero. Worker default: `8`.                                                        |
| `memory_mb` | integer      | `65536`             | Greater than zero. Worker default: `32768`.                                                    |
| `disk_gb`   | integer      | `100`               | Greater than zero.                                                                             |
| `hardware`  | object       | Default hardware    | Applied to every node in the pool.                                                             |


Generated names are `<cluster>-control-plane-<index>` and
`<cluster>-worker-<index>`, starting at zero. Names must be unique across both
pools.

## Node hardware


| Field            | Type         | Required or default | Rules                                                                                                      |
| ---------------- | ------------ | ------------------- | ---------------------------------------------------------------------------------------------------------- |
| `boot_order`     | list         | `[hd, cdrom]`       | This exact order is required.                                                                              |
| `cpu_mode`       | enum         | `host-passthrough`  | `host-passthrough`, `host-model`, or `custom`; omitted from the Air manifest with explicit node emulation. |
| `nic_model`      | enum         | `virtio`            | `virtio` or `e1000`.                                                                                       |
| `uefi`           | boolean      | `false`             | Enables UEFI.                                                                                              |
| `secureboot`     | boolean      | `false`             | Requires `uefi: true`.                                                                                     |
| `emulation_type` | enum or null | `null`              | Supported explicit value: `HOST`.                                                                          |
| `network_pci`    | list         | `[]`                | Requires `emulation_type: HOST`; names are unique within the pool.                                         |


Each PCI entry requires nonempty `name` and `model` strings.
`emulation_type` is `NIC_ETHERNET` or `NIC_INFINIBAND`. The model string is
sent to Air, for example `connectx7`.

## Authentication

All fields are optional while parsing, but deployment preflight requires all
five:


| Field                     | Credential                                        |
| ------------------------- | ------------------------------------------------- |
| `air_api_key_file`        | NVIDIA Air API key                                |
| `ai_offlinetoken_file`    | Assisted Installer offline token                  |
| `pull_secret_file`        | OpenShift pull-secret JSON                        |
| `ssh_public_key_file`     | Public key installed in the discovery environment |
| `jump_host_password_file` | Replacement password for the Air management node  |


A value can be a local path or an `op://` item-field reference. Local paths
expand `~` and `${ENV_VAR}`. Missing, unreadable, or empty values fail before
service clients are constructed.

For 1Password, the application runs `op read --no-newline <reference>` with a
120-second timeout. It preserves internal whitespace, removes trailing CR and LF
characters, and rejects whitespace-only output. Failure does not fall back to a
file.

Destroy reads only the Air API key and Assisted token. Tunnel and console read
only the Air API key. Deploy requires all five credentials.

## Command-line overrides


| Deploy option                 | Replaced value                |
| ----------------------------- | ----------------------------- |
| `--sim NAME`                  | `simulation.name`             |
| `--cluster NAME`              | `cluster.name`                |
| `--control-plane COUNT`       | `cluster.control_plane.count` |
| `--workers COUNT`             | `cluster.workers.count`       |
| `--ocp-version VERSION`       | `cluster.version`             |
| `--discovery-timeout MINUTES` | Computed discovery timeout    |


The merged spec is validated again. Destroy supports `--sim` and `--cluster`
only. Tunnel and console accept no spec overrides.

## Parser behavior

The extension selects the parser. Unsupported extensions fail. The decoded top
level must be a mapping. Current models use Pydantic's default behavior for
extra keys, so unknown keys are ignored. Check spelling carefully because an
ignored key does not change deployment intent.