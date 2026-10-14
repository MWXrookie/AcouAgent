"""Host experiment evidence journal, never a runtime approval credential."""
import json
import os
from pathlib import Path
import tempfile
import time


class WindowJournal:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def write(self, name, data):
        if name not in {"command", "before", "after", "result", "attempt"}:
            raise ValueError("invalid_evidence_name")
        encoded = json.dumps(data, allow_nan=False, indent=2).encode()
        if len(encoded) > 2000000:
            raise ValueError("oversize_window_evidence")
        path = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.directory, delete=False) as stream:
                path = Path(stream.name)
                stream.write(encoded + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(path, self.directory / (name + ".json"))
        finally:
            if path is not None and path.exists():
                path.unlink()

    def capture(self, command, observe, execute):
        """Persist command/before before execute; save after even when execute fails.

        Caller owns and cleans only its test resources after this method exits.
        Observer/execute callbacks are installed host test code, not HTTP inputs.
        """
        start = time.monotonic()
        stage = "command"
        error = None
        after_error = None
        result = None
        try:
            self.write("command", command)
            stage = "before"
            self.write("before", observe())
            stage = "execute"
            result = execute()
            self.write("result", result)
        except Exception as failure:
            error = type(failure).__name__
            raise
        finally:
            try:
                self.write("after", observe())
            except Exception as failure:
                after_error = type(failure).__name__
            self.write("attempt", {"stage": stage, "error_type": error,
                "after_error_type": after_error, "duration_s": time.monotonic() - start,
                "runtime_approved": False, "trusted_run": False, "run_bound": False})
        if after_error:
            raise ValueError("after_observation_unavailable")
        return result
