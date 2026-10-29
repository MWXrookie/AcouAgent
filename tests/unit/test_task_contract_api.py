import asyncio

import httpx
from starlette.applications import Starlette
from starlette.routing import Route

from app import task_contract_api
from app.task_contract import ConfirmationDrafts

QUERY = '二维矩形房间，刚性壁面，Lx=4 m，Ly=3 m，c=343 m/s，求前10个模态频率。'


def client_app(runner=None, service=None):
    class Registry:
        routes = []
        def custom_route(self, path, methods):
            def decorate(fn):
                self.routes.append(Route(path, fn, methods=methods))
                return fn
            return decorate
    registry = Registry()
    task_contract_api.register(registry, runner=runner, service=service)
    return Starlette(routes=registry.routes)


def test_preview_is_read_only_and_incomplete_capability_cannot_execute(monkeypatch):
    monkeypatch.setattr(task_contract_api, 'DRAFTS', ConfirmationDrafts())
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=client_app()), base_url='http://isolated') as c:
            r = await c.post('/api/task-intake', json={'message': QUERY, 'session_id': 's'})
            data = r.json()
            assert data['handled'] and not data['execution_available']
            assert data['contract']['parameters']['length_x_m'] == 4
            assert r.headers['cache-control'] == 'no-store'
            r = await c.post('/api/task-intake/confirm', json={'message': QUERY, 'session_id': 's', 'task_id': data['task_id']})
            assert r.status_code == 409
            r = await c.post('/api/task-intake', json={'message': '水中1MHz点源', 'session_id': 's'})
            assert r.json() == {'handled': False}
    asyncio.run(run())


def test_confirmation_is_single_use_and_bound_to_actual_original_input(monkeypatch):
    monkeypatch.setattr(task_contract_api, 'DRAFTS', ConfirmationDrafts())
    calls = []
    def runner(contract):
        calls.append(contract)
        return {'workflow_status': 'succeeded', 'report': 'test runner only'}
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=client_app(runner)), base_url='http://isolated') as c:
            data = (await c.post('/api/task-intake', json={'message': QUERY, 'session_id': 's', 'model_profile': 'p'})).json()
            body = dict(message=QUERY, session_id='s', model_profile='p', task_id=data['task_id'],
                        contract_sha256=data['contract']['contract_sha256'],
                        accepted_proposals=[p['id'] for p in data['contract']['proposals']])
            for changed in [dict(body, message=QUERY+'长度改为5m'), dict(body, session_id='other'),
                            dict(body, model_profile='other'), dict(body, accepted_proposals=[{}])]:
                assert (await c.post('/api/task-intake/confirm', json=changed)).status_code == 400
            assert calls == []
            assert (await c.post('/api/task-intake/confirm', json=body)).status_code == 200
            assert (await c.post('/api/task-intake/confirm', json=body)).status_code == 400
            fresh = (await c.post('/api/task-intake', json={'message': QUERY, 'session_id': 's', 'model_profile': 'p'})).json()
            replacement = (await c.post('/api/task-intake', json={'message': QUERY, 'session_id': 's', 'model_profile': 'p', 'previous_task_id': fresh['task_id']})).json()
            assert (await c.post('/api/task-intake/confirm', json=dict(body, task_id=fresh['task_id']))).status_code == 400
            cancelled = await c.post('/api/task-intake/cancel', json={'task_id': replacement['task_id'], 'session_id': 's', 'model_profile': 'p'})
            assert cancelled.json() == {'cancelled': True}
            assert (await c.post('/api/task-intake/confirm', json=dict(body, task_id=replacement['task_id']))).status_code == 400
    asyncio.run(run())
    assert len(calls) == 1 and calls[0]['source_text'] == QUERY


def test_followup_preserves_text_and_requires_resolution_of_conflicting_values(monkeypatch):
    monkeypatch.setattr(task_contract_api, 'DRAFTS', ConfirmationDrafts())
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=client_app()), base_url='http://isolated') as c:
            data = (await c.post('/api/task-intake', json={'message': 'Lx=5m', 'requirement': QUERY, 'session_id': 's'})).json()
            assert data['contract']['status'] == 'needs_input'
            assert data['contract']['source_text'] == QUERY+'\nLx=5m'
            assert any(i['field'] == 'length_x_m' for i in data['contract']['issues'])
            for payload in [{'message': QUERY}, {'message': {}, 'session_id': 's'},
                            {'message': QUERY, 'session_id': 's', 'unknown': True}]:
                assert (await c.post('/api/task-intake', json=payload)).status_code == 400
            assert (await c.post('/api/task-intake', content='{"session_id":"s","session_id":"other"}')).status_code == 400
    asyncio.run(run())


def test_direct_chat_cannot_bypass_room_capability_guard_with_local_or_dify(monkeypatch):
    from app import config, portal, local_workflow
    def forbidden(*args, **kwargs):
        raise AssertionError('guarded room request must not merge or dispatch')
    monkeypatch.setattr(portal, '_merge_requirement', forbidden)
    monkeypatch.setattr(portal, 'workflow_events', forbidden)
    monkeypatch.setattr(local_workflow, 'workflow_events', forbidden)
    async def run():
        class Registry:
            routes = []
            def custom_route(self, path, methods):
                def decorate(fn):
                    self.routes.append(Route(path, fn, methods=methods))
                    return fn
                return decorate
        registry = Registry()
        portal.register(registry)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=Starlette(routes=registry.routes)), base_url='http://isolated') as c:
            for backend in ('local', 'dify'):
                monkeypatch.setattr(config, 'ORCHESTRATION_BACKEND', backend)
                r = await c.post('/chat', json={'message': QUERY, 'session_id': 's'})
                assert r.status_code == 200
                assert r.json()['error_code'] == 'task_capability_unavailable'
                r = await c.post('/chat', json={'message': 'Ly=3m', 'requirement': QUERY, 'session_id': 's'})
                assert r.json()['requirement'] == QUERY+'\nLy=3m'
                assert r.json()['execution_performed'] is False
                assert 'run_id' not in r.json()
            for name in ('message', 'requirement', 'session_id'):
                r = await c.post('/chat', json=dict({'message': 'x'}, **{name: []}))
                assert r.status_code == 400
    asyncio.run(run())


def test_durable_status_recovers_unknown_dispatch_without_resubmission(tmp_path, monkeypatch):
    from app import execution
    from app.room_mode_service import RoomModeService
    service = RoomModeService(tmp_path/'tasks.db')
    calls = []
    def lost(*args, **kwargs):
        calls.append(args)
        raise TimeoutError('unit-only')
    monkeypatch.setattr(execution, '_execute_code', lost)
    monkeypatch.setattr(task_contract_api, 'DRAFTS', ConfirmationDrafts())
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=client_app(service=service)),base_url='http://isolated') as c:
            data = (await c.post('/api/task-intake',json=dict(message=QUERY,session_id='owner'))).json()
            assert data['execution_available'] is True
            body = dict(message=QUERY,session_id='owner',task_id=data['task_id'],contract_sha256=data['contract']['contract_sha256'],
                        accepted_proposals=[p['id'] for p in data['contract']['proposals']])
            result = (await c.post('/api/task-intake/confirm',json=body)).json()
            assert result['state'] == 'needs_reconciliation' and len(calls) == 1
            assert (await c.post('/api/task-intake/confirm',json=body)).status_code == 400
            status = (await c.post('/api/task-intake/status',json=dict(task_id=data['task_id'],session_id='owner'))).json()
            assert status['run_id'] == result['run_id'] and len(calls) == 1
            assert (await c.post('/api/task-intake/status',json=dict(task_id=data['task_id'],session_id='other'))).json() == dict(found=False)
    asyncio.run(run())
