"""Single-request-at-a-time executor for the packaged jwave environment."""

import base64
import hmac
import hashlib
import importlib.metadata
import platform
import json
import os
import resource
import signal
import subprocess
import sys
import tempfile
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


TOKEN = os.environ.get("EXECUTOR_SHARED_TOKEN", "")
MAX_CODE_BYTES = 20000
MAX_OUTPUT_BYTES = 1024 * 1024
PR_SET_CHILD_SUBREAPER = 36


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _runtime_identity() -> dict:
    """Collected by the server BEFORE untrusted code runs; not from stdout.

    Version metadata is not immutable-image or package-content attestation.
    """
    versions = {}
    for name in ("jwave", "jax", "jaxlib", "jaxdf", "numpy"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    return {"executor_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "machine": platform.machine(), "library_versions": versions}


def _quota_cores(quota: int, period: int) -> int:
    """Return a small whole-core budget for a cgroup CPU quota.

    RLIMIT_CPU counts aggregate process CPU time across threads.  A two-core
    container can therefore consume roughly two CPU seconds per wall second.
    Keep the independent wall timeout unchanged while allowing that declared
    parallelism.  The upper bound prevents a malformed host quota from turning
    the secondary CPU guard into an effectively unbounded value.
    """
    if quota <= 0 or period <= 0:
        return 1
    return max(1, min(8, (quota + period - 1) // period))


def _cgroup_cpu_cores() -> int:
    """Read cgroup v2/v1 CPU quota, failing closed to the legacy one-core budget."""
    try:
        quota, period = Path("/sys/fs/cgroup/cpu.max").read_text(encoding="ascii").split()
        if quota != "max":
            return _quota_cores(int(quota), int(period))
    except (OSError, ValueError):
        pass
    try:
        quota = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us").read_text(encoding="ascii"))
        period = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us").read_text(encoding="ascii"))
        return _quota_cores(quota, period)
    except (OSError, ValueError):
        return 1


def _cpu_limit_seconds(timeout_seconds: int) -> int:
    return timeout_seconds * _cgroup_cpu_cores() + 2


def _limits(timeout_seconds: int) -> None:
    cpu_seconds = _cpu_limit_seconds(timeout_seconds)
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_OUTPUT_BYTES, MAX_OUTPUT_BYTES))
    resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
    resource.setrlimit(resource.RLIMIT_NPROC, (128, 128))


def _enable_child_subreaper() -> None:
    """Make direct test callers adopt orphaned user descendants like container PID 1."""
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number))


def _direct_child_pids() -> list[int]:
    children = Path(f"/proc/{os.getpid()}/task/{os.getpid()}/children")
    try:
        return [int(pid) for pid in children.read_text(encoding="ascii").split()]
    except FileNotFoundError:
        return []


def _reap_exited_children() -> None:
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if pid == 0:
            return


def _terminate_descendants() -> None:
    """Kill and reap the complete user tree, including descendants that called setsid()."""
    while True:
        children = _direct_child_pids()
        if not children:
            _reap_exited_children()
            if not _direct_child_pids():
                return
            continue
        for pid in children:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        _reap_exited_children()
        time.sleep(0.01)


def _kill_process_group(process: subprocess.Popen) -> None:
    """Terminate the initial user process group before reaping escaped descendants."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    if process.poll() is None:
        process.wait()


def execute(payload: dict) -> dict:
    code = payload.get("code")
    timeout_seconds = int(payload.get("timeout_seconds", 15))
    if not isinstance(code, str) or not code.strip():
        raise ValueError("code must be a non-empty Python string")
    if len(code.encode("utf-8")) > MAX_CODE_BYTES:
        raise ValueError("code is limited to 20000 bytes")
    if not 1 <= timeout_seconds <= 30:
        raise ValueError("timeout_seconds must be between 1 and 30")

    nonce = payload.get("request_nonce")
    if nonce is not None and (not isinstance(nonce, str) or len(nonce) != 32
                              or any(c not in "0123456789abcdef" for c in nonce)):
        raise ValueError("request_nonce must be 32 lowercase hex characters")
    runtime = _runtime_identity()
    started = time.monotonic()
    _enable_child_subreaper()
    with tempfile.TemporaryDirectory(prefix="mcp-run-", dir="/tmp") as workdir:
        work = Path(workdir)
        script = work / "main.py"
        stdout_path = work / "stdout.log"
        stderr_path = work / "stderr.log"
        script.write_text(code, encoding="utf-8")
        env = {
            "PATH": "/opt/jwave/bin:/usr/bin:/bin",
            "HOME": workdir,
            "MPLCONFIGDIR": f"{workdir}/mpl",
            "XDG_CACHE_HOME": f"{workdir}/cache",
            "LD_LIBRARY_PATH": "/opt/jwave/lib",
            "PYTHONUNBUFFERED": "1",
            "JAX_PLATFORMS": "cpu",
            "XLA_PYTHON_CLIENT_PREALLOCATE": "false",
            "XLA_FLAGS": "--xla_cpu_multi_thread_eigen=true intra_op_parallelism_threads=2",
        }
        timed_out = False
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            process = subprocess.Popen(
                [sys.executable, "-I", str(script)],
                cwd=workdir,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
                preexec_fn=lambda: _limits(timeout_seconds),
            )
            try:
                process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
            finally:
                _kill_process_group(process)
                _terminate_descendants()
        stdout_text = stdout_path.read_bytes()[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
        stderr_text = stderr_path.read_bytes()[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")

        # Check for gallery image in the per-request workdir only
        image_base64 = None
        result_png = Path(workdir) / "result.png"
        if result_png.exists():
            try:
                raw = result_png.read_bytes()
                if len(raw) <= 500 * 1024:  # 500KB limit
                    image_base64 = base64.b64encode(raw).decode("ascii")
            except Exception:
                pass

    receipt = {"version": "executor-receipt.v1", "request_nonce": nonce,
               "code_sha256": _sha256(code), "stdout_sha256": _sha256(stdout_text),
               "stderr_sha256": _sha256(stderr_text), "exit_code": process.returncode,
               "timed_out": timed_out, "timeout_seconds": timeout_seconds,
               "runtime": runtime,
               "runtime_sha256": _sha256(json.dumps(runtime, sort_keys=True, separators=(",", ":")))}
    return {
        "executor_receipt": receipt,
        "exit_code": process.returncode,
        "timed_out": timed_out,
        "duration_ms": round((time.monotonic() - started) * 1000, 1),
        "stdout": stdout_text,
        "stderr": stderr_text,
        "python": sys.executable,
        "image_base64": image_base64,
    }


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path != "/health":
            self._send(404, {"error": "not found"})
            return
        self._send(200, {"status": "ok", "python": "/opt/jwave/bin/python", "libraries": ["jwave", "jax", "jaxlib", "jaxdf", "equinox", "jaxtyping"]})

    def do_POST(self):  # noqa: N802
        if not hmac.compare_digest(self.headers.get("X-Executor-Token", ""), TOKEN):
            self._send(401, {"error": "unauthorized"})
            return
        if self.path != "/execute":
            self._send(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > MAX_CODE_BYTES + 1000:
                raise ValueError("request is too large")
            payload = json.loads(self.rfile.read(length))
            self._send(200, execute(payload))
        except Exception as exc:
            self._send(400, {"error": str(exc)})

    def _send(self, status: int, payload: dict):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        return


def _require_pid_namespace_init() -> None:
    if os.getpid() != 1:
        raise RuntimeError("executor server must run as container PID 1 so user code cannot kill its reaper")


if __name__ == "__main__":
    if len(TOKEN) < 32:
        raise RuntimeError("EXECUTOR_SHARED_TOKEN must be set and at least 32 characters long")
    _require_pid_namespace_init()
    HTTPServer(("0.0.0.0", 8010), Handler).serve_forever()
