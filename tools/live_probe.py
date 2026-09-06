"""Explicit live checks. Output contains only allowlisted diagnostic metadata."""

import argparse
import fcntl
import json
import logging
import os
import runpy
import shutil
import signal
import sys
import time
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from uuid import UUID, uuid4

from ocp_dsx_air.adapters.air import NvidiaAirAdapter
from ocp_dsx_air.adapters.assisted import AssistedInstallerAdapter
from ocp_dsx_air.cli.commands.deploy import _read_secret
from ocp_dsx_air.core.runtime import SystemClock
from ocp_dsx_air.core.workflows import deploy_lab
from ocp_dsx_air.models.resolution import resolve_deploy_intent
from ocp_dsx_air.models.runtime import ResolvedCredentials
from ocp_dsx_air.models.spec import LabSpec, load_spec


@contextmanager
def silence():
    """Suppress Python and native writes, including inherited subprocess output."""
    sys.stdout.flush()
    sys.stderr.flush()
    saved = [os.dup(1), os.dup(2)]
    old_level = logging.root.manager.disable
    try:
        with open(os.devnull, "w") as sink:
            os.dup2(sink.fileno(), 1)
            os.dup2(sink.fileno(), 2)
        logging.disable(logging.CRITICAL)
        with open(os.devnull, "w") as sink, redirect_stdout(sink), redirect_stderr(sink):
            yield
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        for target, fd in zip((1, 2), saved, strict=True):
            os.dup2(fd, target)
            os.close(fd)
        logging.disable(old_level)


def failure_status(exc):
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        for attribute in ("status_code", "status", "code"):
            value = getattr(exc, attribute, None)
            if type(value) is int and 100 <= value <= 599:
                return value
        exc = exc.__cause__ or exc.__context__
    return None


def probe(operation, function):
    started = time.monotonic()
    result = None
    record = {"operation": operation}
    try:
        with silence():
            result = function()
        record["outcome"] = "passed"
        record["response_type"] = type(result).__name__
    except BaseException as exc:
        record["outcome"] = "failed"
        record["http_status"] = failure_status(exc)
        # Only builtin/domain exception names, never exception messages.
        record["failure_category"] = (
            "interrupted" if isinstance(exc, (KeyboardInterrupt, SystemExit)) else "operation_failed"
        )
    record["seconds"] = round(time.monotonic() - started, 2)
    print(json.dumps(record), flush=True)
    return record["outcome"] == "passed", result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--deploy", action="store_true")
    parser.add_argument("--session", action="store_true")
    parser.add_argument("--diagnose-cluster", action="store_true")
    parser.add_argument("--state", type=Path, default=Path("/tmp/ocp-air-live-probe.json"))
    args = parser.parse_args()
    ok, spec = probe("load_configuration", lambda: load_spec(args.spec))
    if not ok:
        return 1
    if args.session:
        return session(spec, args.state)
    target = "probe-" + uuid4().hex[:12]
    passed = True
    for service, field, factory, methods in (
        (
            "assisted",
            "ai_offlinetoken_file",
            lambda key: AssistedInstallerAdapter(key),
            ("find_cluster", "find_infraenv"),
        ),
        ("air", "air_api_key_file", lambda key: NvidiaAirAdapter(api_key=key), ("find_simulation", "find_image")),
    ):
        ok, adapter = probe(
            service + ".credentials",
            lambda field=field, factory=factory: factory(
                _read_secret(getattr(spec.auth, field), field="auth." + field)
            ),
        )
        passed = passed and ok
        if not ok:
            continue
        for method in methods:
            ok, _ = probe(
                service + "." + method, lambda method=method, adapter=adapter: getattr(adapter, method)(target)
            )
            passed = passed and ok
    if passed and args.diagnose_cluster:
        ok, _ = probe("cluster_diagnosis", lambda: diagnose_cluster(spec, args.state))
        passed = passed and ok
    if passed and args.deploy:
        ok, _ = probe("disposable_deployment", lambda: full_deploy(spec, args.state))
        passed = passed and ok
    return 0 if passed else 1


def session(spec, state_path):
    """Resolve credentials once; fork fresh code for each command without another op call."""
    cache = {}

    def resolve():
        for field in type(spec.auth).model_fields:
            source = getattr(spec.auth, field)
            if source not in cache:
                cache[source] = _read_secret(source, field="auth." + field)

    ok, _ = probe("session_credentials", resolve)
    if not ok:
        return 1
    print('{"session":"ready","commands":["diagnose","deploy","quit"]}', flush=True)
    for command in sys.stdin:
        command = command.strip()
        if command == "quit":
            cache.clear()
            return 0
        if command not in {"diagnose", "deploy"}:
            print('{"session":"unknown_command"}', flush=True)
            continue
        sys.stdout.flush()
        pid = os.fork()
        if pid == 0:
            try:
                for module in list(sys.modules):
                    if module == "ocp_dsx_air" or module.startswith("ocp_dsx_air."):
                        del sys.modules[module]
                fresh = runpy.run_path(__file__, run_name="live_probe_child")
                fresh["full_deploy"].__globals__["_read_secret"] = lambda source, **kw: cache[source]
                fresh["full_deploy"].__globals__["live_output"] = live_output
                function = fresh["diagnose_cluster" if command == "diagnose" else "full_deploy"]
                ok, _ = fresh["probe"](
                    command,
                    lambda function=function, fresh=fresh: function(
                        fresh["LabSpec"].model_validate(spec.model_dump(mode="json")), state_path
                    ),
                )
                sys.stdout.flush()
                os._exit(0 if ok else 1)
            except BaseException:
                os._exit(1)
        os.waitpid(pid, 0)
        print('{"session":"ready"}', flush=True)
    cache.clear()
    return 0


def diagnose_cluster(spec, state_path):
    spec = LabSpec.model_validate(spec.model_dump(mode="json"))
    from ocp_dsx_air.core.decisions import decide_cluster_action

    state = json.loads(state_path.read_text())
    key = _read_secret(spec.auth.ai_offlinetoken_file, field="auth.ai_offlinetoken_file")
    adapter = AssistedInstallerAdapter(key)
    observed = adapter.find_cluster(state["name"])
    raw = adapter._transport.call(
        "diagnose cluster shape",
        lambda api: api.v2_get_cluster(
            str(observed.id), exclude_hosts=True, _preload_content=False, _request_timeout=30
        ),
    )
    try:
        payload = json.loads(raw.data)
        live_output(
            {
                "operation": "raw_cluster_shape",
                "machine_networks_type": type(payload.get("machine_networks")).__name__,
                "machine_networks_present": "machine_networks" in payload,
                "machine_networks_nonempty": bool(payload.get("machine_networks")),
            }
        )
    finally:
        raw.release_conn()
    intent = resolve_deploy_intent(spec, cache_root=Path("/tmp/probe-unused"))
    live_output(
        {
            "operation": "cluster_shape",
            "version_is_minor_expansion": observed.ocp_version.startswith(intent.cluster.ocp_version + "."),
            "machine_networks_missing": not observed.machine_networks,
            "machine_network_count_matches": len(observed.machine_networks) == len(intent.cluster.machine_networks),
            "status": observed.status.value,
            "install_started": observed.install_started,
            "install_completed": observed.install_completed,
            "start_timestamp_zero": str(payload.get("install_started_at", "")).startswith("0001-01-01"),
            "completion_timestamp_zero": str(payload.get("install_completed_at", "")).startswith("0001-01-01"),
        }
    )
    hosts = adapter.list_hosts(observed.id)
    live_output({"operation": "list_owned_cluster_hosts", "outcome": "passed", "response_type": type(hosts).__name__})
    for executable in ("qemu-img", "ssh"):
        live_output(
            {"operation": "prerequisite", "executable": executable, "available": shutil.which(executable) is not None}
        )
    decision = decide_cluster_action(replace(intent.cluster, name=state["name"]), observed, replace=False)
    live_output(
        {"operation": "cluster_decision", "action": decision.action.value, "drift_fields": list(decision.drift)}
    )


class Journal:
    def __init__(self, path, output):
        self.path = path
        self.output = output
        if path.exists():
            self.data = json.loads(path.read_text())
        else:
            self.data = {"name": "probe-" + uuid4().hex[:12], "attempts": 0, "resources": {}, "complete": False}
        self.save()

    def save(self):
        temporary = self.path.with_suffix(".tmp")
        with open(temporary, "w", opener=lambda p, flags: os.open(p, flags, 0o600)) as handle:
            json.dump(self.data, handle)
        os.replace(temporary, self.path)

    def owns(self, kind, resource):
        return str(resource) in self.data["resources"].get(kind, [])

    def add(self, kind, resource):
        identifier = str(UUID(str(resource)))
        values = self.data["resources"].setdefault(kind, [])
        if identifier not in values:
            values.append(identifier)
            self.save()
            self.output({"operation": "created", "kind": kind, "id": identifier})


class ObservedTransport:
    def __init__(self, transport, journal):
        self.transport = transport
        self.journal = journal

    def __getattr__(self, name):
        return getattr(self.transport, name)

    def call(self, operation, request):
        start = time.monotonic()
        try:
            result = self.transport.call(operation, request)
            kind = {
                "create cluster": "cluster",
                "create InfraEnv": "infraenv",
                "create image": "image",
                "import simulation": "simulation",
            }.get(operation)
            if kind:
                self.journal.add(kind, result.id)
                if kind == "image" and str(getattr(result, "name", "")).startswith("ocp-dsx-air-blank-"):
                    self.journal.data.setdefault("shared_images", []).append(str(result.id))
                    self.journal.save()
            self.journal.output(
                {"operation": operation, "outcome": "passed", "seconds": round(time.monotonic() - start, 2)}
            )
            return result
        except BaseException as exc:
            self.journal.output({"operation": operation, "outcome": "failed", "http_status": failure_status(exc)})
            raise


class OwnedAdapter:
    """Refuse mutations or reuse of a lab that this run did not create."""

    def __init__(self, adapter, journal):
        self.adapter = adapter
        self.journal = journal

    def __getattr__(self, name):
        function = getattr(self.adapter, name)

        def call(*args, **kwargs):
            kind = {
                "upload_image": "image",
                "delete_image": "image",
                "start_simulation": "simulation",
                "shutdown_simulation": "simulation",
                "delete_simulation": "simulation",
                "ensure_jump_host": "simulation",
                "delete_cluster": "cluster",
                "start_installation": "cluster",
                "delete_infraenv": "infraenv",
                "update_host_role": "infraenv",
            }.get(name)
            if kind and not self.journal.owns(kind, args[0]):
                raise RuntimeError("Refusing mutation of an unowned resource")
            result = function(*args, **kwargs)
            lookup_kind = {"find_cluster": "cluster", "find_infraenv": "infraenv", "find_simulation": "simulation"}.get(
                name
            )
            if lookup_kind and result is not None and not self.journal.owns(lookup_kind, result.id):
                raise RuntimeError("Disposable lab name collision")
            return result

        return call


def full_deploy(spec, state_path):
    spec = LabSpec.model_validate(spec.model_dump(mode="json"))
    for executable in ("qemu-img", "ssh"):
        available = shutil.which(executable) is not None
        live_output({"operation": "prerequisite", "executable": executable, "available": available})
        if not available:
            raise RuntimeError("Missing live deployment prerequisite")
    # This descriptor points to the silenced stream; diagnostics are supplied by main instead.
    lock = open(state_path.with_suffix(".lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        journal = Journal(state_path, live_output)
        if journal.data["complete"] or journal.data["attempts"] >= 3:
            raise RuntimeError("Run complete or attempt limit reached")
        credentials = ResolvedCredentials(
            **{
                dest: _read_secret(getattr(spec.auth, field), field="auth." + field)
                for dest, field in (
                    ("air_api_key", "air_api_key_file"),
                    ("ai_offline_token", "ai_offlinetoken_file"),
                    ("pull_secret", "pull_secret_file"),
                    ("ssh_public_key", "ssh_public_key_file"),
                    ("jump_host_password", "jump_host_password_file"),
                )
            }
        )
        assisted = AssistedInstallerAdapter(credentials.ai_offline_token)
        air = NvidiaAirAdapter(api_key=credentials.air_api_key)
        for adapter in (assisted, air):
            adapter._transport = ObservedTransport(adapter._transport, journal)
        assisted, air = OwnedAdapter(assisted, journal), OwnedAdapter(air, journal)
        name = journal.data["name"]
        intent = resolve_deploy_intent(spec, cache_root=state_path.parent / (name + "-cache"))
        names = {node.name: f"{name}-node-{index}" for index, node in enumerate(intent.nodes)}
        intent = replace(
            intent,
            simulation_name=name,
            cluster=replace(intent.cluster, name=name),
            nodes=tuple(replace(node, name=names[node.name]) for node in intent.nodes),
            links=tuple(
                replace(
                    link,
                    endpoints=tuple(
                        replace(end, node_name=names.get(end.node_name, end.node_name)) for end in link.endpoints
                    ),
                )
                for link in intent.links
            ),
        )
        journal.data["attempts"] += 1
        journal.save()
        signal.signal(signal.SIGALRM, lambda *a: (_ for _ in ()).throw(TimeoutError()))
        signal.alarm(7200)

        class Reporter:
            def emit(self, event):
                live_output({"phase": event.phase.value})

        for stage in ("deploy", "resume"):
            live_output({"stage": stage, "attempt": journal.data["attempts"]})
            result = deploy_lab(
                intent,
                credentials=credentials,
                assisted=assisted,
                air=air,
                jump_host=air,
                reporter=Reporter(),
                clock=SystemClock(),
            )
            for path in (result.credentials.kubeconfig, result.credentials.kubeadmin_password):
                if not path.is_file() or path.stat().st_size == 0 or path.stat().st_mode & 0o077:
                    raise RuntimeError("Invalid credential artifact")
        # Delete only journaled objects. Confirm eventual deletion through direct SDK GETs.
        for kind, adapter, method in (
            ("simulation", air, "delete_simulation"),
            ("infraenv", assisted, "delete_infraenv"),
            ("cluster", assisted, "delete_cluster"),
            ("image", air, "delete_image"),
        ):
            for identifier in journal.data["resources"].get(kind, []):
                if kind == "image" and identifier in journal.data.get("shared_images", []):
                    live_output({"operation": "retain_shared_image", "id": identifier})
                    continue
                getattr(adapter, method)(UUID(identifier))
                confirmed = False
                for _ in range(60):
                    try:
                        transport = adapter.adapter._transport

                        def get(api, kind=kind, identifier=identifier):
                            if kind == "cluster":
                                return api.v2_get_cluster(identifier)
                            if kind == "infraenv":
                                return api.get_infra_env(identifier)
                            return getattr(api, "simulations" if kind == "simulation" else "images").get(identifier)

                        transport.call("verify deletion", get)
                    except Exception as exc:
                        if failure_status(exc) == 404:
                            confirmed = True
                            break
                        raise
                    time.sleep(5)
                if not confirmed:
                    raise RuntimeError("Deletion not confirmed")
                journal.data.setdefault("deleted", []).append(identifier)
                journal.save()
        journal.data["complete"] = True
        journal.save()
    finally:
        signal.alarm(0)
        lock.close()


def live_output(record):
    pass


if __name__ == "__main__":
    output_fd = os.dup(1)

    def write_record(record):
        os.write(output_fd, (json.dumps(record) + "\n").encode())

    live_output = write_record
    raise SystemExit(main())
