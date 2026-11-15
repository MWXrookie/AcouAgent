"""Durable, no-retry dispatch for the opt-in controlled room capability.

This ledger is separate from the existing execution/skill schemas. Interrupted
dispatches remain unresolved; a restart never silently resubmits a solver job.
"""
import json
from pathlib import Path
import sqlite3
import uuid
from contextlib import contextmanager

from app import dashboard, execution, room_modes
from app.executor_evidence import sha256


class RoomModeService:
    def __init__(self, path):
        self.path = Path(path)
        with self._db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS room_tasks (
                task_key TEXT PRIMARY KEY, owner_sha256 TEXT NOT NULL,
                contract_sha256 TEXT NOT NULL, run_id TEXT UNIQUE NOT NULL, request_nonce TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('dispatching','succeeded','rejected','needs_reconciliation')),
                result_json TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA synchronous=FULL')
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _owner(session, profile):
        return sha256(json.dumps([session, profile], separators=(',', ':')))

    def status(self, task_id, session, profile):
        with self._db() as db:
            row = db.execute('SELECT * FROM room_tasks WHERE task_key=? AND owner_sha256=?',
                (sha256(task_id), self._owner(session, profile))).fetchone()
        if row is None:
            return dict(found=False)
        if row['result_json'] is not None:
            return dict(found=True, **json.loads(row['result_json']))
        return dict(found=True, state=row['state'], run_id=row['run_id'], workflow_status='unknown',
                    report='任务已登记，但尚未确认完成。请查看执行看板；系统不会自动重复派发。')

    def submit(self, contract, task_id, session, profile):
        code = room_modes.build_code(contract)
        key, owner, run_id, nonce = sha256(task_id), self._owner(session, profile), str(uuid.uuid4()), uuid.uuid4().hex
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT owner_sha256,contract_sha256 FROM room_tasks WHERE task_key=?', (key,)).fetchone()
            if row:
                if row['owner_sha256'] != owner or row['contract_sha256'] != contract['contract_sha256']:
                    raise ValueError('room_task_binding_mismatch')
            else:
                db.execute('INSERT INTO room_tasks(task_key,owner_sha256,contract_sha256,run_id,request_nonce,state) VALUES (?,?,?,?,?,?)',
                           (key, owner, contract['contract_sha256'], run_id, nonce, 'dispatching'))
        if row:
            return self.status(task_id, session, profile)
        # Once the durable row exists, every failure is a status to reconcile,
        # never a reason for an automatic retry or a new run id.
        try:
            result = execution._execute_code(code, 30, request_nonce=nonce)
            dashboard.record_execution(tool_name='controlled_room_modes_2d', code=code,
                exit_code=result.get('exit_code'), timed_out=result.get('timed_out'),
                duration_ms=result.get('duration_ms'), stdout=result.get('stdout', ''),
                stderr=result.get('stderr', ''), run_id=run_id,
                execution_evidence=result.get('execution_evidence'))
            try:
                validation = room_modes.verify_result(contract, result)
            except (ValueError, TypeError, KeyError, OverflowError):
                summary = '房间模态独立验证未通过；本次未学习技能，请查看本次执行记录。'
                verdict, state = 'error', 'rejected'
                payload = dict(state=state, workflow_status='error', report=summary, run_id=run_id,
                               execution_performed=True, skill_usage=[], skill_admitted=False)
            else:
                summary = (f"前{len(validation['modes'])}个房间模态已完成，解析核对与网格收敛检查通过。"
                           f"所列模态最大相对误差{validation['max_relative_error']:.4%}。此结果尚未加入可复用技能库。")
                verdict, state = 'normal', 'succeeded'
                payload = dict(state=state, workflow_status='succeeded', report=room_modes.report(validation, run_id),
                    run_id=run_id, execution_performed=True, validation=validation, skill_usage=[], skill_admitted=False)
            dashboard.record_analysis_event(exit_code=result.get('exit_code'), verdict=verdict,
                max_pressure=None, rms_pressure=None, field_shape=None, has_signal=False,
                summary=summary, heatmap_base64=None, waveform_base64=None,
                stdout_excerpt='', stderr_excerpt='', run_id=run_id)
        except Exception:
            state = 'needs_reconciliation'
            payload = dict(state=state, workflow_status='unknown', run_id=run_id,
                report='任务派发或记录未确认完成，请查看执行看板后核对。系统不会自动重试，也未接纳技能。',
                skill_usage=[], skill_admitted=False)
        with self._db() as db:
            db.execute('UPDATE room_tasks SET state=?,result_json=?,updated_at=CURRENT_TIMESTAMP WHERE task_key=?',
                       (state, json.dumps(payload, ensure_ascii=False, allow_nan=False), key))
        return payload
