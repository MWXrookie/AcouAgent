"""Read-only approval package integrity. Never grants runtime/physics trust.

The operator selects a server-owned manifest and evidence root. JSON content
alone cannot authorize promotion; a future trusted approval registry must do so.
"""
import hashlib
import json
import os
import stat
from pathlib import Path

VERSION = "runtime-approval-package.v1"
FIELDS = {"schema_version", "approval_id", "status", "scope", "runtime_sha256",
          "image_id", "recipe_sha256", "validator_sha256", "evidence"}


def _sha(value):
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result


def _read_fd(fd, limit):
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        raise ValueError("not_regular_file")
    chunks = []
    remaining = limit + 1
    while remaining:
        block = os.read(fd, min(65536, remaining))
        if not block:
            break
        chunks.append(block)
        remaining -= len(block)
    value = b"".join(chunks)
    if len(value) > limit:
        raise ValueError("file_too_large")
    return value


def _read_manifest(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        return _read_fd(fd, 16384)
    finally:
        os.close(fd)


def _read_evidence(root_fd, name):
    # Walk from an already-open root; each component rejects symlinks.
    parts = name.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("noncanonical_path")
    current = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            os.close(current)
            current = next_fd
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=current)
        try:
            return _read_fd(fd, 8_000_000)
        finally:
            os.close(fd)
    finally:
        os.close(current)


def check_package(manifest, evidence_root):
    result = {"version": VERSION, "package_valid": False, "runtime_approved": False,
              "trusted_run": False, "reason": "invalid_package"}
    root_fd = None
    try:
        source = _read_manifest(manifest)
        data = json.loads(source, object_pairs_hook=_unique)
        if not isinstance(data, dict) or set(data) != FIELDS:
            return result
        if data["schema_version"] != VERSION or data["status"] not in {"candidate", "approved", "retired"}:
            return result
        if data["scope"] not in {"isolated_validation", "production"}:
            return result
        if not isinstance(data["approval_id"], str) or not 1 <= len(data["approval_id"]) <= 128:
            return result
        if any(not _sha(data[k]) for k in ("runtime_sha256", "recipe_sha256", "validator_sha256")):
            return result
        if not isinstance(data["image_id"], str) or not data["image_id"].startswith("sha256:") or not _sha(data["image_id"][7:]):
            return result
        entries = data["evidence"]
        if not isinstance(entries, list) or not 1 <= len(entries) <= 16:
            return result
        root_fd = os.open(evidence_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        seen = set()
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {"path", "sha256"} or not _sha(entry["sha256"]):
                return result
            name = entry["path"]
            if not isinstance(name, str) or not name or "\\" in name:
                return result
            rel = Path(name)
            if rel.is_absolute() or ".." in rel.parts or name in seen:
                return result
            seen.add(name)
            content = _read_evidence(root_fd, name)
            if hashlib.sha256(content).hexdigest() != entry["sha256"]:
                result["reason"] = "evidence_hash_mismatch"
                return result
        result.update(package_valid=True, approval_id=data["approval_id"], status=data["status"],
                      scope=data["scope"], runtime_sha256=data["runtime_sha256"],
                      image_id=data["image_id"], recipe_sha256=data["recipe_sha256"],
                      validator_sha256=data["validator_sha256"], manifest_sha256=hashlib.sha256(source).hexdigest(),
                      reason="trusted_registry_not_connected")
        # Even an approved label is untrusted without registry authorization.
        return result
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        return result
    finally:
        if root_fd is not None:
            os.close(root_fd)
