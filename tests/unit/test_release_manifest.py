import json
import shutil
from pathlib import Path

import pytest

from scripts.validation import release_manifest


def test_parse_tree_and_forbidden_paths():
    raw = (
        b"100644 blob " + b"a" * 40 + b"\tREADME.md\0"
        b"100755 blob " + b"b" * 40 + b"\tscripts/install.sh\0"
    )
    entries = release_manifest._parse_tree(raw)
    assert entries[0]["path"] == "README.md"
    assert entries[1]["mode"] == "100755"
    assert release_manifest._is_forbidden("nested/__pycache__/module.pyc")
    assert release_manifest._is_forbidden("private/.env")
    assert release_manifest._is_forbidden("evidence/data.sqlite")
    assert not release_manifest._is_forbidden(".env.example")


def test_manifest_digest_detects_tampering():
    manifest = {"schema_version": 1, "release_files_ready": True}
    manifest["manifest_sha256"] = release_manifest._digest_without_field(manifest)
    assert release_manifest.verify_manifest(manifest)
    manifest["release_files_ready"] = False
    assert not release_manifest.verify_manifest(manifest)


def test_current_head_manifest_is_reproducible_and_safe():
    if shutil.which("git") is None:
        pytest.skip("git executable is covered by the separate release-manifest workflow job")
    root = Path(__file__).resolve().parents[2]
    first = release_manifest.build_manifest(root)
    second = release_manifest.build_manifest(root)
    assert first == second
    assert first["integrity"]["forbidden_tracked_paths"] == []
    assert set(first["integrity"]["missing_required_files"]) <= {
        "app/model_preflight.py",
        "docs/CURRENT_STATUS.md",
        "docs/releases/v1.0.0-draft.md",
        "docs/validation/stage7/RELEASE_CHECKLIST.md",
        "scripts/diagnostics/acouagent_doctor.py",
        "scripts/validation/release_manifest.py",
    }
    assert first["source"]["commit_sha"]
    assert first["project"] == {"name": "dify-mcp-server", "version": "0.1.0"}
    assert release_manifest.verify_manifest(first)


def test_verify_cli_accepts_intact_file_and_rejects_modified_file(tmp_path, monkeypatch):
    manifest = {"schema_version": 1, "value": "original"}
    manifest["manifest_sha256"] = release_manifest._digest_without_field(manifest)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["release_manifest", "--verify", str(path)])
    assert release_manifest.main() == 0
    manifest["value"] = "changed"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["release_manifest", "--verify", str(path)])
    assert release_manifest.main() == 1


def test_release_workflow_generates_and_uploads_manifest():
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github" / "workflows" / "release-images.yml").read_text(
        encoding="utf-8"
    )
    assert "scripts/validation/release_manifest.py" in workflow
    assert "--verify release-manifest.json" in workflow
    assert "actions/upload-artifact@v4" in workflow
    assert "release-manifest-${{ github.sha }}" in workflow
    assert "Full unit regression" in workflow
    assert "python -m pytest -q -p no:cacheprovider" in workflow
    assert "--network none" in workflow
    assert "--read-only" in workflow
    assert "--cap-drop ALL" in workflow
