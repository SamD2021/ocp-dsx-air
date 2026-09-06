# Deployment boundary verification

## Evidence from 2026-09-06

This run used `assisted-service-client==2.55.0.post105` and `nv-air-sdk==1.7.0` and a separately named disposable lab. Only the
probe process read the local spec and resolved credentials. Raw responses, headers,
URLs, model representations, and credential values were suppressed.

Three live deployment attempts were used. Full installation, successful resume,
and cleanup were **not completed**. No Air simulation or Air image was created.

| Boundary | Live evidence | Offline evidence |
| --- | --- | --- |
| 1Password resolution | Passed; persistent session reused credentials after user requested it | Existing loader tests plus output suppression tests |
| Assisted token exchange, cluster and InfraEnv lists | Passed | Real generated endpoint HTTP decoding; malformed list rejection |
| Cluster create/read and request-to-observation reconciliation | Passed after fixes | Existing request-model tests; real Cluster decoding; drift regressions |
| InfraEnv create/read and signed ISO URL | Passed | Existing adapter, model, and ISO tests |
| Discovery ISO download | Download call completed and workflow re-observed InfraEnv | Existing streaming, failure, and atomic-file tests |
| Host lists for owned cluster | Passed with no discovered hosts | Real Host decoding and existing inventory/progress mappings |
| Air simulation/image list | Passed | Real SDK HTTP response decoding and 401/403/429/500 translation |
| Blank disk generation | Blocked: qemu-img unavailable | Existing artifact tests; runner now checks prerequisites |
| Air image create/upload, simulation import/start, SSH, host discovery | Unexercised live | Existing adapter/workflow tests; not equivalent to live proof |
| Installation, reboot/resume, credential download, cleanup | Unexercised live | Existing workflow/credential tests; not equivalent to live proof |

## Defects and corrections

- Generated `InfraEnvList`, `ClusterList`, and `HostList` aliases leave JSON objects
  as dictionaries. The transport now decodes them as lists of actual SDK models.
  Tests also reject malformed arrays rather than treating them as absent resources.
- Go zero-time installation markers decode into non-null year-0001 Python datetimes.
  They now mean unset instead of incorrectly indicating installed/started clusters.
- A minor release request such as `4.19` can be returned as a stable patch release.
  Compatibility accepts only matching stable patches; exact patch requests remain strict.
- An absent machine network is deferred only in `pending-for-input`, before either
  installation marker is set. Conflicting networks and missing networks at readiness
  remain errors. This does not prove the network will resolve correctly after discovery.
- The development runner initially carried old Enum instances across module reloads.
  It now reconstructs the spec against the freshly loaded models before each child run.
  Attempt two stopped without additional resource creation because of this harness issue.

## Resource disposition

Failed runs are retained, as agreed:

- Assisted cluster: `e21c5c68-0bb1-44d5-9b9b-1adb2e15b6fb`
- Assisted InfraEnv: `d0dc72f0-f7ff-4021-9fba-27f633e94ed1`
- Ownership and attempt journal: `/tmp/ocp-air-live-probe.json`

No cleanup was attempted. The third attempt stopped after ISO handling; qemu-img
is absent and is required by the next local artifact step. The runner suppresses
exception text, so its initial failure report did not itself identify that dependency.
The local cache may contain a discovery ISO with embedded credentials; do not print
or commit it. The session process has exited and its credential cache is gone.

## Running the probes

Read-only checks (no deployment):

```sh
uv run python tools/live_probe.py --spec spec.local.yaml
```

For repeated operations, use one persistent Linux/POSIX session:

```sh
uv run python tools/live_probe.py --spec spec.local.yaml --session
```

The process resolves configured sources once. Enter `diagnose` for read-only checks
of the journaled cluster, `deploy` for a deployment plus resume verification and
success cleanup, or `quit` to release credentials. Each command uses fresh project
code in a child process and inherits the in-memory credential cache. No secret is
passed as a command argument or written to a temporary credential file.

Deployment requires `qemu-img` and system SSH configured for the appropriate agent.
The runner uses unique names and a separate cache, refuses mutation of unowned
resources, never passes `--replace`, and journals create responses before subsequent
mapping. A per-state lock prevents concurrent use of the same run. Use the default
journal for this exercise; do not create another state file to bypass the one-lab limit.
Each attempt has a two-hour alarm and the journal caps deployment at three attempts.

The existing run is at its attempt limit. Continuing requires an explicitly agreed
extension; do not delete its journal or create a second lab to bypass the limit.
Creation failures without a returned resource ID can still leave an unknown remote
resource; investigate such failures before retrying. Cleanup of shared blank images
is deliberately skipped and reported. Cleanup and the full successful lifecycle
remain unverified on a live deployment.

## Validation

436 tests passed after the runtime fixes. Ruff lint passed; the
repository already has unrelated formatting differences. Live failure injection
was not used. Fixtures contain synthetic values rather than captured responses.
