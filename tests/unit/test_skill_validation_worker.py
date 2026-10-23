import json
from unittest.mock import Mock

import pytest

from app import executor_evidence
from app.skill_validation_worker import ValidationWorker, _digest, verify_recorded_attempt


def fixture():
    code, nonce = 'print(1)', 'a' * 32
    runtime = dict(executor_source_sha256='b' * 64, python_version='3.12',
                   python_implementation='CPython', machine='x86_64',
                   library_versions={k: '1' for k in executor_evidence.LIBRARIES})
    response = dict(stdout='1', stderr='', exit_code=0, timed_out=False)
    receipt = dict(version='executor-receipt.v1', request_nonce=nonce,
                   code_sha256=executor_evidence.sha256(code), stdout_sha256=executor_evidence.sha256('1'),
                   stderr_sha256=executor_evidence.sha256(''), exit_code=0, timed_out=False,
                   timeout_seconds=30, runtime=runtime, runtime_sha256=_digest(runtime))
    dispatch = dict(version='local-dispatch.v1', source_kind='managed_executor_transport',
                    request_sha256=_digest(dict(code=code, timeout_seconds=30, request_nonce=nonce)),
                    code_sha256=receipt['code_sha256'], request_nonce=nonce, timeout_seconds=30,
                    trust_boundary='gateway_and_internal_executor')
    evidence = dict(receipt=receipt, local_dispatch=dispatch, gateway_request_nonce=nonce,
                    gateway_timeout_seconds=30)
    response['execution_evidence'] = evidence
    attempt = {k: dispatch[k] for k in ('request_nonce', 'request_sha256', 'code_sha256')}
    attempt.update(run_id='run1', attempt_no=1, parameters_sha256=_digest({}), runtime_sha256=_digest(runtime))
    job = dict(job_id='job1', attempt=attempt, spec=dict(code_template=code, parameters={}, result_spec={}),
               revision=1, fence=1, content_sha256='c' * 64, case_fingerprint='d' * 64)
    record = dict(response, run_id='run1', code=code, code_sha256=receipt['code_sha256'],
                  record_version='gateway-execution.v1', executor_evidence_json=json.dumps(evidence))
    return job, response, record


@pytest.mark.parametrize('key,value', [('code', 'print(2)'), ('stdout', 'PASS'),
    ('run_id', 'other'), ('record_version', 'legacy'), ('timed_out', 0)])
def test_tampered_history_rejected(key, value):
    job, _, record = fixture()
    record[key] = value
    with pytest.raises(ValueError):
        verify_recorded_attempt(job, record)


@pytest.mark.parametrize('key', ['request_sha256', 'runtime_sha256', 'parameters_sha256', 'code_sha256', 'request_nonce'])
def test_attempt_binding_rechecked(key):
    job, _, record = fixture()
    job['attempt'][key] = 'e' * 64
    with pytest.raises(ValueError):
        verify_recorded_attempt(job, record)


def setup_worker(job, response, record):
    jobs = Mock()
    jobs.claim.return_value = job
    jobs.worker_get.return_value = job
    for name in ('mark_dispatching', 'mark_unknown', 'finish', 'reconcile', 'heartbeat'):
        getattr(jobs, name).return_value = job
    assets = Mock()
    assets.ingest_executor.return_value = {'asset_sha256': 'f' * 64}
    assets.put_manifest.return_value = {'asset_sha256': '1' * 64}
    dispatcher, reader, writer = Mock(return_value=response), Mock(return_value=record), Mock()
    return ValidationWorker(jobs, assets, dispatcher=dispatcher, history_reader=reader,
                            history_writer=writer), jobs, dispatcher, writer


def test_single_dispatch_then_unique_history_recheck():
    job, response, record = fixture()
    worker, jobs, dispatch, writer = setup_worker(job, response, record)
    worker.run_once('worker1')
    dispatch.assert_called_once_with('print(1)', 30, request_nonce='a' * 32)
    writer.assert_called_once()
    assert jobs.finish.call_args.kwargs['succeeded'] is True
    assert jobs.finish.call_args.kwargs['reason'] == 'trial_completed_ungraded'
    jobs.mark_unknown.assert_not_called()


def test_transport_unknown_never_replayed_by_reconcile():
    job, response, record = fixture()
    worker, jobs, dispatch, writer = setup_worker(job, response, record)
    dispatch.side_effect = TimeoutError()
    worker.run_once('worker1')
    jobs.mark_unknown.assert_called_once()
    writer.assert_not_called()
    worker.reconcile(job['job_id'])
    dispatch.assert_called_once()
    jobs.reconcile.assert_called_once()


def test_missing_or_duplicate_history_stays_unknown():
    job, response, _ = fixture()
    worker, jobs, dispatch, _ = setup_worker(job, response, None)
    worker.run_once('worker1')
    worker.reconcile('job1')
    dispatch.assert_called_once()
    jobs.finish.assert_not_called()
    jobs.reconcile.assert_not_called()


def test_asset_failure_terminal_without_quality():
    job, response, record = fixture()
    worker, jobs, _, _ = setup_worker(job, response, record)
    worker.assets.ingest_executor.side_effect = ValueError('bad_field')
    worker.run_once('worker1')
    assert jobs.finish.call_args.kwargs['succeeded'] is False
    assert jobs.finish.call_args.kwargs['reason'] == 'invalid_result'


@pytest.mark.parametrize('expire', [False, True])
def test_real_jobs_assets_history_completion(tmp_path, monkeypatch, expire):
    import base64
    import io
    from pathlib import Path
    import zlib
    import numpy as np
    from app import config, dashboard
    from app.skill_drafts import DraftStore
    from app.skill_store import SkillStore
    from app.skill_validation_jobs import ValidationJobs
    from app.skill_result_assets import ResultAssetStore
    monkeypatch.setattr(config, 'SKILL_AUTHORING_ENABLED', True)
    monkeypatch.setattr(config, 'SKILL_VALIDATION_ENABLED', True)
    monkeypatch.setattr(dashboard, 'DB_PATH', str(tmp_path / 'history.db'))
    dashboard.init_db()
    clock = [1_000_000]
    monkeypatch.setattr('app.skill_validation_jobs._now', lambda: clock[0])
    store = SkillStore(tmp_path / 'skills.db')
    store.migrate_skill_authoring()
    _, response, _ = fixture()
    runtime = response['execution_evidence']['receipt']['runtime']
    jobs = ValidationJobs(store, _digest(runtime))
    jobs.install_trial_profile()
    spec = json.loads((Path(__file__).resolve().parents[2] / 'docs/skills-next/examples/manual-skill.json').read_text(encoding='utf-8'))
    draft = DraftStore(store).create(json.dumps(spec), actor_id='alice', idempotency_key='draft')
    queued = jobs.enqueue(draft['draft_id'], actor_id='alice', expected_revision=draft['revision'], idempotency_key='job')
    assets = ResultAssetStore(store, tmp_path / 'assets')

    def dispatch(code, timeout, *, request_nonce):
        buf = io.BytesIO()
        np.save(buf, np.ones((128, 128), dtype=np.complex64), allow_pickle=False)
        payload = {'quantity': 'complex_pressure', 'npy_zlib_base64': base64.b64encode(zlib.compress(buf.getvalue())).decode()}
        out = dict(stdout='__SKILL_PROBE_START__' + json.dumps(payload) + '__SKILL_PROBE_END__', stderr='', exit_code=0, timed_out=False)
        receipt = dict(version='executor-receipt.v1', request_nonce=request_nonce,
                       code_sha256=executor_evidence.sha256(code), stdout_sha256=executor_evidence.sha256(out['stdout']),
                       stderr_sha256=executor_evidence.sha256(''), exit_code=0, timed_out=False,
                       timeout_seconds=timeout, runtime=runtime, runtime_sha256=_digest(runtime))
        local = dict(version='local-dispatch.v1', source_kind='managed_executor_transport',
                     request_nonce=request_nonce, timeout_seconds=timeout, code_sha256=receipt['code_sha256'],
                     request_sha256=_digest(dict(code=code, timeout_seconds=timeout, request_nonce=request_nonce)),
                     trust_boundary='gateway_and_internal_executor')
        out['execution_evidence'] = dict(receipt=receipt, local_dispatch=local,
            gateway_request_nonce=request_nonce, gateway_timeout_seconds=timeout)
        if expire:
            clock[0] += 121_000
        return out

    dispatcher = Mock(side_effect=dispatch)
    worker = ValidationWorker(jobs, assets, dispatcher=dispatcher)
    result = worker.run_once('worker')
    if expire:
        assert result['state'] == 'needs_reconciliation'
        result = worker.reconcile(queued['job_id'])
    assert result['state'] == 'rejected'
    assert result['reason_code'] == 'trial_completed_ungraded'
    dispatcher.assert_called_once()
    manifest = assets.read_manifest(result['attempt']['output_manifest_sha256'])
    assert manifest['run_id'] == result['attempt']['run_id']
    assert assets.present(manifest['field_asset_sha256'], manifest['result_spec'])['max_pressure'] == 1


@pytest.mark.parametrize('tamper', ['nonce_duplicate', 'run_duplicate', 'invalid_flag'])
def test_readonly_history_rejects_ambiguity(tmp_path, monkeypatch, tamper):
    import sqlite3
    from app import dashboard
    from app.skill_validation_worker import read_unique_history
    monkeypatch.setattr(dashboard, 'DB_PATH', str(tmp_path / 'history.db'))
    dashboard.init_db()
    _, response, _ = fixture()
    def write(run_id):
        dashboard.record_execution('trial', 'print(1)', 0, False, 1, '1', '',
            run_id=run_id, execution_evidence=response['execution_evidence'])
    write('run1')
    assert read_unique_history('run1', 'a' * 32)['run_id'] == 'run1'
    if tamper == 'invalid_flag':
        with sqlite3.connect(dashboard.DB_PATH) as db:
            db.execute('UPDATE executions SET timed_out=2')
    else:
        write('run2' if tamper == 'nonce_duplicate' else 'run1')
    assert read_unique_history('run1', 'a' * 32) is None


def test_cancel_races_failed_dispatch_does_not_release_or_replay():
    job, response, record = fixture()
    worker, jobs, dispatch, _ = setup_worker(job, response, record)
    cancelled = dict(job, state='cancel_requested', revision=4)
    dispatch.side_effect = TimeoutError()
    jobs.mark_unknown.side_effect = ValueError('revision_conflict')
    jobs.worker_get.return_value = cancelled
    assert worker.run_once('worker') == cancelled
    jobs.recover_expired.assert_called_once()
    jobs.finish.assert_not_called()
    dispatch.assert_called_once()


@pytest.mark.parametrize('field,limit', [('code', 20000), ('stdout', 1048576),
    ('stderr', 1048576), ('executor_evidence_json', 16000)])
def test_history_field_budgets_enforced_before_hashing(tmp_path, monkeypatch, field, limit):
    import sqlite3
    from app import dashboard
    from app.skill_validation_worker import read_unique_history
    monkeypatch.setattr(dashboard, 'DB_PATH', str(tmp_path / 'history.db'))
    dashboard.init_db()
    job, response, record = fixture()
    dashboard.record_execution('trial', 'print(1)', 0, False, 1, '1', '',
        run_id='run1', execution_evidence=response['execution_evidence'])
    # A corrupted trusted database must not copy an oversized field into Python.
    with sqlite3.connect(dashboard.DB_PATH) as db:
        db.execute('UPDATE executions SET ' + field + '=?', ('x' * (limit + 1),))
    bounded = read_unique_history('run1', 'a' * 32)
    assert bounded[field] is None
    with pytest.raises(ValueError, match='history_field_budget'):
        verify_recorded_attempt(job, bounded)
    # Injected history readers must obey the identical UTF-8 byte limit.
    record[field] = '中' * (limit // 3 + 1)
    with pytest.raises(ValueError, match='history_field_budget'):
        verify_recorded_attempt(job, record)
