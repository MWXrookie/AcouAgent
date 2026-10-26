"""Explicit, backed-up preparation of the three exact independent references.

Downloads fixed upstream bytes only on --download-references. No reference ever
enters the executor: its sole preparation call is a harmless runtime receipt probe.
Does not edit .env, start workers, activate skills, or enable automatic learning.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import struct
import tempfile
import time
import urllib.request
import uuid
import zlib

import numpy as np

from app import config, dashboard, execution, executor_evidence
from app.skill_general_library import GeneralLibrary
from app.skill_general_profiles import REFERENCES, policy, load_reference
from app.skill_result_assets import ResultAssetStore
from app.skill_store import SkillStore
from app.skill_validation_jobs import ValidationJobs
from scripts.skill_library.authoring import migrate_with_backup

COMMIT = "600e50c8b4ac71f0bc344098320a1e30b80ade00"
MAT = {
    "B01": ("helmholtz_homog", "1c7d782d26981ff3f7592edd71470a4670cc4b8d7d733ebca67db56ba2d326ee"),
    "B02": ("helmholtz_heterog_c0", "5d94579003ef6a962c01ecbf5fab29e6c49516da7d578c4bfee89e219d66581a"),
    "B03": ("helmholtz_inteface_rho0", "4242217d375a7f2a59c10da89a25b85233018823df6402ec8a4bd44b794ade07"),
}


def convert_pinned_mat(case, raw):
    """Restricted reader for exactly these three SHA-pinned MATLAB5 files.

    This is not an upload/MAT parser. Verify compressed bytes before bounded
    expansion; accept only the single 128² complex-single p_final variable layout.
    Preserve Fortran order to reproduce the previously frozen complete NPY bytes.
    """
    if case not in MAT or hashlib.sha256(raw).hexdigest() != MAT[case][1]:
        raise ValueError("reference_mat_digest_mismatch")
    if len(raw) > 1_048_576 or raw[124:128] != b"\x00\x01IM":
        raise ValueError("unsupported_pinned_mat")
    kind, size = struct.unpack_from("<II", raw, 128)
    if kind != 15 or size != len(raw) - 136:
        raise ValueError("unsupported_pinned_mat")
    decoder = zlib.decompressobj()
    data = decoder.decompress(raw[136:], 131145)
    if len(data) != 131144 or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        raise ValueError("unsupported_pinned_mat")
    prefix = (struct.pack("<IIIIIIIIII", 14, 131136, 6, 8, 0x807, 0, 5, 8, 128, 128)
              + struct.pack("<II", 1, 7) + b"p_final\0" + struct.pack("<II", 7, 65536))
    if data[:64] != prefix or struct.unpack_from("<II", data, 65600) != (7, 65536):
        raise ValueError("unsupported_pinned_mat")
    real = np.frombuffer(data, dtype="<f4", count=16384, offset=64)
    imag = np.frombuffer(data, dtype="<f4", count=16384, offset=65608)
    field = (real + 1j * imag).reshape((128, 128), order="F")
    buffer = io.BytesIO(); np.save(buffer, field, allow_pickle=False)
    result = buffer.getvalue()
    if hashlib.sha256(result).hexdigest() != REFERENCES[case] or not np.isfinite(field).all() or not np.any(field):
        raise ValueError("reference_npy_digest_mismatch")
    return result


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError("reference_redirect_rejected")


def download_reference(case):
    if case not in MAT:
        raise ValueError("unsupported_reference")
    url = "https://raw.githubusercontent.com/ucl-bug/jwave-data/" + COMMIT + "/tests/kwave_data/" + MAT[case][0] + ".mat"
    request = urllib.request.Request(url, headers={"User-Agent": "AcouAgent-reference-preparation/1"})
    with urllib.request.build_opener(_NoRedirect()).open(request, timeout=15) as response:
        if response.status != 200:
            raise ValueError("reference_download_failed")
        raw = response.read(1_048_577)
    return convert_pinned_mat(case, raw)


def read_reviewed_reference(directory, case):
    path = Path(directory) / (case + ".npy")
    if path.is_symlink() or not path.is_file() or path.stat().st_size != 131200:
        raise ValueError("reference_npy_digest_mismatch")
    return path.read_bytes()


def runtime_identity():
    code, nonce = "print('acouagent-runtime-probe')\n", uuid.uuid4().hex
    result = execution._execute_code(code, 30, request_nonce=nonce)
    receipt = result.get("execution_evidence", {}).get("receipt")
    check = executor_evidence.verify_executor_receipt(receipt, result, code, 30, nonce)
    if not check["matched"] or result["exit_code"] != 0 or result["timed_out"]:
        raise ValueError("runtime_probe_failed")
    runtime = receipt["runtime"]
    source = Path(__file__).resolve().parents[2] / "executor" / "executor.py"
    # Source checkout and gateway image both carry the reviewed executor source.
    if not source.is_file():
        raise ValueError("reviewed_executor_source_missing")
    if (runtime["executor_source_sha256"] != hashlib.sha256(source.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
            or runtime["library_versions"] != policy("B01", receipt["runtime_sha256"])["library_versions"]):
        raise ValueError("unapproved_executor_runtime")
    return receipt["runtime_sha256"]


def backup_existing(path, destination):
    path = Path(path).resolve()
    if not path.is_file():
        return False
    deadline = time.monotonic() + 15
    def progress(_status, _remaining, _total):
        if time.monotonic() > deadline:
            raise TimeoutError("backup_timeout")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)) as source:
        with closing(sqlite3.connect(destination)) as target:
            source.backup(target, pages=256, progress=progress, sleep=.05)
            if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("backup_integrity_failed")
    return True


def prepare(*, backup_dir, download=False, reference_directory=None):
    if not all((config.SKILL_AUTHORING_ENABLED, config.SKILL_VALIDATION_ENABLED, config.SKILL_GENERAL_ADMISSION_ENABLED)):
        raise ValueError("explicit_preparation_flags_required")
    from app.skill_library_service import STORE
    if not STORE.path.is_file():
        raise ValueError("existing_skill_database_required")
    if download == (reference_directory is not None):
        raise ValueError("choose_download_or_reviewed_directory")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex
    backup = Path(backup_dir) / stamp; backup.mkdir(parents=True, exist_ok=False)
    copied = []
    for path in (STORE.path, Path(dashboard.DB_PATH), STORE.path.parent / "error_cache.db"):
        if backup_existing(path, backup / path.name):
            copied.append(path.name)
    # Fetch and verify every independent asset before migration/profile writes.
    raw = {case: download_reference(case) if download else read_reviewed_reference(reference_directory, case)
           for case in MAT}
    if any(hashlib.sha256(value).hexdigest() != REFERENCES[case] for case, value in raw.items()):
        raise ValueError("reference_npy_digest_mismatch")
    runtime = runtime_identity()
    migrated = migrate_with_backup(STORE.path, backup)
    with tempfile.TemporaryDirectory(prefix="acouagent-references-") as temp:
        root = Path(temp)
        for case, value in raw.items():
            (root / (case + ".npy")).write_bytes(value)
            load_reference(case, root / (case + ".npy"))
        library = GeneralLibrary(STORE, ResultAssetStore(STORE, config.SKILL_GENERAL_ASSET_ROOT), root, runtime)
        installed = [library.install_profile(case)["profile_id"] for case in MAT]
        ValidationJobs(STORE, runtime).install_trial_profile()
    return dict(prepared=True, runtime_sha256=runtime, schema=migrated["schema"], profiles=installed,
                backed_up=copied, skills_activated=0, learning_enabled_changed=False,
                next="Set SKILL_GENERAL_RUNTIME_SHA256 and desired feature flags in .env, then recreate the gateway")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-dir", type=Path, required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--download-references", action="store_true")
    group.add_argument("--reference-directory", type=Path)
    args = parser.parse_args()
    try:
        result = prepare(backup_dir=args.backup_dir, download=args.download_references, reference_directory=args.reference_directory)
        print(json.dumps(result)); return 0
    except (ValueError, OSError, sqlite3.Error, KeyError, TypeError, TimeoutError):
        print(json.dumps({"prepared": False, "error": "general_preparation_failed"})); return 1


if __name__ == "__main__":
    raise SystemExit(main())
