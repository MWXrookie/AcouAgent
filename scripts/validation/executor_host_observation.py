"""Host-side read-only Docker observation. Never a runtime approval credential.

No Docker socket or CLI is exposed to gateway/generated code. The host Docker
administrator and this installed collector are trusted; JSON returned by a
caller cannot impersonate this collection process. Image ID alone does not
prove writable-layer contents, package versions, or association with a run.
"""
import json
import os
import selectors
import signal
import time
import re
import subprocess
from datetime import datetime, timezone

CONTAINER = "jwave-executor"
FIELDS = {"container_id", "image_id", "running", "started_at", "restart_count"}
FORMAT = ('{"container_id":{{json .Id}},"image_id":{{json .Image}},'
          '"running":{{json .State.Running}},"started_at":{{json .State.StartedAt}},'
          '"restart_count":{{json .RestartCount}}}')



def _started_at_valid(value):
    # Docker emits UTC RFC3339; reject arbitrary text and naive/local timestamps.
    if not isinstance(value, str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z", value):
        return False
    try:
        stamp = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return stamp.year >= 1970


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_observation_key")
        result[key] = value
    return result


def _valid(value):
    return (isinstance(value, dict) and set(value) == FIELDS
            and isinstance(value["container_id"], str)
            and re.fullmatch(r"[0-9a-f]{64}", value["container_id"]) is not None
            and isinstance(value["image_id"], str)
            and re.fullmatch(r"sha256:[0-9a-f]{64}", value["image_id"]) is not None
            and value["running"] is True
            and _started_at_valid(value["started_at"])
            and type(value["restart_count"]) is int and value["restart_count"] >= 0)


def _bounded_output(command, *, timeout=10, limit=2048):
    """Linux host-only subprocess capture: at most limit+1 bytes, no stderr data.

    One deadline covers reads and exit. Kill the owned process group even when
    its leader exited but a child retained the pipe. This helper is not exposed
    to gateway or caller-selected commands.
    """
    deadline = time.monotonic() + timeout
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               start_new_session=True)
    selector = selectors.DefaultSelector()
    output = bytearray()
    try:
        selector.register(process.stdout, selectors.EVENT_READ)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout)
            for key, _ in selector.select(remaining):
                chunk = os.read(key.fd, limit + 1 - len(output))
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                output.extend(chunk)
                if len(output) > limit:
                    raise ValueError("oversize_container_observation")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(command, timeout)
        code = process.wait(timeout=remaining)
        if code:
            raise subprocess.CalledProcessError(code, command)
        return bytes(output).decode("utf-8", errors="strict")
    finally:
        selector.close()
        process.stdout.close()
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=1)


def collect():
    """Inspect only named identity/state fields; never read Config.Env or Key."""
    output = _bounded_output(
        ["docker", "inspect", "--type", "container", "--format", FORMAT, CONTAINER],
        timeout=10, limit=2048)
    snapshot = json.loads(output, object_pairs_hook=_unique_object)
    if not _valid(snapshot):
        raise ValueError("invalid_container_observation")
    return snapshot


def compare_window(before, after):
    """Lifecycle consistency only; cannot certify a run used this container."""
    return {"version": "executor-host-observation.v1",
            "lifecycle_stable": bool(_valid(before) and _valid(after) and before == after),
            "runtime_approved": False, "trusted_run": False,
            "run_bound": False}


def collect_window():
    before = collect()
    after = collect()
    return {"observed_at": datetime.now(timezone.utc).isoformat(),
            "source": "host_docker_cli_fixed_container", "before": before, "after": after,
            "check": compare_window(before, after)}


if __name__ == "__main__":
    print(json.dumps(collect_window(), allow_nan=False))
