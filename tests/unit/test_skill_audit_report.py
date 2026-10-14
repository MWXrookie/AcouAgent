import json
import sqlite3
import sys

from app.skill_store import SkillStore
from scripts.skill_library import audit_report


def _candidate(store: SkillStore) -> tuple[str, str, str]:
    code = "print('audited skill')"
    source_run = "run-private-source-123"
    skill_id = store.create_candidate(
        scenario_type="point_source_2d",
        state_text="2D point source",
        normalized_params={"frequency_hz": 500000, "dx_m": 0.00025},
        code_template=code,
        postconditions={"has_signal": True},
        physics_evidence={"check_id": "numeric_valid", "passed": True},
        source_run_id=source_run,
        validator_version="physics_gate.v1",
        quality_level="Q2",
    )
    return skill_id, source_run, code


def test_report_is_sanitized_reproducible_and_structurally_healthy(tmp_path):
    store = SkillStore(tmp_path / "skills.db")
    skill_id, source_run, code = _candidate(store)

    report = audit_report.build_report(store.path)
    encoded = json.dumps(report, ensure_ascii=False)

    assert report["healthy"] is True
    assert report["trust_verified"] is False
    assert report["summary"]["status_counts"] == {"candidate": 1}
    assert report["summary"]["quality_counts"] == {"Q2": 1}
    assert report["skills"][0]["skill_id"] == skill_id
    assert report["skills"][0]["code_hash_verified"] is True
    assert report["skills"][0]["source_run_sha256"]
    assert source_run not in encoded
    assert code not in encoded
    assert "physics_evidence" not in encoded
    assert audit_report.verify_report(report) is True


def test_report_detects_code_hash_tampering(tmp_path):
    store = SkillStore(tmp_path / "skills.db")
    _candidate(store)
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE skill_versions SET code_template='print(\"changed\")'")

    report = audit_report.build_report(store.path)

    assert report["healthy"] is False
    assert report["checks"]["code_hash_failures"] == 1
    assert report["skills"][0]["code_hash_verified"] is False


def test_report_digest_rejects_modified_summary(tmp_path):
    store = SkillStore(tmp_path / "skills.db")
    _candidate(store)
    report = audit_report.build_report(store.path)
    report["summary"]["total_feedback"] = 99

    assert audit_report.verify_report(report) is False


def test_markdown_contains_inventory_but_not_raw_source(tmp_path):
    store = SkillStore(tmp_path / "skills.db")
    skill_id, source_run, _ = _candidate(store)
    markdown = audit_report.render_markdown(audit_report.build_report(store.path))

    assert skill_id in markdown
    assert source_run not in markdown
    assert "不证明模型采用" in markdown


def test_report_hides_unsafe_legacy_metadata(tmp_path):
    store = SkillStore(tmp_path / "skills.db")
    _candidate(store)
    unsafe = "Bearer private-value-must-not-leak"
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE skill_versions SET validator_version=?", (unsafe,))

    report = audit_report.build_report(store.path)
    encoded = json.dumps(report, ensure_ascii=False)

    assert report["healthy"] is False
    assert report["checks"]["unsafe_metadata_rows"] == 1
    assert report["skills"][0]["validator_version"] is None
    assert unsafe not in encoded


def test_cli_writes_and_verifies_both_reports(tmp_path, monkeypatch):
    store = SkillStore(tmp_path / "skills.db")
    _candidate(store)
    json_path = tmp_path / "audit.json"
    markdown_path = tmp_path / "audit.md"
    monkeypatch.setattr(sys, "argv", ["audit_report", "--db", str(store.path),
        "--json", str(json_path), "--markdown", str(markdown_path)])

    assert audit_report.main() == 0
    assert json_path.is_file() and markdown_path.is_file()
    monkeypatch.setattr(sys, "argv", ["audit_report", "--verify", str(json_path)])
    assert audit_report.main() == 0
