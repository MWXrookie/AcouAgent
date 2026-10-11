"""Operator-local literal token reader; never source a shell or print values."""
import os
import re
import stat
from pathlib import Path

PROJECT_ENV = Path(__file__).resolve().parents[1] / ".env"

def read_project_token():
    return _read_token_file(PROJECT_ENV)

def _read_token_file(path):
    # Existing project deployment may be group-writable (0664). This is a
    # literal read of an operator-owned secret, not executable configuration
    # or a trust-root approval; do not modify its existing permissions.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        metadata = os.fstat(fd)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid()
                or metadata.st_nlink != 1 or stat.S_IMODE(metadata.st_mode) & 0o002
                or not 0 < metadata.st_size <= 65536):
            raise ValueError("unsafe_project_environment")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise ValueError("project_environment_budget")
    finally:
        os.close(fd)
    try:
        text = raw.decode("utf-8")
    except UnicodeError:
        raise ValueError("invalid_project_environment_encoding") from None
    values = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        if key.strip() != "EXECUTOR_SHARED_TOKEN":
            continue
        if not separator:
            raise ValueError("invalid_project_token")
        value = value.strip()
        if len(value) >= 2 and value[0] in ("'", chr(34)) and value[-1] == value[0]:
            value = value[1:-1]
        if not re.fullmatch(r"[A-Za-z0-9._~+/=-]{32,256}", value):
            raise ValueError("invalid_project_token")
        values.append(value)
    if len(values) != 1:
        raise ValueError("missing_or_duplicate_project_token")
    return values[0]
