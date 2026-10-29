"""No duplicate workflow dispatch; tests use synthetic events, not physics proof."""
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from app import local_workflow, workflow_contract
from app.task_contract import digest
from app.workflow_task_service import WorkflowTaskService
from tests.unit.test_workflow_contract import build
from tests.unit.test_local_workflow import _responses


def test_confirmed_workflow_does_not_extract_parameters_again(monkeypatch):
    value = build()
    frozen = workflow_contract.frozen_extraction(value,'profile')
    async def forbidden(*args): raise AssertionError('confirmed parameters must not be re-extracted')
    monkeypatch.setattr(local_workflow,'extract_parameters',forbidden)
    calls = []
    async def model(profile,task,prompt,run):
        calls.append(task)
        if task == 'parameter_extraction': raise AssertionError('unexpected extraction')
        return dict(content=_responses()[task],profile_id='unit-profile',model='unit-model')
    monkeypatch.setattr(local_workflow,'_model',model)
    monkeypatch.setattr(local_workflow,'_tool_functions',lambda:dict(
        validate_simulation_params=lambda raw:dict(valid=True,errors=[]),
        run_jwave_code=lambda *a:dict(exit_code=0,timed_out=False,stdout='unit-only',stderr='',run_id='unit-only-run')))
    monkeypatch.setattr(local_workflow.analysis,'analyze_and_record',lambda *a:dict(verdict='normal',quality_level='Q2',summary='unit-only'))
    monkeypatch.setattr(local_workflow.execution,'_build_report',lambda *a:'unit report')
    async def run():
        return [event async for event in local_workflow.workflow_events(value['source_text'],'owner','profile',_confirmed=frozen)]
    result = asyncio.run(run())[-1]
    assert result['workflow_status']=='succeeded' and result['run_id']=='unit-only-run'
    assert 'parameter_extraction' not in calls


def test_durable_workflow_status_and_concurrent_duplicate_requests(tmp_path,monkeypatch):
    service = WorkflowTaskService(tmp_path/'tasks.db')
    value,calls = build(),[]
    async def events(query,user,profile,*,_confirmed):
        calls.append(_confirmed)
        yield dict(event='progress',stage='unit-only progress')
        yield dict(event='result',workflow_status='succeeded',report='unit-only bookkeeping result',run_id='unit-run')
    monkeypatch.setattr(local_workflow,'workflow_events',events)
    with ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(lambda _:service.submit(value,'same-task','owner','profile'),range(5)))
    assert len(calls)==1
    first = service.status('same-task','owner','profile')
    assert first['state']=='succeeded' and first['run_id']=='unit-run'
    restored = WorkflowTaskService(service.path)
    second = restored.submit(value,'same-task','owner','profile')
    assert second['request_id']==first['request_id'] and len(calls)==1
    assert restored.status('same-task','other','profile') == dict(found=False)
    with pytest.raises(ValueError): restored.submit(value,'same-task','other','profile')


def test_restart_cannot_relabel_interrupted_job_as_running_or_resubmit(tmp_path,monkeypatch):
    service = WorkflowTaskService(tmp_path/'tasks.db')
    value = build()
    with service._db() as db:
        db.execute('INSERT INTO confirmed_workflow_tasks(task_key,owner_sha256,contract_sha256,request_id,state,result_json) VALUES (?,?,?,?,?,?)',
            (service._key('interrupted'),service._owner('owner','profile'),value['contract_sha256'],'unit-request','running',
             json.dumps(dict(state='running',workflow_status='running',request_id='unit-request'))))
    restored = WorkflowTaskService(service.path)
    status = restored.status('interrupted','owner','profile')
    assert status['state']=='unknown' and status['workflow_status']=='unknown'
    assert restored.submit(value,'interrupted','owner','profile')['state']=='unknown'


def test_failed_workflow_is_queryable_and_not_retried(tmp_path,monkeypatch):
    service = WorkflowTaskService(tmp_path/'tasks.db')
    calls = []
    async def broken(*args,**kwargs):
        calls.append(1)
        raise RuntimeError('private transport detail must not escape')
        yield
    monkeypatch.setattr(local_workflow,'workflow_events',broken)
    result = service.submit(build(),'failed','owner','profile')
    assert result['state']=='unknown'
    assert 'private' not in json.dumps(result)
    assert service.submit(build(),'failed','owner','profile')['state']=='unknown' and len(calls)==1


def test_execution_association_survives_interrupted_explanation(tmp_path,monkeypatch):
    service = WorkflowTaskService(tmp_path/'tasks.db')
    async def broken(*args,**kwargs):
        yield dict(event='progress',stage='unit execution recorded',run_id='physical-unit-run',workflow_run_id='workflow-unit-id')
        raise RuntimeError('private explanation error')
    monkeypatch.setattr(local_workflow,'workflow_events',broken)
    result = service.submit(build(),'interrupted-explanation','owner','profile')
    assert result['state']=='unknown' and result['run_id']=='physical-unit-run'
    restored = WorkflowTaskService(service.path)
    assert restored.status('interrupted-explanation','owner','profile')['workflow_run_id']=='workflow-unit-id'
    assert restored.submit(build(),'interrupted-explanation','owner','profile')['run_id']=='physical-unit-run'
