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


## Development boundary probes

See [the boundary report and probe instructions](docs/live-boundary-report.md) for
live evidence, retained resources, remaining gaps, and the opt-in probe runner.
Use its `--session` mode for repeated diagnostics without repeated 1Password reads.
Live checks are never part of the default pytest suite.
