"""General jobs reuse lease/cancellation semantics without trusting worker PASS."""
import json
from pathlib import Path
import sqlite3
import sys
from types import ModuleType
from unittest.mock import Mock

import pytest

from app import config
from app.skill_drafts import DraftStore
from app.skill_general_validation import GeneralValidationJobs, GeneralValidationWorker
from app.skill_store import SkillStore
from app.skill_validation_worker import ValidationWorker


@pytest.fixture
def env(tmp_path, monkeypatch):
    for flag in ('SKILL_AUTHORING_ENABLED', 'SKILL_VALIDATION_ENABLED', 'SKILL_GENERAL_ADMISSION_ENABLED'):
        monkeypatch.setattr(config, flag, True, raising=False)
    profiles = ModuleType('app.skill_general_profiles')
    profiles.policy = lambda case, runtime: {'profile_id': 'general-test', 'runtime_sha256': runtime}
    profiles.match_profile = lambda prepared: 'B01'
    profiles.build_controlled_spec = lambda raw: dict(json.loads(raw), code_template='print(1)')
    monkeypatch.setitem(sys.modules, 'app.skill_general_profiles', profiles)
    store = SkillStore(tmp_path / 'general.db')
    store.migrate(); store.migrate_skill_authoring()
    library = Mock()
    library.require_profile.side_effect = lambda db, case, runtime: db.execute(
        "INSERT OR IGNORE INTO skill_validation_profiles VALUES('general-test',1,'{}',?,'test',?,'enabled',0)",
        ('a' * 64, 'b' * 64))
    library.verify_proof.return_value = True
    jobs = GeneralValidationJobs(store, 'a' * 64, 'B01', library)
    path = Path(__file__).resolve().parents[2] / 'docs/skills-next/examples/manual-skill.json'
    spec = json.loads(path.read_text(encoding='utf-8'))
    spec['code_template'] = 'print(1)'
    drafts = DraftStore(store)
    return jobs, drafts, library, spec, profiles


def submit(env):
    jobs, drafts, _, spec, _ = env
    draft = drafts.create(json.dumps(spec), actor_id='alice', idempotency_key='draft')
    return jobs.enqueue(draft['draft_id'], actor_id='alice', expected_revision=draft['revision'], idempotency_key='submit')


def dispatch(env):
    submit(env)
    jobs = env[0]
    row = jobs.claim('worker')
    return jobs.mark_dispatching(row['job_id'], owner='worker', fence=row['fence'], expected_revision=row['revision'])


def finish(jobs, row, **kwargs):
    return jobs.finish(row['job_id'], owner='worker', fence=row['fence'], expected_revision=row['revision'],
                       receipt_sha256='b' * 64, manifest_sha256='c' * 64,
                       succeeded=True, reason='general_validation_complete', **kwargs)


def test_general_requires_separate_flag(env, monkeypatch):
    monkeypatch.setattr(config, 'SKILL_GENERAL_ADMISSION_ENABLED', False)
    with pytest.raises(ValueError, match='general_admission_disabled'):
        submit(env)


def test_noncontrolled_code_rejected_without_frozen_submission(env):
    env[3]['code_template'] = 'print("PASS")'
    with pytest.raises(ValueError, match='controlled_version_required'):
        submit(env)
    with sqlite3.connect(env[0].store.path) as db:
        assert db.execute('SELECT count(*) FROM skill_validation_jobs').fetchone()[0] == 0
        assert db.execute('SELECT state FROM skill_drafts').fetchone()[0] == 'editing'


def test_claim_rechecks_adapter_policy(env):
    submit(env)
    env[4].build_controlled_spec = lambda raw: dict(json.loads(raw), code_template='print(2)')
    with pytest.raises(ValueError, match='controlled_version_required'):
        env[0].claim('worker')


@pytest.mark.parametrize('passed,state', [(True, 'validated'), (False, 'rejected')])
def test_worker_success_cannot_replace_independent_persisted_proof(env, passed, state):
    row = dispatch(env)
    env[2].verify_proof.return_value = passed
    result = finish(env[0], row)
    assert result['state'] == state
    assert env[2].verify_proof.call_count == 1
    with sqlite3.connect(env[0].store.path) as db:
        assert db.execute('SELECT count(*) FROM skill_validation_admissions').fetchone()[0] == 0


def test_cancel_wins_over_successful_comparison(env):
    row = dispatch(env)
    cancelled = env[0].cancel(row['job_id'], actor_id='alice', expected_revision=row['revision'])
    result = finish(env[0], cancelled)
    assert result['state'] == 'cancelled'
    env[2].verify_proof.assert_not_called()


def test_unknown_dispatch_reconciles_proof_without_retry(env):
    row = dispatch(env)
    unknown = env[0].mark_unknown(row['job_id'], owner='worker', fence=row['fence'], expected_revision=row['revision'])
    assert env[0].claim('other') is None
    result = env[0].reconcile(row['job_id'], expected_revision=unknown['revision'],
                             receipt_sha256='b' * 64, manifest_sha256='c' * 64,
                             succeeded=True, reason='general_validation_complete')
    assert result['state'] == 'validated'
    assert result['attempt_no'] == 1


def test_worker_binds_history_before_independent_comparison(env, monkeypatch):
    library = env[2]
    worker = GeneralValidationWorker(env[0], Mock(), library)
    monkeypatch.setattr(ValidationWorker, '_bind', Mock(side_effect=ValueError('receipt mismatch')))
    with pytest.raises(ValueError, match='receipt mismatch'):
        worker._bind({})
    library.validate_result.assert_not_called()


def test_failed_executor_never_calls_reference_comparator(env, monkeypatch):
    bound = dict(succeeded=False, reason='executor_failed', receipt_sha256='b' * 64, manifest_sha256=None)
    monkeypatch.setattr(ValidationWorker, '_bind', lambda self, job: bound)
    worker = GeneralValidationWorker(env[0], Mock(), env[2])
    assert worker._bind({}) == bound
    env[2].validate_result.assert_not_called()


def test_missing_persisted_proof_cannot_finish_even_with_success_flag(env):
    row = dispatch(env)
    env[2].verify_proof.side_effect = ValueError('proof_not_found')
    with pytest.raises(ValueError, match='proof_not_found'):
        finish(env[0], row)
    assert env[0].worker_get(row['job_id'])['state'] == 'running'


def test_profile_revoked_after_dispatch_blocks_completion(env):
    row = dispatch(env)
    env[2].require_profile.side_effect = ValueError('profile_not_available')
    with pytest.raises(ValueError, match='profile_not_available'):
        finish(env[0], row)
    env[2].verify_proof.assert_not_called()


def test_trial_intent_is_rejected_by_general_jobs(env):
    jobs, drafts, _, spec, _ = env
    draft = drafts.create(json.dumps(spec), actor_id='alice', idempotency_key='draft')
    with pytest.raises(ValueError, match='invalid_general_intent'):
        jobs.enqueue(draft['draft_id'], actor_id='alice', expected_revision=draft['revision'],
                     idempotency_key='submit', intent='trial')
