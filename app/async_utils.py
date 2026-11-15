"""Small async helpers that do not depend on the event loop's thread pool."""
from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from collections.abc import Callable
from typing import Any


async def await_thread(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run one bounded blocking call in a dedicated daemon thread."""
    future: concurrent.futures.Future[Any] = concurrent.futures.Future()

    def worker():
        try:
            value = fn(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 - forwarded to the awaiting coroutine
            future.set_exception(exc)
        else:
            future.set_result(value)

    threading.Thread(target=worker, name="acouagent-bounded-call", daemon=True).start()
    # Poll the thread-safe future. This avoids depending on loop-owned thread
    # callbacks, which are brittle when tests create and close event loops.
    while not future.done():
        await asyncio.sleep(0.01)
    return future.result()


__all__ = ["await_thread"]
