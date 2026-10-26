"""Internal single-dispatch trial worker. Correlation never grants scientific trust."""
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import threading
import time

from app import dashboard, execution, executor_evidence

TIMEOUT_SECONDS = 30


def _digest(value):
    return executor_evidence.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False))


def read_unique_history(run_id, nonce):
    """Read only, fail closed on duplicates and malformed stored SQLite flags."""
    with closing(sqlite3.connect(Path(dashboard.DB_PATH).resolve().as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
        db.row_factory = sqlite3.Row
        deadline, ticks = time.monotonic() + 2, [0]
        def budget():
            ticks[0] += 1
            return ticks[0] >= 2000 or time.monotonic() > deadline
        db.set_progress_handler(budget, 1000)
        db.execute('BEGIN')
        rows = db.execute("""SELECT run_id,record_version,exit_code,timed_out,code_sha256,
            CASE WHEN length(CAST(code AS BLOB))<=20000 THEN code END AS code,
            CASE WHEN length(CAST(stdout AS BLOB))<=1048576 THEN stdout END AS stdout,
            CASE WHEN length(CAST(stderr AS BLOB))<=1048576 THEN stderr END AS stderr,
            CASE WHEN length(CAST(executor_evidence_json AS BLOB))<=16000
                 THEN executor_evidence_json END AS executor_evidence_json
            FROM executions WHERE run_id=? OR
            CASE WHEN json_valid(executor_evidence_json) THEN
            json_extract(executor_evidence_json,'$.gateway_request_nonce')=? ELSE 0 END LIMIT 2""",
            (run_id, nonce)).fetchall()
        if len(rows) != 1:
            return None
        record = dict(rows[0])
        if type(record.get('timed_out')) is not int or record['timed_out'] not in (0, 1):
            return None
        record['timed_out'] = bool(record['timed_out'])
        return record


def verify_recorded_attempt(job, record):
    """Recompute bindings from an immutable draft, attempt and unique history row."""
    attempt, spec = job['attempt'], job['spec']
    if not isinstance(record, dict) or any(
            type(record.get(key)) is not str or len(record[key].encode('utf-8')) > limit
            for key, limit in (('code', 20000), ('stdout', 1048576), ('stderr', 1048576),
                               ('executor_evidence_json', 16000))):
        raise ValueError('history_field_budget')
    code, nonce = spec['code_template'], attempt['request_nonce']
    request = {'code': code, 'timeout_seconds': TIMEOUT_SECONDS, 'request_nonce': nonce}
    if (not isinstance(record, dict) or record.get('record_version') != 'gateway-execution.v1'
            or record.get('run_id') != attempt['run_id'] or record.get('code') != code
            or record.get('code_sha256') != executor_evidence.sha256(code)
            or attempt['code_sha256'] != executor_evidence.sha256(code)
            or attempt['request_sha256'] != _digest(request)
            or attempt['parameters_sha256'] != _digest(spec['parameters'])
            or type(record.get('timed_out')) is not bool):
        raise ValueError('history_binding_mismatch')
    evidence = json.loads(record.get('executor_evidence_json') or '{}')
    receipt = evidence.get('receipt')
    checked = executor_evidence.verify_executor_receipt(receipt, record, code, TIMEOUT_SECONDS, nonce)
    dispatch = evidence.get('local_dispatch', {})
    if (checked['matched'] is not True or receipt['runtime_sha256'] != attempt['runtime_sha256']
            or evidence.get('gateway_request_nonce') != nonce
            or evidence.get('gateway_timeout_seconds') != TIMEOUT_SECONDS
            or any(dispatch.get(k) != v for k, v in {
                'version': 'local-dispatch.v1', 'source_kind': 'managed_executor_transport',
                'request_sha256': _digest(request), 'code_sha256': executor_evidence.sha256(code),
                'request_nonce': nonce, 'timeout_seconds': TIMEOUT_SECONDS,
                'trust_boundary': 'gateway_and_internal_executor',
            }.items())):
        raise ValueError('executor_binding_mismatch')
    return _digest(receipt)


class ValidationWorker:
    def __init__(self, jobs, assets, *, dispatcher=None, history_reader=None,
                 history_writer=None, heartbeat_interval=5):
        self.jobs, self.assets = jobs, assets
        self.dispatcher = dispatcher or execution._execute_code
        self.history_reader = history_reader or read_unique_history
        self.history_writer = history_writer or dashboard.record_execution
        self.heartbeat_interval = heartbeat_interval

    def _bind(self, job):
        record = self.history_reader(job['attempt']['run_id'], job['attempt']['request_nonce'])
        receipt_hash = verify_recorded_attempt(job, record)
        succeeded = record['exit_code'] == 0 and record['timed_out'] is False
        if not succeeded:
            return dict(manifest_sha256=None, receipt_sha256=receipt_hash,
                        succeeded=False, reason='executor_failed')
        try:
            field = self.assets.ingest_executor(record, job['spec']['result_spec'])
            attempt = job['attempt']
            manifest = {k: attempt[k] for k in ('run_id', 'request_nonce', 'request_sha256',
                        'code_sha256', 'parameters_sha256', 'runtime_sha256')}
            manifest.update(job_id=job['job_id'], attempt_no=attempt['attempt_no'],
                            content_sha256=job['content_sha256'], case_fingerprint=job['case_fingerprint'],
                            result_spec=job['spec']['result_spec'], field_asset_sha256=field['asset_sha256'],
                            receipt_sha256=receipt_hash)
            asset = self.assets.put_manifest(manifest)
            return dict(manifest_sha256=asset['asset_sha256'], receipt_sha256=receipt_hash,
                        succeeded=True, reason='trial_completed_ungraded')
        except (ValueError, OSError):
            return dict(manifest_sha256=None, receipt_sha256=receipt_hash,
                        succeeded=False, reason='invalid_result')

    def run_once(self, owner):
        job = self.jobs.claim(owner)
        if job is None:
            return None
        lock, stop = threading.Lock(), threading.Event()
        lost = []

        def mutate(method, **kwargs):
            nonlocal job
            with lock:
                job = method(job['job_id'], owner=owner, fence=job['fence'],
                             expected_revision=job['revision'], **kwargs)
                return job

        def heartbeat():
            while not stop.wait(self.heartbeat_interval):
                try:
                    mutate(self.jobs.heartbeat)
                except Exception:
                    lost.append(True)
                    return

        mutate(self.jobs.mark_dispatching)
        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        try:
            result = self.dispatcher(job['spec']['code_template'], TIMEOUT_SECONDS,
                                     request_nonce=job['attempt']['request_nonce'])
            self.history_writer('skill_validation_trial', job['spec']['code_template'],
                                result['exit_code'], result['timed_out'], result.get('duration_ms'),
                                result['stdout'], result['stderr'], run_id=job['attempt']['run_id'],
                                execution_evidence=result.get('execution_evidence'))
            bound = self._bind(job)
        except Exception:
            stop.set()
            thread.join()
            if not lost:
                try:
                    return mutate(self.jobs.mark_unknown, reason='dispatch_or_binding_unknown')
                except ValueError:
                    self.jobs.recover_expired()
            return self.jobs.worker_get(job['job_id'])
        finally:
            stop.set()
            thread.join()
        if lost:
            return self.jobs.worker_get(job['job_id'])
        try:
            return mutate(self.jobs.finish, **bound)
        except ValueError:
            self.jobs.recover_expired()
            return self.jobs.worker_get(job['job_id'])

    def reconcile(self, job_id):
        """Only consume an existing history record. Never invokes dispatcher."""
        self.jobs.recover_expired()
        job = self.jobs.worker_get(job_id)
        try:
            bound = self._bind(job)
        except Exception:
            return job
        return self.jobs.reconcile(job_id, expected_revision=job['revision'], **bound)
