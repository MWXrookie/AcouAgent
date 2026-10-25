"""Controlled first-execution provenance using disposable synthetic data only."""
import base64
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import uuid
import zlib

import numpy as np
import pytest

from app import config, dashboard, execution, skill_auto_source as source
from app import skill_general_library as general, skill_general_profiles as profiles
from app.skill_drafts import DraftStore
from app.skill_general_validation import GeneralValidationJobs, GeneralValidationWorker
from app.skill_result_assets import ResultAssetStore
from app.skill_store import SkillStore
from app.skill_validation_jobs import ValidationJobs, canonical, digest
from app.skill_validation_worker import ValidationWorker


@pytest.fixture
def env(tmp_path, monkeypatch):
    for flag in ('SKILL_LEARNING_ENABLED', 'SKILL_AUTHORING_ENABLED', 'SKILL_VALIDATION_ENABLED',
                 'SKILL_GENERAL_ADMISSION_ENABLED', 'SKILL_LIBRARY_ENABLED', 'SKILL_RETRIEVAL_ENABLED',
                 'SKILL_INJECTION_ENABLED'):
        monkeypatch.setattr(config, flag, True)
    store = SkillStore(tmp_path / 'skills.db'); store.migrate_skill_authoring()
    assets = ResultAssetStore(store, tmp_path / 'assets')
    refs = tmp_path / 'references'; refs.mkdir()
    reference = np.ones((128, 128), dtype=np.complex64) * (2 + 3j)
    buffer = io.BytesIO(); np.save(buffer, reference, allow_pickle=False)
    raw = buffer.getvalue(); (refs / 'B01.npy').write_bytes(raw)
    monkeypatch.setitem(profiles.REFERENCES, 'B01', hashlib.sha256(raw).hexdigest())
    executor_path = Path(__file__).resolve().parents[2] / 'executor/executor.py'
    runtime = dict(executor_source_sha256=hashlib.sha256(executor_path.read_bytes().replace(b'\r\n', b'\n')).hexdigest(),
                   python_version='3.12.9', python_implementation='CPython', machine='x86_64',
                   library_versions=profiles.policy('B01', 'a' * 64)['library_versions'])
    library = general.GeneralLibrary(store, assets, refs, digest(canonical(runtime)))
    library.install_profile('B01')
    monkeypatch.setattr(source, 'get_library', lambda: library)
    monkeypatch.setattr(general, 'get_library', lambda store=None: library)
    monkeypatch.setattr(dashboard, 'DB_PATH', str(tmp_path / 'history.db')); dashboard.init_db()
    value = dict(field=reference.astype(np.complex128), calls=[])
    def dispatch(code, timeout, *, request_nonce):
        value['calls'].append(dict(code=code, timeout=timeout, nonce=request_nonce))
        buffer = io.BytesIO(); np.save(buffer, value['field'], allow_pickle=False)
        stdout = '__SKILL_PROBE_START__' + json.dumps(dict(quantity='complex_pressure',
            npy_zlib_base64=base64.b64encode(zlib.compress(buffer.getvalue())).decode('ascii'))) + '__SKILL_PROBE_END__'
        receipt = dict(version='executor-receipt.v1', request_nonce=request_nonce, code_sha256=digest(code),
                       stdout_sha256=digest(stdout), stderr_sha256=digest(''), exit_code=0, timed_out=False,
                       timeout_seconds=timeout, runtime=runtime, runtime_sha256=digest(canonical(runtime)))
        dispatch = dict(version='local-dispatch.v1', source_kind='managed_executor_transport',
                        request_sha256=digest(canonical(dict(code=code, timeout_seconds=timeout, request_nonce=request_nonce))),
                        code_sha256=digest(code), request_nonce=request_nonce, timeout_seconds=timeout,
                        trust_boundary='gateway_and_internal_executor')
        evidence = dict(receipt=receipt, local_dispatch=dispatch, gateway_request_nonce=request_nonce,
                        gateway_timeout_seconds=timeout)
        return dict(stdout=stdout, stderr='', exit_code=0, timed_out=False, duration_ms=1,
                    execution_evidence=evidence)
    monkeypatch.setattr(execution, '_execute_code', dispatch)
    sample = Path(__file__).resolve().parents[2] / 'docs/skills-next/examples/manual-skill.json'
    return dict(store=store, assets=assets, library=library, spec=json.loads(sample.read_text(encoding='utf-8')),
                runtime=runtime, value=value, dispatch=dispatch)


def counts(env):
    with env['store']._read_db() as db:
        return {name: db.execute('SELECT count(*) FROM ' + name).fetchone()[0]
                for name in ('skill_drafts', 'skill_validation_jobs', 'skill_draft_requests',
                             'skill_validation_attempts', 'simulation_skills', 'skill_validation_admissions')}


def finish_source(env, key='source'):
    queued = source.start_source(env['spec'], request_key=key, library=env['library'])
    jobs = source._SourceJobs(env['library'], queued['job_id'])
    result = ValidationWorker(jobs, env['assets']).run_once('source-worker')
    assert result['reason_code'] == 'trial_completed_ungraded'
    return result


def auto_validated(env):
    from app.skill_auto_learning import AutoLearning
    first = finish_source(env)
    run = first['attempt']['run_id']
    observed = AutoLearning(env['library']).observe(run)
    assert observed['queued'], observed
    jobs = GeneralValidationJobs(env['store'], env['library'].runtime_sha256, 'B01', env['library'])
    result = GeneralValidationWorker(jobs, env['assets'], env['library']).run_once('validation-worker')
    assert result['state'] == 'validated', result
    return first, result


def test_source_and_pre_dispatch_marker_commit_atomically(env, monkeypatch):
    def fail(*args):
        raise RuntimeError('marker construction interrupted')
    monkeypatch.setattr(source, '_binding', fail)
    with pytest.raises(RuntimeError):
        source.start_source(env['spec'], request_key='failure', library=env['library'])
    got = counts(env)
    assert got['skill_drafts'] == got['skill_validation_jobs'] == got['skill_draft_requests'] == 0
    assert not env['value']['calls']


def test_source_idempotency_and_server_marker_are_immutable(env):
    first = source.start_source(env['spec'], request_key='one', library=env['library'])
    duplicate = source.start_source(env['spec'], request_key='one', library=env['library'])
    assert first['job_id'] == duplicate['job_id']
    got = counts(env)
    assert got['skill_drafts'] == got['skill_validation_jobs'] == 1
    assert got['skill_draft_requests'] == 2 and got['skill_validation_attempts'] == 0
    changed = dict(env['spec'], name='different request content')
    with pytest.raises(ValueError):
        source.start_source(changed, request_key='one', library=env['library'])
    with env['store']._db_existing_write() as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE skill_draft_requests SET request_sha256=? WHERE operation=?", ('0' * 64, source.MARKER))


def test_valid_source_is_receipt_and_full_field_bound_not_a_skill(env):
    job = finish_source(env)
    checked = source.verify_source(job['attempt']['run_id'], library=env['library'])
    assert checked['source_job_id'] == job['job_id']
    assert checked['source_run_id'] == job['attempt']['run_id']
    assert checked['prepared']['code_sha256'] == job['attempt']['code_sha256']
    with env['store']._db_existing_write() as db:
        assert source.require_source_connection(db, checked['source_run_id']) is True
    assert counts(env)['simulation_skills'] == counts(env)['skill_validation_admissions'] == 0


def test_source_spec_is_immutable_and_cannot_be_rewritten_after_execution(env):
    job = finish_source(env)
    with env['store']._db_existing_write() as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE skill_draft_versions SET spec_json='{}' WHERE draft_id=?", (job['draft_id'],))
    assert source.verify_source(job['attempt']['run_id'], library=env['library'])['source_job_id'] == job['job_id']


def test_commit_guard_never_treats_a_cache_miss_as_validation(env):
    job = finish_source(env)
    with pytest.raises(ValueError, match='source_preflight_required'):
        with env['store']._db_existing_write() as db:
            source.require_source_connection(db, job['attempt']['run_id'])


def test_cancel_during_first_execution_cannot_be_observed_as_a_source(env, monkeypatch):
    queued = source.start_source(env['spec'], request_key='cancelled', library=env['library'])
    jobs = source._SourceJobs(env['library'], queued['job_id'])
    original = env['dispatch']
    def cancel_then_finish(code, timeout, *, request_nonce):
        current = jobs.get(queued['job_id'], actor_id=source.ACTOR)
        jobs.cancel(queued['job_id'], actor_id=source.ACTOR, expected_revision=current['revision'])
        return original(code, timeout, request_nonce=request_nonce)
    monkeypatch.setattr(execution, '_execute_code', cancel_then_finish)
    result = ValidationWorker(jobs, env['assets']).run_once('cancelled-source-worker')
    assert result['state'] in ('cancel_requested', 'cancelled', 'needs_reconciliation')
    with pytest.raises(ValueError):
        source.verify_source(result['attempt']['run_id'], library=env['library'])
    assert counts(env)['simulation_skills'] == counts(env)['skill_validation_admissions'] == 0


def test_an_ordinary_trial_with_identical_code_is_not_an_automatic_source(env):
    controlled = profiles.build_controlled_spec(canonical(env['spec']))
    draft = DraftStore(env['store']).create(canonical(controlled['spec']), actor_id='alice', idempotency_key='ordinary')
    jobs = ValidationJobs(env['store'], env['library'].runtime_sha256); jobs.install_trial_profile()
    jobs.enqueue(draft['draft_id'], actor_id='alice', expected_revision=1, idempotency_key='ordinary')
    finished = ValidationWorker(jobs, env['assets']).run_once('ordinary-worker')
    assert finished['reason_code'] == 'trial_completed_ungraded'
    with pytest.raises(ValueError):
        source.verify_source(finished['attempt']['run_id'], library=env['library'])


@pytest.mark.parametrize('field', ['code_sha256', 'request_sha256', 'parameters_sha256', 'runtime_sha256',
                                   'request_nonce', 'run_id', 'output_manifest_sha256'])
def test_attempt_binding_tamper_cannot_relabel_a_source(env, field):
    job = finish_source(env)
    run = job['attempt']['run_id']
    value = str(uuid.uuid4()) if field == 'run_id' else '1' * 32 if field == 'request_nonce' else '1' * 64
    with env['store']._db_existing_write() as db:
        db.execute('UPDATE skill_validation_attempts SET ' + field + '=? WHERE job_id=?', (value, job['job_id']))
    with pytest.raises(ValueError):
        source.verify_source(run, library=env['library'])


@pytest.mark.parametrize('field', ['stdout', 'nonce', 'runtime', 'duplicate_run'])
def test_history_mutations_cannot_mint_source_evidence(env, field):
    job = finish_source(env)
    run = job['attempt']['run_id']
    with sqlite3.connect(dashboard.DB_PATH) as db:
        if field == 'stdout':
            db.execute("UPDATE executions SET stdout='PASS'")
        elif field == 'duplicate_run':
            db.execute("INSERT INTO executions(timestamp,tool_name,code,run_id) SELECT timestamp,tool_name,code,run_id FROM executions WHERE run_id=?", (run,))
        else:
            evidence = json.loads(db.execute('SELECT executor_evidence_json FROM executions WHERE run_id=?', (run,)).fetchone()[0])
            if field == 'nonce':
                evidence['receipt']['request_nonce'] = '0' * 32
            else:
                evidence['receipt']['runtime']['library_versions']['jwave'] = 'different'
                evidence['receipt']['runtime_sha256'] = digest(canonical(evidence['receipt']['runtime']))
            db.execute('UPDATE executions SET executor_evidence_json=? WHERE run_id=?', (canonical(evidence), run))
    with pytest.raises(ValueError):
        source.verify_source(run, library=env['library'])


def test_zero_field_is_not_a_learnable_success(env):
    env['value']['field'][:] = 0
    job = finish_source(env)
    with pytest.raises(ValueError):
        source.verify_source(job['attempt']['run_id'], library=env['library'])


def test_archived_source_vetoes_cached_source_and_automatic_admission(env):
    first, learning_job = auto_validated(env)
    run = first['attempt']['run_id']
    source.verify_source(run, library=env['library'])
    drafts = DraftStore(env['store'])
    head = drafts.get(first['draft_id'], actor_id=source.ACTOR)
    drafts.archive(first['draft_id'], actor_id=source.ACTOR, expected_revision=head['revision'])
    with pytest.raises(ValueError):
        with env['store']._db_existing_write() as db:
            source.require_source_connection(db, run)
    with pytest.raises(ValueError):
        env['library'].admit(learning_job['job_id'], actor_id='internal-auto-learning',
                             expected_revision=learning_job['revision'], _automatic=True)
    assert counts(env)['simulation_skills'] == counts(env)['skill_validation_admissions'] == 0


@pytest.mark.parametrize('veto', ['source_archived', 'learning_disabled'])
def test_automatic_origin_cannot_use_manual_admit_to_skip_source_guard(env, monkeypatch, veto):
    first, learning_job = auto_validated(env)
    if veto == 'source_archived':
        drafts = DraftStore(env['store'])
        head = drafts.get(first['draft_id'], actor_id=source.ACTOR)
        drafts.archive(first['draft_id'], actor_id=source.ACTOR, expected_revision=head['revision'])
    else:
        monkeypatch.setattr(config, 'SKILL_LEARNING_ENABLED', False)
    # The job's persisted automatic origin must impose the guard even when
    # a caller forgets the private convenience flag (e.g. operator CLI).
    with pytest.raises(ValueError):
        env['library'].admit(learning_job['job_id'], actor_id='internal-auto-learning',
                             expected_revision=learning_job['revision'])
    assert counts(env)['simulation_skills'] == counts(env)['skill_validation_admissions'] == 0


def test_commit_guard_rehashes_assets_after_preflight(env):
    job = finish_source(env)
    checked = source.verify_source(job['attempt']['run_id'], library=env['library'])
    field = checked['manifest']['field_asset_sha256']
    env['assets']._path(field + '.npy').write_bytes(b'corrupt after verified')
    with pytest.raises(ValueError):
        with env['store']._db_existing_write() as db:
            source.require_source_connection(db, checked['source_run_id'])


@pytest.mark.parametrize('tamper', ['receipt_pointer', 'field_role'])
def test_commit_guard_rejects_late_binding_metadata_changes(env, tamper):
    job = finish_source(env)
    checked = source.verify_source(job['attempt']['run_id'], library=env['library'])
    with env['store']._db_existing_write() as db:
        if tamper == 'receipt_pointer':
            db.execute('UPDATE skill_validation_attempts SET receipt_sha256=? WHERE job_id=?', ('0' * 64, job['job_id']))
        else:
            db.execute("UPDATE skill_validation_assets SET role='evidence' WHERE asset_sha256=?", (checked['manifest']['field_asset_sha256'],))
    with pytest.raises(ValueError):
        with env['store']._db_existing_write() as db:
            source.require_source_connection(db, checked['source_run_id'])


def test_learning_disabled_at_commit_rolls_back_new_activation(env, monkeypatch):
    first, learning_job = auto_validated(env)
    source.verify_source(first['attempt']['run_id'], library=env['library'])
    original = env['store'].create_candidate
    def close_after_candidate(**kwargs):
        result = original(**kwargs)
        monkeypatch.setattr(config, 'SKILL_LEARNING_ENABLED', False)
        return result
    monkeypatch.setattr(env['store'], 'create_candidate', close_after_candidate)
    with pytest.raises(ValueError, match='learning_disabled'):
        env['library'].admit(learning_job['job_id'], actor_id='internal-auto-learning',
                             expected_revision=learning_job['revision'], _automatic=True)
    assert counts(env)['simulation_skills'] == counts(env)['skill_validation_admissions'] == 0


def test_targeted_source_claim_never_steals_another_queued_job(env, monkeypatch):
    clock = [source.time.time_ns()]
    monkeypatch.setattr(source.time, 'time_ns', lambda: clock[0])
    draft = DraftStore(env['store']).create(canonical(env['spec']), actor_id='alice', idempotency_key='first')
    ordinary = ValidationJobs(env['store'], env['library'].runtime_sha256); ordinary.install_trial_profile()
    queued = ordinary.enqueue(draft['draft_id'], actor_id='alice', expected_revision=1, idempotency_key='first')
    clock[0] += 1_000_000
    target = source.start_source(env['spec'], request_key='second', library=env['library'])
    assert source._SourceJobs(env['library'], target['job_id']).claim('target-worker') is None
    assert ordinary.worker_get(queued['job_id'])['state'] == 'queued'
    assert counts(env)['skill_validation_attempts'] == 0


def test_unknown_source_dispatch_is_never_replayed(env, monkeypatch):
    queued = source.start_source(env['spec'], request_key='unknown', library=env['library'])
    calls = []
    def failed(*args, **kwargs):
        calls.append(1)
        raise TimeoutError('unknown transport outcome')
    monkeypatch.setattr(execution, '_execute_code', failed)
    jobs = source._SourceJobs(env['library'], queued['job_id'])
    result = ValidationWorker(jobs, env['assets']).run_once('first-worker')
    assert result['state'] == 'needs_reconciliation'
    replay = source.start_source(env['spec'], request_key='unknown', library=env['library'])
    assert replay['job_id'] == queued['job_id']
    assert ValidationWorker(jobs, env['assets']).run_once('second-worker') is None
    source.tick_source(env['library'], 'reconcile-only')
    assert calls == [1]
    assert counts(env)['skill_validation_attempts'] == 1


def test_learning_closed_during_first_solve_preserves_q2_display_without_learning(env, monkeypatch):
    original = env['dispatch']
    def close_learning_after_solve(code, timeout, *, request_nonce):
        result = original(code, timeout, request_nonce=request_nonce)
        monkeypatch.setattr(config, 'SKILL_LEARNING_ENABLED', False)
        return result
    monkeypatch.setattr(execution, '_execute_code', close_learning_after_solve)
    params = canonical(dict(normalizer_version='normalizer_v2', parameters=env['spec']['parameters'],
                            result_spec=env['spec']['result_spec']))
    result = source.execute_source(scenario_type=env['spec']['scenario_type'], parameters_json=params,
                                   request_key='closed-mid-solve')
    assert result['source_execution_verified'] is True
    assert result['analysis']['quality_level'] == 'Q2'
    assert result['analysis']['units'] == 'numerical_source_normalization'
    assert result['learning']['observed'] is False
    assert result['learning']['reason'] == 'learning_disabled'
    assert len(env['value']['calls']) == 1
    assert counts(env)['skill_drafts'] == counts(env)['skill_validation_jobs'] == 1
    assert counts(env)['simulation_skills'] == counts(env)['skill_validation_admissions'] == 0
    displayed = dashboard.get_latest_analysis_by_run_ids([result['run_id']])[result['run_id']]
    assert displayed['units'] == 'numerical_source_normalization'
    with pytest.raises(ValueError, match='learning_disabled'):
        source.verify_source(result['run_id'], library=env['library'])


def test_completed_source_observation_crash_recovers_without_new_source_dispatch(env):
    job = finish_source(env)
    assert counts(env)['skill_drafts'] == 1 and len(env['value']['calls']) == 1
    recovered = source.tick_source(env['library'], 'recover-observation')
    assert recovered['job_id'] == job['job_id']
    assert recovered['learning']['observed'] is True
    assert recovered['learning']['queued'] is True
    assert len(env['value']['calls']) == 1
    assert counts(env)['skill_drafts'] == counts(env)['skill_validation_jobs'] == 2


def test_unsupported_source_has_durable_skip_marker_instead_of_infinite_observation(env):
    env['spec']['parameters']['frequency_hz'] *= .8
    job = finish_source(env)
    first = source.tick_source(env['library'], 'first-observer')
    assert first['learning']['reason'] == 'unsupported_learning_case'
    assert source.tick_source(env['library'], 'second-observer') is None
    with env['store']._read_db() as db:
        assert db.execute("SELECT count(*) FROM skill_draft_requests WHERE actor_id=? AND operation='source-observation-rejected' AND idempotency_key=?",
                          (source.ACTOR, job['attempt']['run_id'])).fetchone()[0] == 1
    assert len(env['value']['calls']) == 1 and counts(env)['simulation_skills'] == 0


def test_source_backfill_does_not_starve_an_already_validated_learning_job(env):
    auto_validated(env)
    finish_source(env, key='second-completed-source')
    assert source.tick_source(env['library'], 'give-admission-priority') is None
    with env['store']._read_db() as db:
        assert db.execute("SELECT count(*) FROM skill_draft_requests WHERE actor_id='internal-auto-learning' AND operation='auto-source'").fetchone()[0] == 1


def test_disabled_source_start_performs_no_source_writes(env, monkeypatch):
    before = counts(env)
    monkeypatch.setattr(config, 'SKILL_LEARNING_ENABLED', False)
    with pytest.raises(ValueError):
        source.start_source(env['spec'], request_key='disabled', library=env['library'])
    assert counts(env) == before and not env['value']['calls']
