"""Default-off bounded automatic learning pump for the gateway lifespan.

The daemon only schedules existing durable work. It never migrates, registers
profiles, changes configuration, or supplies its own admission authority.
"""
import logging
import math
import threading
import uuid

from app.skill_auto_learning import AutoLearning, enabled
from app.skill_authoring_schema import schema_ready
from app.skill_general_library import get_library

LOG = logging.getLogger(__name__)
_WORKER_LOCK = threading.Lock()
_WORKER = None


class AutoLearningWorker:
    def __init__(self, library, interval=5, *, engine_factory=AutoLearning):
        if type(interval) not in (int, float) or not math.isfinite(interval) or not 0 < interval <= 60:
            raise ValueError("invalid_learning_worker_interval")
        self.library = library
        self.interval = interval
        self._engine_factory = engine_factory
        self._stop = threading.Event()
        self._thread = None
        self._start_lock = threading.Lock()
        self.owner = "automatic-worker-" + uuid.uuid4().hex

    def start(self):
        with self._start_lock:
            if not enabled():
                return None
            if self._stop.is_set():
                raise RuntimeError("automatic_worker_stopped")
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="acouagent-auto-learning", daemon=True)
                self._thread.start()
        return self

    def _run(self):
        try:
            engine = self._engine_factory(self.library)
        except Exception:
            # Errors can contain model credentials or local paths. Log a stable
            # code only, without exception text or traceback interpolation.
            LOG.warning("automatic_learning_worker_initialization_failed")
            return
        while not self._stop.wait(self.interval):
            if not enabled():
                continue
            try:
                engine.tick(self.owner)
            except Exception:
                LOG.warning("automatic_learning_tick_failed")

    def is_alive(self):
        return self._thread is not None and self._thread.is_alive()

    def stop(self):
        """Stop scheduling; never kill an executor or wait indefinitely for it."""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1)
        return not self.is_alive()


def start_worker():
    """Lifespan entry: disabled/missing/legacy storage is a side-effect-free no-op."""
    global _WORKER
    if not enabled():
        return None
    with _WORKER_LOCK:
        if _WORKER is not None and _WORKER.is_alive():
            return _WORKER
        try:
            library = get_library()
            with library.store._read_db() as db:
                if not schema_ready(db):
                    return None
            worker = AutoLearningWorker(library)
            started = worker.start()
            if started is not None:
                _WORKER = worker
            return started
        except Exception:
            LOG.warning("automatic_learning_worker_not_ready")
            return None
