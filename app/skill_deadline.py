"""Per-call monotonic budget, shared across synchronous skill DB operations."""
import time
from contextvars import ContextVar
from contextlib import contextmanager
_end=ContextVar("skill_call_deadline",default=None)
def expired():
    end=_end.get()
    return end is not None and time.monotonic()>=end
def check():
    if expired():raise TimeoutError("skill_context_deadline")
def sqlite_timeout(default=2):
    check()
    end=_end.get()
    return default if end is None else max(0.001,min(default,end-time.monotonic(),0.25))
@contextmanager
def limited(seconds=2):
    if type(seconds) not in (int,float) or not 0<seconds<=2:raise ValueError("skill_deadline_limit")
    parent=_end.get();end=time.monotonic()+seconds
    token=_end.set(end if parent is None else min(parent,end))
    try:
        check();yield;check()
    finally:
        _end.reset(token)
