import os
import shutil
import subprocess
from pathlib import Path

import pytest

from app import config, dashboard
from app import skill_local_profile as local
from scripts.skill_library import local_basic


def test_bootstrap_runs_every_reviewed_profile_once(monkeypatch):
    calls=[]
    monkeypatch.setattr(config,"SKILL_BOOTSTRAP_ALL_FIXED",True)
    monkeypatch.setattr(config,"SKILL_LIBRARY_ENABLED",True)
    monkeypatch.setattr(config,"SKILL_RETRIEVAL_ENABLED",True)
    monkeypatch.setattr(config,"SKILL_MANUAL_INTAKE_ENABLED",True)
    monkeypatch.setattr(dashboard,"init_db",lambda: calls.append("history"))
    monkeypatch.setattr(local_basic,"initialize",lambda:{"ready":True,"created":True})
    monkeypatch.setattr(local.catalog,"SCOPES",("one","two","three"))
    monkeypatch.setattr(local_basic,"seed",lambda profile:(calls.append(profile) or {
        "ready":True,"executed":True,"reason":"accepted",
        "admission":{"skill_id":"skill-"+profile,"quality_level":"Q4"}}))
    result=local_basic.bootstrap()
    assert result["ready"] and result["profile_count"]==3 and result["executed_count"]==3
    assert calls==["history","one","two","three"]
    assert all(len(item["attempts"])==1 for item in result["profiles"])
    assert all(item["quality_level"]=="Q4" for item in result["profiles"])


def test_bootstrap_disabled_is_clean_noop(monkeypatch):
    monkeypatch.setattr(config,"SKILL_BOOTSTRAP_ALL_FIXED",False)
    monkeypatch.setattr(local_basic,"initialize",lambda:(_ for _ in ()).throw(AssertionError("must not initialize")))
    result=local_basic.bootstrap()
    assert result=={"ready":True,"skipped":True,"profile_count":0,
                    "reason":"fixed_profile_bootstrap_disabled"}


def test_bootstrap_stops_on_first_failed_profile(monkeypatch):
    monkeypatch.setattr(config,"SKILL_BOOTSTRAP_ALL_FIXED",True)
    monkeypatch.setattr(config,"SKILL_LIBRARY_ENABLED",True)
    monkeypatch.setattr(config,"SKILL_RETRIEVAL_ENABLED",True)
    monkeypatch.setattr(config,"SKILL_MANUAL_INTAKE_ENABLED",True)
    monkeypatch.setattr(dashboard,"init_db",lambda:None)
    monkeypatch.setattr(local_basic,"initialize",lambda:{"ready":True})
    monkeypatch.setattr(local.catalog,"SCOPES",("one","two","three"))
    calls=[]
    def fake_seed(profile):
        calls.append(profile)
        return {"ready":profile!="two","executed":True,"reason":"failed" if profile=="two" else "ok"}
    monkeypatch.setattr(local_basic,"seed",fake_seed)
    result=local_basic.bootstrap()
    assert not result["ready"] and result["failed_profile"]=="two"
    assert calls==["one","two","two","two"]
    assert len(result["profiles"][-1]["attempts"])==3


def test_bootstrap_retries_transient_executor_failure(monkeypatch):
    monkeypatch.setattr(config,"SKILL_BOOTSTRAP_ALL_FIXED",True)
    monkeypatch.setattr(config,"SKILL_LIBRARY_ENABLED",True)
    monkeypatch.setattr(config,"SKILL_RETRIEVAL_ENABLED",True)
    monkeypatch.setattr(config,"SKILL_MANUAL_INTAKE_ENABLED",True)
    monkeypatch.setattr(dashboard,"init_db",lambda:None)
    monkeypatch.setattr(local_basic,"initialize",lambda:{"ready":True})
    monkeypatch.setattr(local.catalog,"SCOPES",("one",))
    outcomes=iter((
        {"ready":False,"executed":True,"reason":"local_execution_failed"},
        {"ready":True,"executed":True,"reason":"local_fixed_profile_accepted",
         "admission":{"skill_id":"skill-one","quality_level":"Q4"}},
    ))
    monkeypatch.setattr(local_basic,"seed",lambda profile:next(outcomes))
    result=local_basic.bootstrap()
    assert result["ready"] and len(result["profiles"][0]["attempts"])==2


def test_status_all_exit_code_fails_when_catalog_is_incomplete(monkeypatch):
    monkeypatch.setattr(local_basic,"status_all",lambda:{"ready":False,"ready_count":16})
    monkeypatch.setattr("sys.argv",["local_basic","status-all"])
    assert local_basic.main()==1


def test_release_compose_keeps_gateway_reachable_and_executor_internal_only():
    compose_path = Path(__file__).resolve().parents[2] / "compose.yaml"
    compose = compose_path.read_text(encoding="utf-8")
    executor = compose.split("  jwave-executor:\n", 1)[1].split("  dify-mcp:\n", 1)[0]
    gateway = compose.split("  dify-mcp:\n", 1)[1].split("  skill-bootstrap:\n", 1)[0]
    networks = compose.split("\nnetworks:\n", 1)[1].split("\nvolumes:\n", 1)[0]

    assert "      - executor_internal" in executor
    assert "      - gateway_frontend" not in executor
    assert "      - gateway_frontend" in gateway
    assert "      - executor_internal" in gateway
    assert "  gateway_frontend:" in networks
    assert "  executor_internal:\n    internal: true" in networks


def test_release_compose_builds_gateway_from_checked_out_source():
    compose_path = Path(__file__).resolve().parents[2] / "compose.yaml"
    compose = compose_path.read_text(encoding="utf-8")
    gateway = compose.split("  dify-mcp:\n", 1)[1].split("  skill-bootstrap:\n", 1)[0]
    bootstrap = compose.split("  skill-bootstrap:\n", 1)[1].split("\nnetworks:\n", 1)[0]

    assert "x-gateway-build: &gateway-build" in compose
    assert "    build: *gateway-build" in gateway
    assert "    build: *gateway-build" in bootstrap
    assert "local/acouagent-gateway:source" in gateway
    assert "feat-acouagent-evidence-gates-20261007" not in gateway


def test_release_compose_pins_executor_image_with_matching_source_identity():
    root = Path(__file__).resolve().parents[2]
    compose = (root / "compose.yaml").read_text(encoding="utf-8")
    env_example = (root / ".env.example").read_text(encoding="utf-8")
    expected = "ghcr.io/mwxrookie/acouagent-executor:f65addf"

    assert expected in compose
    assert f"ACOUAGENT_EXECUTOR_IMAGE={expected}" in env_example
    assert "feat-acouagent-evidence-gates-20261007" not in compose
    assert "feat-acouagent-evidence-gates-20261007" not in env_example


def test_clean_install_workflow_checks_source_skills_and_cleanup():
    workflow_path = (
        Path(__file__).resolve().parents[2]
        / ".github"
        / "workflows"
        / "clean-install-smoke.yml"
    )
    workflow = workflow_path.read_text(encoding="utf-8")

    assert "bash scripts/install.sh --prepare-only" in workflow
    assert "bash scripts/install.sh" in workflow
    assert "scripts.skill_library.local_basic status-all" in workflow
    assert "scripts.skill_library.audit_report" in workflow
    assert "skill-library-audit.json" in workflow
    assert "app/local_workflow.py" in workflow
    assert 'assert status["ready_count"] == 17' in workflow
    assert 'assert network["Internal"] is True' in workflow
    assert "docker compose down -v --remove-orphans" in workflow
    assert "if: always()" in workflow


def test_posix_installer_prepares_tokens_without_overwriting(tmp_path):
    if os.name == "nt":
        return
    bash = shutil.which("bash")
    if bash is None:
        return
    root = Path(__file__).resolve().parents[2]
    (tmp_path / "scripts").mkdir()
    shutil.copy2(root / "scripts" / "install.sh", tmp_path / "scripts" / "install.sh")
    shutil.copy2(root / ".env.example", tmp_path / ".env.example")

    command = [bash, str(tmp_path / "scripts" / "install.sh"), "--prepare-only"]
    first = subprocess.run(command, cwd=tmp_path, text=True, capture_output=True, check=False)
    assert first.returncode == 0, first.stderr
    env_path = tmp_path / ".env"
    initial = env_path.read_text(encoding="utf-8")
    assert "replace-with-at-least-32-random-characters" not in initial
    assert "replace-with-a-different-32-character-secret" not in initial
    values = dict(
        line.split("=", 1)
        for line in initial.splitlines()
        if line.startswith(("MCP_AUTH_TOKEN=", "EXECUTOR_SHARED_TOKEN="))
    )
    assert len(values["MCP_AUTH_TOKEN"]) >= 32
    assert len(values["EXECUTOR_SHARED_TOKEN"]) >= 32
    assert values["MCP_AUTH_TOKEN"] != values["EXECUTOR_SHARED_TOKEN"]
    if os.name != "nt":
        assert env_path.stat().st_mode & 0o077 == 0

    second = subprocess.run(command, cwd=tmp_path, text=True, capture_output=True, check=False)
    assert second.returncode == 0, second.stderr
    assert env_path.read_text(encoding="utf-8") == initial


@pytest.mark.parametrize("obsolete_tag", [
    "feat-acouagent-evidence-gates-20261007",
    "c77bc27",
])
def test_posix_installer_migrates_only_obsolete_executor_default(tmp_path, obsolete_tag):
    if os.name == "nt":
        return
    bash = shutil.which("bash")
    if bash is None:
        return
    root = Path(__file__).resolve().parents[2]
    (tmp_path / "scripts").mkdir()
    shutil.copy2(root / "scripts" / "install.sh", tmp_path / "scripts" / "install.sh")
    content = (root / ".env.example").read_text(encoding="utf-8")
    content = content.replace("replace-with-at-least-32-random-characters", "a" * 64)
    content = content.replace("replace-with-a-different-32-character-secret", "b" * 64)
    content = content.replace("acouagent-executor:f65addf", f"acouagent-executor:{obsolete_tag}")
    (tmp_path / ".env").write_bytes(content.replace("\n", "\r\n").encode("utf-8"))

    result = subprocess.run(
        [bash, str(tmp_path / "scripts" / "install.sh"), "--prepare-only"],
        cwd=tmp_path, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    migrated = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "acouagent-executor:f65addf" in migrated
    assert f"acouagent-executor:{obsolete_tag}" not in migrated
    assert "a" * 64 in migrated and "b" * 64 in migrated
    backups = list(tmp_path.glob(".env.bak.executor-image-*"))
    assert len(backups) == 1
    assert f"acouagent-executor:{obsolete_tag}" in backups[0].read_text(encoding="utf-8")


def test_installers_do_not_print_or_overwrite_model_credentials():
    root = Path(__file__).resolve().parents[2]
    shell = (root / "scripts" / "install.sh").read_text(encoding="utf-8")
    powershell = (root / "scripts" / "install.ps1").read_text(encoding="utf-8")

    assert "set -x" not in shell
    assert "MODEL_API_KEY}" not in shell
    assert "Write-Output $" not in powershell
    assert "using existing .env and preserving custom values" in shell
    assert "using existing .env and preserving custom values" in powershell


def test_powershell_installer_prepares_tokens_without_overwriting(tmp_path):
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if powershell is None:
        return
    root = Path(__file__).resolve().parents[2]
    (tmp_path / "scripts").mkdir()
    shutil.copy2(root / "scripts" / "install.ps1", tmp_path / "scripts" / "install.ps1")
    shutil.copy2(root / ".env.example", tmp_path / ".env.example")

    command = [powershell, "-NoProfile", "-File",
               str(tmp_path / "scripts" / "install.ps1"),
               "-PrepareOnly"]
    first = subprocess.run(command, cwd=tmp_path, text=True, encoding="utf-8",
                           errors="replace", capture_output=True, check=False)
    assert first.returncode == 0, first.stderr
    env_path = tmp_path / ".env"
    initial = env_path.read_text(encoding="utf-8")
    assert "replace-with-at-least-32-random-characters" not in initial
    assert "replace-with-a-different-32-character-secret" not in initial

    second = subprocess.run(command, cwd=tmp_path, text=True, encoding="utf-8",
                            errors="replace", capture_output=True, check=False)
    assert second.returncode == 0, second.stderr
    assert env_path.read_text(encoding="utf-8") == initial


@pytest.mark.parametrize("obsolete_tag", [
    "feat-acouagent-evidence-gates-20261007",
    "c77bc27",
])
def test_powershell_installer_migrates_only_obsolete_executor_default(tmp_path, obsolete_tag):
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if powershell is None:
        return
    root = Path(__file__).resolve().parents[2]
    (tmp_path / "scripts").mkdir()
    shutil.copy2(root / "scripts" / "install.ps1", tmp_path / "scripts" / "install.ps1")
    content = (root / ".env.example").read_text(encoding="utf-8")
    content = content.replace("replace-with-at-least-32-random-characters", "a" * 64)
    content = content.replace("replace-with-a-different-32-character-secret", "b" * 64)
    content = content.replace("acouagent-executor:f65addf", f"acouagent-executor:{obsolete_tag}")
    (tmp_path / ".env").write_text(content, encoding="utf-8")

    result = subprocess.run(
        [powershell, "-NoProfile", "-File", str(tmp_path / "scripts" / "install.ps1"), "-PrepareOnly"],
        cwd=tmp_path, text=True, encoding="utf-8", errors="replace",
        capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    migrated = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "acouagent-executor:f65addf" in migrated
    assert f"acouagent-executor:{obsolete_tag}" not in migrated
    assert "a" * 64 in migrated and "b" * 64 in migrated
    backups = list(tmp_path.glob(".env.bak.executor-image-*"))
    assert len(backups) == 1
