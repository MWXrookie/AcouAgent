"""One confirmed workflow request, durably tracked, never auto-resubmitted.

The original workflow still owns executor receipts, analysis, retries and skill
learning gates. This layer freezes intake, not scientific trust.
"""
import asyncio
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import uuid
import threading

from app import local_workflow
from app.task_contract import digest
from app.workflow_contract import frozen_extraction


class WorkflowTaskService:
    def __init__(self,path):
        self.path = Path(path)
        self._active,self._lock = set(),threading.Lock()
        with self._db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS confirmed_workflow_tasks (
                task_key TEXT PRIMARY KEY,owner_sha256 TEXT NOT NULL,contract_sha256 TEXT NOT NULL,
                request_id TEXT UNIQUE NOT NULL,state TEXT NOT NULL,
                result_json TEXT NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path,timeout=5)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA synchronous=FULL')
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _key(task_id):
        if not isinstance(task_id,str) or not 1<=len(task_id)<=128:
            raise ValueError('invalid_task_id')
        return digest(dict(task_id=task_id))

    @staticmethod
    def _owner(session,profile):
        return digest(dict(session=session,profile=profile))

    def status(self,task_id,session,profile):
        with self._db() as db:
            row = db.execute('SELECT result_json FROM confirmed_workflow_tasks WHERE task_key=? AND owner_sha256=?',
                (self._key(task_id),self._owner(session,profile))).fetchone()
        if row is None:
            return dict(found=False)
        result = json.loads(row['result_json'])
        with self._lock:
            active = self._key(task_id) in self._active
        if result.get('state')=='running' and not active:
            result = dict(result,state='unknown',workflow_status='unknown',
                report='上次任务的执行进程已无法确认。请查看执行看板后核对，系统不会自动重新提交整项任务。')
        return dict(found=True,**result)

    def _update(self,key,state,payload):
        # Reports are bounded before persistence; never store model credentials
        # or raw exceptions in status responses.
        encoded = json.dumps(payload,ensure_ascii=False,allow_nan=False)
        if len(encoded.encode())>1048576:
            raise ValueError('confirmed_workflow_result_too_large')
        with self._db() as db:
            db.execute('UPDATE confirmed_workflow_tasks SET state=?,result_json=?,updated_at=CURRENT_TIMESTAMP WHERE task_key=?',
                       (state,encoded,key))

    def submit(self,contract,task_id,session,profile):
        frozen = frozen_extraction(contract,profile)
        key,owner = self._key(task_id),self._owner(session,profile)
        request_id = str(uuid.uuid4())
        pending = dict(request_id=request_id,state='running',workflow_status='running',
                       report='已保存确认条件，正在执行；请勿重复提交。')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT owner_sha256,contract_sha256 FROM confirmed_workflow_tasks WHERE task_key=?',(key,)).fetchone()
            if old:
                if old['owner_sha256']!=owner or old['contract_sha256']!=contract['contract_sha256']:
                    raise ValueError('workflow_task_binding_mismatch')
            else:
                db.execute('INSERT INTO confirmed_workflow_tasks(task_key,owner_sha256,contract_sha256,request_id,state,result_json) VALUES (?,?,?,?,?,?)',
                    (key,owner,contract['contract_sha256'],request_id,'running',json.dumps(pending,ensure_ascii=False)))
        if old:
            return self.status(task_id,session,profile)
        with self._lock:
            self._active.add(key)
        async def run():
            final = None
            async for event in local_workflow.workflow_events(frozen.source_text,session,profile,_confirmed=frozen):
                if event.get('event') == 'result':
                    final = dict(event)
                    for name in ('run_id','workflow_run_id'):
                        if not final.get(name) and pending.get(name):
                            final[name] = pending[name]
                elif event.get('event') == 'progress':
                    for name in ('run_id','workflow_run_id'):
                        value = event.get(name)
                        if isinstance(value,str) and 1<=len(value)<=128:
                            pending[name] = value
                    stage = event.get('stage')
                    if isinstance(stage,str) and len(stage)<=128:
                        self._update(key,'running',dict(pending,report=stage+'；本次任务仍在进行。'))
            if final is None:
                raise ValueError('workflow_result_missing')
            return final
        try:
            result = asyncio.run(run())
            state = 'succeeded' if result.get('workflow_status')=='succeeded' else 'error'
            result = dict(result,state=state,request_id=request_id)
            self._update(key,state,result)
        except Exception:
            result = dict(pending,state='unknown',workflow_status='unknown',
                report='任务尚未确认完成。请查询上次计算或查看执行看板后核对；系统不会自动重新派发。')
            self._update(key,'unknown',result)
        finally:
            with self._lock:
                self._active.discard(key)
        return result
