#!/usr/bin/env python3
"""Build or verify a deterministic manifest for one Git release snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
import tomllib
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA_VERSION = 1
REQUIRED_FILES = (
    ".github/workflows/clean-install-smoke.yml",
    ".github/workflows/release-images.yml",
    ".env.example",
    "Dockerfile",
    "README.md",
    "app/model_preflight.py",
    "compose.yaml",
    "docs/CURRENT_STATUS.md",
    "docs/FIRST_SIMULATION.md",
    "docs/releases/v1.0.0-draft.md",
    "docs/validation/stage7/RELEASE_CHECKLIST.md",
    "executor/Dockerfile",
    "pyproject.toml",
    "scripts/install.ps1",
    "scripts/install.sh",
    "scripts/diagnostics/acouagent_doctor.py",
    "scripts/skill_library/audit_report.py",
    "scripts/validation/first_simulation_acceptance.py",
    "scripts/validation/release_manifest.py",
)
KEY_FILES = REQUIRED_FILES


class ManifestError(RuntimeError):
    pass


def _git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise ManifestError(message or "git_command_failed")
    return result.stdout


def _parse_tree(raw: bytes) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            metadata, path_bytes = record.split(b"\t", 1)
            mode, kind, object_id = metadata.decode("ascii").split(" ", 2)
            path = path_bytes.decode("utf-8")
        except (UnicodeDecodeError, ValueError) as exc:
            raise ManifestError("invalid_git_tree_record") from exc
        entries.append({"path": path, "mode": mode, "type": kind, "object_id": object_id})
    return sorted(entries, key=lambda item: item["path"])


def _is_forbidden(path: str) -> bool:
    parts = PurePosixPath(path).parts
    name = parts[-1] if parts else path
    return (
        "__pycache__" in parts
        or name == ".env"
        or name.startswith(".env.bak.")
        or name.endswith((".db", ".log", ".pyc", ".sqlite", ".tar.gz"))
    )


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _digest_without_field(manifest: dict[str, Any]) -> str:
    content = dict(manifest)
    content.pop("manifest_sha256", None)
    return hashlib.sha256(_canonical(content)).hexdigest()


def verify_manifest(manifest: dict[str, Any]) -> bool:
    expected = manifest.get("manifest_sha256")
    return (
        isinstance(expected, str)
        and len(expected) == 64
        and manifest.get("schema_version") == SCHEMA_VERSION
        and expected == _digest_without_field(manifest)
    )


def _blob(repo: Path, ref: str, path: str) -> bytes:
    return _git(repo, "show", f"{ref}:{path}")


def build_manifest(repo: Path, ref: str = "HEAD") -> dict[str, Any]:
    repo = repo.resolve()
    commit_sha = _git(repo, "rev-parse", f"{ref}^{{commit}}").decode("ascii").strip()
    commit_timestamp = (
        _git(repo, "show", "-s", "--format=%cI", commit_sha).decode("ascii").strip()
    )
    source_date_epoch = int(
        _git(repo, "show", "-s", "--format=%ct", commit_sha).decode("ascii").strip()
    )
    entries = _parse_tree(_git(repo, "ls-tree", "-rz", "--full-tree", commit_sha))
    paths = {item["path"] for item in entries if item["type"] == "blob"}
    missing = sorted(set(REQUIRED_FILES) - paths)
    forbidden = sorted(path for path in paths if _is_forbidden(path))
    try:
        project = tomllib.loads(_blob(repo, commit_sha, "pyproject.toml").decode("utf-8"))[
            "project"
        ]
        project_name = project["name"]
        project_version = project["version"]
    except (KeyError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ManifestError("invalid_project_metadata") from exc
    key_hashes = {
        path: hashlib.sha256(_blob(repo, commit_sha, path)).hexdigest()
        for path in KEY_FILES
        if path in paths
    }
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "source": {
            "ref": ref,
            "commit_sha": commit_sha,
            "commit_timestamp": commit_timestamp,
            "source_date_epoch": source_date_epoch,
        },
        "project": {"name": project_name, "version": project_version},
        "inventory": {
            "tracked_entry_count": len(entries),
            "tracked_blob_count": sum(item["type"] == "blob" for item in entries),
            "entries": entries,
        },
        "integrity": {
            "required_files": list(REQUIRED_FILES),
            "missing_required_files": missing,
            "forbidden_tracked_paths": forbidden,
            "key_file_sha256": key_hashes,
        },
        "release_files_ready": not missing and not forbidden,
    }
    manifest["manifest_sha256"] = _digest_without_field(manifest)
    return manifest


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=path.name + ".", delete=False
    ) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--ref", default="HEAD")
    parser.add_argument("--output", type=Path, default=Path("release-manifest.json"))
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    if args.verify is not None:
        try:
            manifest = json.loads(args.verify.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            print(json.dumps({"valid": False, "error": "manifest_unreadable"}))
            return 1
        valid = isinstance(manifest, dict) and verify_manifest(manifest)
        print(json.dumps({"valid": valid, "manifest_sha256": manifest.get("manifest_sha256")}))
        return 0 if valid else 1
    try:
        manifest = build_manifest(args.repo, args.ref)
        _atomic_write(args.output, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    except (ManifestError, OSError, TypeError, ValueError) as exc:
        print(json.dumps({"ready": False, "error": str(exc) or "manifest_generation_failed"}))
        return 1
    print(
        json.dumps(
            {
                "ready": manifest["release_files_ready"],
                "commit_sha": manifest["source"]["commit_sha"],
                "tracked_blob_count": manifest["inventory"]["tracked_blob_count"],
                "manifest_sha256": manifest["manifest_sha256"],
                "output": str(args.output),
            }
        )
    )
    return 0 if manifest["release_files_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
