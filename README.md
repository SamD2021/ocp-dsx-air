# OpenShift on NVIDIA DSX Air

## Credentials from files or 1Password

Each `auth.*_file` setting accepts a local file path or a 1Password field reference.
You can mix both sources. Existing file paths, including `~` and `${ENV_VAR}` path
expansion, continue to work.

```yaml
auth:
  air_api_key_file: "op://Private/DSX Air/api-key"
  ai_offlinetoken_file: "op://Private/Red Hat/offline-token"
  pull_secret_file: "op://Private/Red Hat/pull-secret"
  ssh_public_key_file: "op://Private/My SSH Key/public key"
  jump_host_password_file: "op://Private/DSX Air/jump-host-password"
```

Replace these illustrative references with references to your own item fields.
Store pull-secret JSON in a text field; 1Password Document retrieval is not supported.
For a local public key, for example, use `ssh_public_key_file: ~/.ssh/id_ed25519.pub`.

Install the [1Password CLI](https://developer.1password.com/docs/cli/get-started/)
and configure its account/session or desktop-app integration before deploying:

```sh
uv run ocp-air deploy --spec examples/ha-3cp-2w.yaml
```

For each reference, the application runs `op read --no-newline` using the existing
CLI configuration. Desktop approval may be requested. Each read has a 120-second
timeout. Values are resolved once before service clients are constructed, without
temporary credential files. A failed or empty read stops deployment; it does not
fall back to a local file. Error messages identify the affected auth setting
without printing captured CLI output. File-only configurations do not require `op`.

Reading an SSH item's public-key field only supplies the public key to deployment.
Configure system SSH to use the 1Password agent with the matching private key;
this application does not configure the agent or retrieve private keys.

## Connect to the private OpenShift API

The cluster API VIP is reachable from the Air network, while the managed jump
host SSH service is exposed externally. After deployment, open a foreground
tunnel from another terminal:

```sh
uv run ocp-air tunnel --spec spec.local.yaml
```

The command finds the existing simulation and SSH service without changing
them, forwards local port `6443` to the API VIP, and writes
`kubeconfig.tunnel` beside the downloaded kubeconfig with mode `0600`. It keeps
TLS verification enabled by setting the original API hostname as the TLS server
name. The command prints the exact `KUBECONFIG=... oc get nodes` invocation;
press Ctrl-C to close the tunnel. Use `--local-port PORT` if 6443 is occupied.

## Open the OpenShift web console

On Linux, open the private console through the Air jump host:

```sh
uv run ocp-air console --spec spec.local.yaml
```

The command prefers a supported system-default Chromium-family browser, then
searches for Chromium, Chrome, Edge, or Brave. A Flatpak installation of
`org.chromium.Chromium` is supported as a fallback, which works well on
immutable Linux distributions. Use `--browser PATH` to select a native
executable and `--socks-port PORT` if port 1080 is occupied. It starts a
foreground SOCKS proxy, opens a persistent browser profile dedicated to the
simulation, and closes the proxy when the browser exits or you press Ctrl-C.

The console uses the cluster-generated ingress certificate, so the isolated
profile requires a one-time certificate-warning acceptance. TLS checking is not
disabled. The command prints the `kubeadmin` password-file path without reading
or displaying the password. Use `--print-only` to inspect the safely quoted SSH
and browser commands without launching either process.


## Development boundary probes

See [the boundary report and probe instructions](docs/live-boundary-report.md) for
live evidence, retained resources, remaining gaps, and the opt-in probe runner.
Use its `--session` mode for repeated diagnostics without repeated 1Password reads.
Live checks are never part of the default pytest suite.
