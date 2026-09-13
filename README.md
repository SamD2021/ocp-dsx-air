# OpenShift on NVIDIA DSX Air

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)

`ocp-dsx-air` creates an OpenShift cluster in an NVIDIA DSX Air simulation. It
uses Red Hat Assisted Installer for cluster installation and NVIDIA Air for the
virtual machines and network topology.

The `ocp-air` Command Line Interface (CLI) can deploy a lab, control its Air
simulation lifecycle, open the private OpenShift API or web console, and destroy
the remote resources.

> **Status:** This project is under active development. Review the generated
> topology and resource requirements before you use it in a shared Air
> organization.

## What the deployment creates

A deployment manages:

- one Assisted Installer cluster and InfraEnv;
- one InfraEnv discovery ISO and its Air image;
- one reusable blank-disk Air image;
- one Air simulation with the requested control-plane and worker nodes;
- one Air-managed out-of-band management node and SSH service;
- downloaded `kubeconfig` and `kubeadmin-password` files in the user cache.

A normal rerun resumes compatible resources. The command refuses material
configuration drift or an unmanaged same-name resource.

## Requirements

You need:

- Linux with Python 3.14 or later;
- [uv](https://docs.astral.sh/uv/) to install and run the project;
- `qemu-img` when a compatible blank-disk Air image is unavailable;
- an SSH client and agent with the private key for the configured public key;
- an NVIDIA Air API key;
- an [Assisted Installer offline token](https://console.redhat.com/openshift/token) and [OpenShift pull secret](https://console.redhat.com/openshift/install/pull-secret).

Install the dependencies from a repository checkout:

```sh
uv sync
```



## Configure a lab

Copy the example and edit it for your environment:

```sh
cp examples/ha-3cp-2w.yaml spec.local.yaml
```

The spec accepts YAML, JSON, or TOML. This YAML example creates three
control-plane nodes and one worker:

```yaml
simulation:
  name: example-ocp-lab
cluster:
  name: ocp
  version: "4.19"
  architecture: x86_64
  base_dns_domain: dsx.air.local
  machine_networks: [192.168.200.0/24]
  cluster_networks: [{ cidr: 10.128.0.0/14, host_prefix: 23 }]
  service_networks: [172.30.0.0/16]
  api_vips: [192.168.200.10]
  ingress_vips: [192.168.200.11]
  control_plane: { count: 3, cpu: 16, memory_mb: 65536, disk_gb: 100 }
  workers: { count: 1, cpu: 8, memory_mb: 32768, disk_gb: 100 }
auth:
  air_api_key_file: ~/.config/ocp-dsx-air/air-api-key
  ai_offlinetoken_file: ~/.config/ocp-dsx-air/assisted-offline-token
  pull_secret_file: ~/.config/ocp-dsx-air/pull-secret.json
  ssh_public_key_file: ~/.ssh/id_ed25519.pub
  jump_host_password_file: ~/.config/ocp-dsx-air/jump-host-password
```

Each `auth.*_file` field also accepts a 1Password item-field reference:

```yaml
auth:
  air_api_key_file: "op://Private/DSX Air/api-key"
  ai_offlinetoken_file: "op://Private/Red Hat/offline-token"
  pull_secret_file: "op://Private/Red Hat/pull-secret"
  ssh_public_key_file: "op://Private/My SSH Key/public key"
  jump_host_password_file: "op://Private/DSX Air/jump-host-password"
```

For these references, install and authenticate the
`op` [CLI](https://developer.1password.com/docs/cli/get-started/). The command
reads item fields without temporary secret files. 1Password Document retrieval
is not supported.

See the [spec reference](docs/spec-reference.md) for every field, default, and
validation rule.

## Deploy the lab

```sh
uv run ocp-air deploy --spec spec.yaml
```

Deployment can take a long time and can run independently from the CLI once kicked off. If the process is interrupted, run the same command again. Compatible resources are reused and incomplete stages continue.

On completion, the command prints paths to `kubeconfig` and
`kubeadmin-password`, followed by commands for API and console access.

Do not use `--replace` as a general retry option. It deletes the managed remote
lab before it creates a replacement. See [Lab operations](docs/operations.md)
for recovery guidance.

## Connect to the cluster

The API and application VIPs are private to the Air network. Open a foreground
API tunnel in a separate terminal:

```sh
uv run ocp-air tunnel --spec spec.yaml
```

The command writes `kubeconfig.tunnel` and prints an `oc` command that uses
it. Press Ctrl+C to close the tunnel.

If the simulation is inactive, start it first or combine the operations:

```sh
uv run ocp-air start --spec spec.yaml
uv run ocp-air tunnel --spec spec.yaml --start
```

Open the web console:

```sh
uv run ocp-air console --spec spec.yaml
```

The console command supports native Chromium, Chrome, Edge, and Brave browsers,
and the Flatpak `org.chromium.Chromium` application. It keeps one browser
profile per simulation. The cluster-generated ingress certificate requires a
one-time warning acceptance in that profile.

## Control the simulation

Inspect the simulation without changing it:

```sh
uv run ocp-air status --spec spec.yaml
```

Normal shutdown preserves a checkpoint and returns after NVIDIA Air accepts the
request. Use `--wait` only when the calling process must wait for `INACTIVE`:

```sh
uv run ocp-air stop --spec spec.yaml
uv run ocp-air stop --spec spec.yaml --wait
```

Restart waits through checkpoint creation and startup:

```sh
uv run ocp-air restart --spec spec.yaml
```

See [Lab operations](docs/operations.md) for state, timeout, and recovery
behavior.

## Destroy the lab

```sh
uv run ocp-air destroy --spec spec.yaml
```

The command asks for confirmation. Use `--yes` for a noninteractive run. It
keeps cached media, downloaded credentials, and reusable blank-disk images.

## Documentation

- [Architecture](docs/architecture.md): component boundaries and data flow
- [Lab lifecycle](docs/lifecycle.md): reconciliation, replacement, and teardown
- [Spec reference](docs/spec-reference.md): complete configuration contract
- [Lab operations](docs/operations.md): deployment, access, and recovery
- [Development](docs/development.md): project layout, checks, and boundary tests



## License

This project is licensed under the [Apache License 2.0](LICENSE). The
license covers this project's source code and documentation. It does not grant
access to NVIDIA Air or Red Hat services, or rights to Red Hat or NVIDIA
trademarks.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the contribution terms.
