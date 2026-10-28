"""Convert fixed, host-downloaded MAT bytes using the original reviewed parser.

Only the gateway receives reference arrays. The executor receives just the
existing signed runtime probe; no reference files are mounted into it.
"""
import json
from pathlib import Path
import tempfile
from scripts.skill_library.prepare_general import MAT, convert_pinned_mat, prepare

with tempfile.TemporaryDirectory(prefix="reviewed-references-") as directory:
    target = Path(directory)
    for case in MAT:
        source = Path("/references") / (case + ".mat")
        if source.is_symlink() or not source.is_file() or source.stat().st_size > 1048576:
            raise ValueError("reference_input_invalid")
        (target / (case + ".npy")).write_bytes(convert_pinned_mat(case, source.read_bytes()))
    print(json.dumps(prepare(backup_dir="/app/data/skill-backups", reference_directory=target)))
