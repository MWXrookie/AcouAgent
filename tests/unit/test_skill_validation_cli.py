"""Operator commands preserve explicit opt-in and do not dump draft code."""
import json
from pathlib import Path
import sys

from app import config
from app.skill_drafts import DraftStore
from app.skill_store import SkillStore
from app.skill_validation_jobs import ValidationJobs
from scripts.skill_library import validation


def args(monkeypatch, tmp_path, command, *extra):
    monkeypatch.setattr(sys, "argv", ["validation", command, "--database", str(tmp_path / "skills.db"),
                        "--assets", str(tmp_path / "assets"), "--runtime-sha256", "a" * 64, *extra])


def test_disabled_cli_never_initializes_or_migrates(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "SKILL_AUTHORING_ENABLED", False)
    args(monkeypatch, tmp_path, "install-profile")
    assert validation.main() == 1
    assert json.loads(capsys.readouterr().out) == {"error": "trial_operation_failed"}
    assert not (tmp_path / "skills.db").exists()
    assert not (tmp_path / "assets").exists()


def test_status_excludes_code_and_cancel_requires_revision(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "SKILL_AUTHORING_ENABLED", True)
    monkeypatch.setattr(config, "SKILL_VALIDATION_ENABLED", True)
    store = SkillStore(tmp_path / "skills.db")
    store.migrate_skill_authoring()
    jobs = ValidationJobs(store, "a" * 64)
    jobs.install_trial_profile()
    raw = (Path(__file__).resolve().parents[2] / "docs/skills-next/examples/manual-skill.json").read_text(encoding="utf-8")
    draft = DraftStore(store).create(raw, actor_id="internal-operator", idempotency_key="draft")
    job = jobs.enqueue(draft["draft_id"], actor_id="internal-operator", expected_revision=1, idempotency_key="job")
    args(monkeypatch, tmp_path, "status", "--job-id", job["job_id"])
    assert validation.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "queued" and "spec" not in result
    args(monkeypatch, tmp_path, "cancel", "--job-id", job["job_id"], "--expected-revision", "1")
    assert validation.main() == 0
    assert json.loads(capsys.readouterr().out)["state"] == "cancelled"
