"""Disposable end-to-end trust mechanics; synthetic arrays are not physics evidence."""
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

from app import config, dashboard, execution, skill_general_library as general
from app import skill_general_profiles as profiles, skill_library_service, skill_retrieval
from app.skill_drafts import DraftStore
from app.skill_general_validation import GeneralValidationJobs, GeneralValidationWorker
from app.skill_result_assets import ResultAssetStore
from app.skill_store import SkillStore
from app.skill_validation_jobs import canonical, digest


@pytest.fixture
def env(tmp_path, monkeypatch):
    for flag in ('SKILL_AUTHORING_ENABLED', 'SKILL_VALIDATION_ENABLED', 'SKILL_GENERAL_ADMISSION_ENABLED',
                 'SKILL_LIBRARY_ENABLED', 'SKILL_RETRIEVAL_ENABLED', 'SKILL_INJECTION_ENABLED'):
        monkeypatch.setattr(config, flag, True)
    store = SkillStore(tmp_path / 'skills.db'); store.migrate_skill_authoring()
    assets = ResultAssetStore(store, tmp_path / 'assets')
    refroot = tmp_path / 'references'; refroot.mkdir()
    field = np.ones((128, 128), dtype=np.complex64) * (2 + 3j)
    buffer = io.BytesIO(); np.save(buffer, field, allow_pickle=False)
    raw = buffer.getvalue(); (refroot / 'B01.npy').write_bytes(raw)
    actual_buffer = io.BytesIO(); np.save(actual_buffer, field.astype(np.complex128), allow_pickle=False)
    actual_raw = actual_buffer.getvalue()
    monkeypatch.setitem(profiles.REFERENCES, 'B01', hashlib.sha256(raw).hexdigest())
    source = Path(__file__).resolve().parents[2] / 'executor/executor.py'
    runtime = dict(executor_source_sha256=hashlib.sha256(source.read_bytes().replace(b'\r\n', b'\n')).hexdigest(),
                   python_version='3.12.9', python_implementation='CPython', machine='x86_64',
                   library_versions=profiles.policy('B01', 'a' * 64)['library_versions'])
    runtime_sha = digest(canonical(runtime))
    library = general.GeneralLibrary(store, assets, refroot, runtime_sha)
    library.install_profile('B01')
    jobs = GeneralValidationJobs(store, runtime_sha, 'B01', library)
    monkeypatch.setattr(dashboard, 'DB_PATH', str(tmp_path / 'history.db')); dashboard.init_db()
    monkeypatch.setattr(skill_library_service, 'STORE', store)
    monkeypatch.setattr(general, 'get_library', lambda store=None: library)
    calls = []
    def dispatch(code, timeout, *, request_nonce):
        calls.append(code)
        blob = base64.b64encode(zlib.compress(actual_raw)).decode('ascii')
        stdout = '__SKILL_PROBE_START__' + json.dumps(dict(quantity='complex_pressure', npy_zlib_base64=blob)) + '__SKILL_PROBE_END__'
        receipt = dict(version='executor-receipt.v1', request_nonce=request_nonce, code_sha256=digest(code),
                       stdout_sha256=digest(stdout), stderr_sha256=digest(''), exit_code=0, timed_out=False,
                       timeout_seconds=timeout, runtime=runtime, runtime_sha256=runtime_sha)
        local = dict(version='local-dispatch.v1', source_kind='managed_executor_transport',
                     request_sha256=digest(canonical(dict(code=code, timeout_seconds=timeout, request_nonce=request_nonce))),
                     code_sha256=digest(code), request_nonce=request_nonce, timeout_seconds=timeout,
                     trust_boundary='gateway_and_internal_executor')
        evidence = dict(receipt=receipt, local_dispatch=local, gateway_request_nonce=request_nonce, gateway_timeout_seconds=timeout)
        return dict(stdout=stdout, stderr='', exit_code=0, timed_out=False, duration_ms=1, execution_evidence=evidence)
    monkeypatch.setattr(execution, '_execute_code', dispatch)
    path = Path(__file__).resolve().parents[2] / 'docs/skills-next/examples/manual-skill.json'
    spec = json.loads(path.read_text(encoding='utf-8'))
    return dict(store=store, assets=assets, library=library, jobs=jobs, runtime=runtime, calls=calls, spec=spec, dispatch=dispatch)


def validated(env):
    drafts = DraftStore(env['store'])
    draft = drafts.create(canonical(env['spec']), actor_id='alice', idempotency_key='draft')
    args = dict(actor_id='alice', expected_revision=draft['revision'])
    preview = env['library'].save_controlled_draft(draft['draft_id'], **args)
    assert preview['changed'] and preview['diff']
    assert drafts.get(draft['draft_id'], actor_id='alice')['current_version'] == 1
    saved = env['library'].save_controlled_draft(draft['draft_id'], **args, apply=True)['draft']
    assert saved['current_version'] == 2
    assert drafts.get(draft['draft_id'], actor_id='alice', version=1)['spec']['code_template'] == env['spec']['code_template']
    env['jobs'].enqueue(draft['draft_id'], actor_id='alice', expected_revision=saved['revision'], idempotency_key='validate')
    row = GeneralValidationWorker(env['jobs'], env['assets'], env['library']).run_once('worker')
    assert row['state'] == 'validated', row
    return row


def admitted(env):
    row = validated(env)
    result = env['library'].admit(row['job_id'], actor_id='alice', expected_revision=row['revision'])
    assert result['admitted'] and result['quality_level'] == 'Q4'
    return row, result


@pytest.mark.parametrize('field', ['admission_binding', 'proof_pointer', 'case_identity'])
def test_tampered_admission_metadata_never_authorizes_retrieval(env, field):
    _, accepted = admitted(env)
    with env['store']._db_existing_write() as db:
        if field == 'admission_binding':
            db.execute("UPDATE skill_validation_admissions SET admission_binding_sha256=?", ('0' * 64,))
        else:
            row = db.execute("SELECT physics_evidence FROM skill_versions WHERE skill_id=?", (accepted['skill_id'],)).fetchone()
            value = json.loads(row[0])
            value['proof_sha256' if field == 'proof_pointer' else 'case_fingerprint'] = '0' * 64
            db.execute("UPDATE skill_versions SET physics_evidence=? WHERE skill_id=?", (canonical(value), accepted['skill_id']))
    args = query(env)
    assert not skill_retrieval.search(args['scenario_type'], args['parameters_json'])['skills']


def query(env):
    spec = profiles.build_controlled_spec(canonical(env['spec']))['spec']
    return dict(scenario_type=spec['scenario_type'], parameters_json=canonical(dict(normalizer_version='normalizer_v2',
                parameters=spec['parameters'], result_spec=spec['result_spec'])), original_context='simulate')


def delivered(env):
    from app import skill_generation_decision as decisions
    args = query(env)
    prepared = decisions.prepare(**args, _existing_store=True)
    assert prepared['prepared'], prepared
    run = str(uuid.uuid4())
    result = general.consume(decision_id=prepared['decision_id'], nonce=prepared['nonce'], generation_run_id=run, **args)
    assert result['consumed'], result
    return dict(decision_id=prepared['decision_id'], generation_run_id=run, **args)


def test_visible_derivation_validation_admission_delivery_and_fresh_execution(env):
    row, admitted_result = admitted(env)
    assert len(env['calls']) == 1
    duplicate = env['library'].admit(row['job_id'], actor_id='alice', expected_revision=row['revision'])
    assert duplicate['duplicate']
    args = delivered(env)
    result = general.execute_reference(**args)
    assert result['execution_reuse_verified'], result
    assert result['run_id'] != row['attempt']['run_id']
    assert len(env['calls']) == 2
    assert result['analysis']['quality_level'] == 'Q4'
    assert result['analysis']['heatmap_png_base64']
    assert not general.execute_reference(**args)['execution_reuse_verified']
    assert len(env['calls']) == 2


def test_constant_answer_template_cannot_enter_controlled_validation(env):
    buffer = io.BytesIO()
    np.save(buffer, np.ones((128, 128), dtype=np.complex64) * (2 + 3j), allow_pickle=False)
    copied_answer = '__SKILL_PROBE_START__' + json.dumps(dict(quantity='complex_pressure',
        npy_zlib_base64=base64.b64encode(zlib.compress(buffer.getvalue())).decode('ascii'))) + '__SKILL_PROBE_END__'
    env['spec']['code_template'] = f'print({copied_answer!r})'
    drafts = DraftStore(env['store'])
    draft = drafts.create(canonical(env['spec']), actor_id='alice', idempotency_key='cheat')
    with pytest.raises(ValueError, match='controlled_version_required'):
        env['jobs'].enqueue(draft['draft_id'], actor_id='alice', expected_revision=draft['revision'], idempotency_key='cheat')
    assert env['calls'] == []


def test_runtime_source_mismatch_cannot_validate(env):
    env['runtime']['executor_source_sha256'] = 'f' * 64
    with pytest.raises(AssertionError, match='needs_reconciliation'):
        validated(env)
    with sqlite3.connect(env['store'].path) as db:
        assert db.execute('SELECT count(*) FROM skill_validation_admissions').fetchone()[0] == 0


def test_archive_after_validation_blocks_admission(env):
    row = validated(env)
    drafts = DraftStore(env['store']); draft = drafts.get(row['draft_id'], actor_id='alice')
    drafts.archive(row['draft_id'], actor_id='alice', expected_revision=draft['revision'])
    with pytest.raises(ValueError, match='draft_changed_since_validation'):
        env['library'].admit(row['job_id'], actor_id='alice', expected_revision=row['revision'])


def test_profile_withdrawal_after_delivery_blocks_dispatch(env):
    admitted(env); args = delivered(env)
    with env['store']._db_existing_write() as db:
        db.execute("UPDATE skill_validation_profiles SET state='retired'")
    assert not general.execute_reference(**args)['execution_reuse_verified']
    assert len(env['calls']) == 1


def test_duplicate_independent_proof_is_idempotent(env):
    row = validated(env)
    one = env['library'].validate_result(row, row['attempt']['output_manifest_sha256'])
    two = env['library'].validate_result(row, row['attempt']['output_manifest_sha256'])
    assert one == two and one['passed']
    with sqlite3.connect(env['store'].path) as db:
        assert db.execute('SELECT count(*) FROM skill_validation_evidence').fetchone()[0] == 1


def test_uncertain_reference_dispatch_is_never_replayed(env, monkeypatch):
    admitted(env); args = delivered(env)
    calls = []
    def lost(*a, **kw):
        calls.append(1); raise OSError('lost response')
    monkeypatch.setattr(execution, '_execute_code', lost)
    assert not general.execute_reference(**args)['execution_reuse_verified']
    assert not general.execute_reference(**args)['execution_reuse_verified']
    assert len(calls) == 1


def test_cancel_during_dispatch_never_admits(env, monkeypatch):
    original = env['dispatch']
    def cancel(code, timeout, *, request_nonce):
        with sqlite3.connect(env['store'].path) as db:
            job_id = db.execute('SELECT job_id FROM skill_validation_jobs').fetchone()[0]
        row = env['jobs'].get(job_id, actor_id='alice')
        env['jobs'].cancel(job_id, actor_id='alice', expected_revision=row['revision'])
        return original(code, timeout, request_nonce=request_nonce)
    monkeypatch.setattr(execution, '_execute_code', cancel)
    with pytest.raises(AssertionError, match='cancel_requested'):
        validated(env)
    from app import skill_validation_jobs
    now = skill_validation_jobs._now()
    monkeypatch.setattr(skill_validation_jobs, '_now', lambda: now + 125_000)
    with sqlite3.connect(env['store'].path) as db:
        job_id = db.execute('SELECT job_id FROM skill_validation_jobs').fetchone()[0]
    reconciled = GeneralValidationWorker(env['jobs'], env['assets'], env['library']).reconcile(job_id)
    assert reconciled['state'] == 'cancelled'
    with sqlite3.connect(env['store'].path) as db:
        assert db.execute('SELECT count(*) FROM skill_validation_admissions').fetchone()[0] == 0


def test_missing_preflight_never_authorizes_write_transaction(env):
    row = validated(env)
    env['library']._preflights.clear()
    with env['store']._db_existing_write() as db:
        db.execute('BEGIN IMMEDIATE')
        with pytest.raises(ValueError, match='independent_preflight_required'):
            env['library'].verify_proof(db, row, row['attempt']['output_manifest_sha256'], row['attempt']['receipt_sha256'])


def test_changed_reference_bytes_block_admission(env):
    row = validated(env)
    sha = profiles.REFERENCES['B01']
    path = env['assets']._path(sha + '.npy')
    original = path.read_bytes(); path.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
    with pytest.raises(ValueError):
        env['library'].admit(row['job_id'], actor_id='alice', expected_revision=row['revision'])


def test_archived_active_skill_cannot_be_reused(env):
    _, admission = admitted(env); args = delivered(env)
    with env['store']._db_existing_write() as db:
        db.execute('UPDATE simulation_skills SET archived=1 WHERE skill_id=?', (admission['skill_id'],))
    assert not general.execute_reference(**args)['execution_reuse_verified']
    assert len(env['calls']) == 1


def test_real_run_bound_negative_feedback_blocks_reuse(env):
    row, admission = admitted(env); args = delivered(env)
    feedback = env['store'].record_negative_feedback(admission['skill_id'], row['attempt']['run_id'],
                                                    actor='internal-system', reason='Observed invalid result')
    assert feedback['recorded']
    assert not general.execute_reference(**args)['execution_reuse_verified']
    assert len(env['calls']) == 1
