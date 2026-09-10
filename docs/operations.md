# Lab operations

Run these commands from a project checkout after `uv sync`.

## Deploy or resume

1. Confirm that the Air organization has capacity for the simulation.
2. Confirm that the SSH agent has the private key for
   `auth.ssh_public_key_file`.
3. Start deployment:

   ```sh
   uv run ocp-air deploy --spec spec.yaml
   ```

4. Leave the command running during reconciliation and installation.
5. After an interruption, run the same command again.

You can override selected values without editing the file:

```sh
uv run ocp-air deploy --spec spec.yaml --sim temporary-lab --workers 1
```

Use `--discovery-timeout MINUTES` to replace the default host-discovery
timeout.

## Respond to drift

A drift error means that a same-name remote resource differs from the desired
configuration.

1. Compare the listed fields with the spec and remote lab.
2. Restore the original spec if the change was accidental.
3. Run the normal deploy command again.
4. If you intend to recreate the complete remote lab, run:

   ```sh
   uv run ocp-air deploy --spec spec.yaml --replace
   ```

Replacement deletes the simulation, discovery image, InfraEnv, and cluster. It
preserves the reusable blank-disk image and local cache. The command refuses to
replace a valid Air simulation that is not managed by this project.

## Diagnose host discovery

NTP synchronization and majority-connectivity validations can be temporary in
Air. The workflow reports them and continues until all hosts are ready or the
discovery timeout expires.

An `Unexpected discovered host` error identifies a stable hostname outside the
desired topology. Compare Assisted host inventory with the generated or
explicit node names. The workflow does not assign unmatched hosts by order.

If a host remains absent, inspect its Air console. Confirm that the discovery
ISO is attached and the node can reach the machine network. Rerun deployment
after it registers.

## Resolve insufficient Air capacity

The start guard reports required and available CPU, memory, or storage. It also
checks per-node storage. Air-managed out-of-band nodes contribute to the total,
so it can exceed the sum of OpenShift nodes in the spec.

Reduce the topology or wait for other simulations to release resources, then
rerun deployment. The check cannot reserve resources, so Air can still reject a
concurrent start.

## Open an API tunnel

The simulation must be active, its SSH service must be ready, and the downloaded
kubeconfig must exist.

```sh
uv run ocp-air tunnel --spec spec.yaml
```

The command forwards `127.0.0.1:6443` to the API VIP and writes
`kubeconfig.tunnel`. It preserves TLS verification. Keep the process open and
run the `oc` command it prints. Press Ctrl+C to stop it.

Choose another local port when needed:

```sh
uv run ocp-air tunnel --spec spec.yaml --local-port 16443
```

## Open the web console

```sh
uv run ocp-air console --spec spec.yaml
```

The Linux console command selects a browser in this order:

1. the native executable supplied with `--browser`;
2. a supported system-default browser;
3. Chromium, Chrome, Edge, or Brave on `PATH`;
4. the Flatpak application `org.chromium.Chromium`.

It creates a SOCKS5 proxy on `127.0.0.1:1080`, starts the browser with
cluster-specific DNS mappings, and uses a persistent profile in the simulation
cache. It prints the console URL, username, and password-file path without
reading the password.

Accept the cluster-generated ingress certificate warning once in the isolated
profile after you verify the hostname. TLS checking remains enabled.

```sh
uv run ocp-air console --spec spec.yaml --socks-port 11080
uv run ocp-air console --spec spec.yaml --print-only
```

`--print-only` prints safely quoted SSH and browser commands without starting
them. Press Ctrl+C or close the browser to stop a live session.

## Destroy a lab

```sh
uv run ocp-air destroy --spec spec.yaml
```

Review the resolved names before confirmation. Noninteractive use requires:

```sh
uv run ocp-air destroy --spec spec.yaml --yes
```

Use `--sim` and `--cluster` if deployment used the same overrides. The
command validates discoverable ownership and removes the complete spec-derived
lab. Repeated destruction succeeds when resources are absent.

Destroy keeps local media, disk files, browser profiles, and downloaded
credentials. Remove them separately according to local retention requirements.
