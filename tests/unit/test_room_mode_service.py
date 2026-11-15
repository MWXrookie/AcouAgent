"""Durable dispatch ownership, duplicate/concurrent requests and lost responses."""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app import dashboard, execution, room_mode_service
from app.task_contract import room_contract

QUERY = json.loads((Path(__file__).parents[1]/'fixtures/task_intake/room_reference.json').read_text(encoding='utf-8'))['original_query']


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, 'DB_PATH', str(tmp_path/'history.db'))
    dashboard.init_db()
    return room_mode_service.RoomModeService(tmp_path/'tasks.db'), room_contract(QUERY)


def test_lost_response_is_durable_and_never_automatically_redispatched(setup, monkeypatch):
    service, contract = setup
    calls = []
    def lost(*args, **kwargs):
        calls.append(args)
        raise TimeoutError('unit-only')
    monkeypatch.setattr(execution, '_execute_code', lost)
    first = service.submit(contract, 'id', 'owner', None)
    assert first['state'] == 'needs_reconciliation' and len(calls) == 1
    restored = room_mode_service.RoomModeService(service.path)
    second = restored.submit(contract, 'id', 'owner', None)
    assert second['run_id'] == first['run_id'] and len(calls) == 1
    assert restored.status('id', 'other', None) == {'found': False}
    assert restored.status('id', 'owner', 'different') == {'found': False}
    with pytest.raises(ValueError): restored.submit(contract, 'id', 'other', None)


def test_concurrent_same_id_reserves_exactly_one_dispatch(setup, monkeypatch):
    service, contract = setup
    calls = []
    def lost(*args, **kwargs):
        calls.append(args)
        raise TimeoutError('unit-only')
    monkeypatch.setattr(execution, '_execute_code', lost)
    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(lambda _:service.submit(contract, 'same-id', 'owner', None), range(5)))
    assert len(calls) == 1 and len({r['run_id'] for r in results}) == 1


def test_exit_zero_without_independent_physics_records_rejection_only(setup, monkeypatch):
    service, contract = setup
    monkeypatch.setattr(execution, '_execute_code', lambda *args, **kwargs:dict(stdout='{}',stderr='',exit_code=0,timed_out=False))
    result = service.submit(contract, 'bad-evidence', 'owner', None)
    assert result['state'] == 'rejected' and result['skill_admitted'] is False
    rows = dashboard.get_analysis_events()
    assert len(rows) == 1 and rows[0]['run_id'] == result['run_id'] and rows[0]['verdict'] == 'error'


def test_interrupted_dispatch_survives_service_restart_without_claiming_success(setup):
    service, contract = setup
    with service._db() as db:
        db.execute('INSERT INTO room_tasks(task_key,owner_sha256,contract_sha256,run_id,request_nonce,state) VALUES (?,?,?,?,?,?)',
            (room_mode_service.sha256('interrupted'),service._owner('owner',None),contract['contract_sha256'],'unit-run','a'*32,'dispatching'))
    restored = room_mode_service.RoomModeService(service.path)
    status = restored.status('interrupted', 'owner', None)
    assert status['workflow_status'] == 'unknown' and status['state'] == 'dispatching'
    assert restored.submit(contract,'interrupted','owner',None)['run_id'] == 'unit-run'
