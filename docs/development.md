# Development

This guide describes the current project and verification commands. It does not
require a particular version-control client or shell wrapper.

## Set up

The project requires Python 3.14 or later and uses `uv`:

```sh
uv sync
```

Run `just` to list the checked-in `install`, `run`, `lint`, and `test`
recipes.

## Source layout

| Path | Purpose |
| --- | --- |
| `src/ocp_dsx_air/cli` | Typer commands, composition roots, and reporting. |
| `src/ocp_dsx_air/models` | Spec validation, runtime values, and intent resolution. |
| `src/ocp_dsx_air/core` | Contracts, decisions, workflows, ports, polling, and errors. |
| `src/ocp_dsx_air/adapters/assisted` | Assisted SDK transport, mapping, and operations. |
| `src/ocp_dsx_air/adapters/air` | Air SDK transport, mapping, artifacts, jump-host operations, and SSH. |
| `tests/test_core` | Decisions, workflows, CLI behavior, and runtime tests. |
| `tests/test_adapters` | SDK request, response, transport, mapping, and artifact tests. |
| `tools/live_probe.py` | Explicit live boundary runner outside the default tests. |

## Maintain a boundary

Core code uses project-owned dataclasses and enums. Generated SDK models stay
inside adapters. A remote-operation change normally requires:

1. Add domain data only when the workflow needs it.
2. Add the operation to the narrow port protocol.
3. Validate and map the SDK response in the adapter.
4. Keep HTTP status extraction and error sanitization in the transport.
5. Test real installed-SDK decoding or model behavior when it is relevant.
6. Test workflow policy with fakes.

Do not return SDK models, raw dictionaries, bodies, headers, signed URLs, or
exception representations through a port. Missing or malformed required fields
must raise a service-specific domain error.

Mutations belong in workflows after an explicit decision. Long operations must
re-observe remote state and use the injected clock for deterministic tests.

## Run checks

```sh
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run pyright src
```

The `just test` and `just lint` recipes run the standard pytest and Ruff
checks. Type-check a changed test or tool by adding its path to the Pyright
command. Start with focused checks and run the full test suite before completion.

## Check documentation

The repository documentation skill is in
`.agents/skills/ocp-dsx-air-docs/`. It requires claims to come from current
code, tests, CLI behavior, or authoritative sources.

```sh
uv run python .agents/skills/ocp-dsx-air-docs/scripts/check_markdown_links.py README.md docs
```

Parse spec examples with `load_spec`. Verify options against current CLI help.

## Run live boundary probes

The live probe is opt-in, excluded from `pytest`, and can create remote
resources. Inspect its help and source before use:

```sh
uv run python tools/live_probe.py --help
uv run python tools/live_probe.py --spec spec.yaml
uv run python tools/live_probe.py --spec spec.yaml --session
```

The persistent session accepts `diagnose`, `deploy`, and `quit`.
`diagnose` performs read-only checks of journaled resources. `deploy` can
create and delete a disposable lab under the runner's ownership and attempt
limits.

The runner emits allowlisted diagnostics. It suppresses response data, headers,
model representations, subprocess output, signed URLs, and credentials. Create
synthetic regression fixtures; do not save live response bodies as test data.
