import hashlib
import json

import pytest
from scripts.validation.runtime_approval_contract import VERSION, check_package


def package(tmp_path):
    artifact = tmp_path / "receipt.json"
    artifact.write_text("real evidence fixture")
    data = dict(schema_version=VERSION, approval_id="test-only", status="candidate",
                scope="isolated_validation", runtime_sha256="a" * 64,
                image_id="sha256:" + "b" * 64, recipe_sha256="c" * 64,
                validator_sha256="d" * 64,
                evidence=[dict(path=artifact.name, sha256=hashlib.sha256(artifact.read_bytes()).hexdigest())])
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(data))
    return manifest, data


@pytest.mark.parametrize("status", ["candidate", "approved", "retired"])
def test_package_never_grants_approval(tmp_path, status):
    manifest, data = package(tmp_path)
    data["status"] = status
    manifest.write_text(json.dumps(data))
    result = check_package(manifest, tmp_path)
    assert result["package_valid"]
    assert not result["runtime_approved"] and not result["trusted_run"]


@pytest.mark.parametrize("mutation", ["hash", "escape", "missing", "duplicate", "bool", "extra", "empty", "image"])
def test_bad_packages_fail_closed(tmp_path, mutation):
    manifest, data = package(tmp_path)
    if mutation == "hash": data["evidence"][0]["sha256"] = "f" * 64
    if mutation == "escape": data["evidence"][0]["path"] = "../receipt.json"
    if mutation == "missing": data["evidence"][0]["path"] = "missing.json"
    if mutation == "duplicate": data["evidence"] *= 2
    if mutation == "bool": data["runtime_sha256"] = True
    if mutation == "extra": data["runtime_approved"] = True
    if mutation == "empty": data["evidence"] = []
    if mutation == "image": data["image_id"] = "latest"
    manifest.write_text(json.dumps(data))
    assert not check_package(manifest, tmp_path)["package_valid"]


def test_duplicate_json_key_rejected(tmp_path):
    manifest, _ = package(tmp_path)
    manifest.write_text('{"status":"candidate","status":"approved"}')
    assert not check_package(manifest, tmp_path)["package_valid"]


@pytest.mark.parametrize("kind", ["file_symlink", "parent_symlink", "manifest_symlink", "fifo", "oversize_manifest", "alias"])
def test_filesystem_boundaries(tmp_path, kind):
    manifest, data = package(tmp_path)
    if kind == "file_symlink":
        (tmp_path / "alias.json").symlink_to(tmp_path / "receipt.json")
        data["evidence"][0]["path"] = "alias.json"
    elif kind == "parent_symlink":
        (tmp_path / "alias").symlink_to(tmp_path, target_is_directory=True)
        data["evidence"][0]["path"] = "alias/receipt.json"
    elif kind == "fifo":
        import os
        os.mkfifo(tmp_path / "fifo")
        data["evidence"][0]["path"] = "fifo"
    elif kind == "alias":
        data["evidence"].append(dict(data["evidence"][0], path="./receipt.json"))
    manifest.write_text(json.dumps(data))
    if kind == "manifest_symlink":
        link = tmp_path / "manifest-link.json"
        link.symlink_to(manifest)
        manifest = link
    elif kind == "oversize_manifest":
        manifest.write_bytes(b" " * 16385)
    assert not check_package(manifest, tmp_path)["package_valid"]


def test_nested_regular_evidence(tmp_path):
    manifest, data = package(tmp_path)
    folder = tmp_path / "nested"
    folder.mkdir()
    (tmp_path / "receipt.json").rename(folder / "receipt.json")
    data["evidence"][0]["path"] = "nested/receipt.json"
    manifest.write_text(json.dumps(data))
    assert check_package(manifest, tmp_path)["package_valid"]
