"""Capabilities must fail honestly before any model, storage or solver use."""
import asyncio
import json
from pathlib import Path

import pytest

from app.task_intake import capability_notice

ROOT = Path(__file__).resolve().parents[1] / 'fixtures/task_intake'
CASES = json.loads((ROOT/'cases.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('case', CASES, ids=lambda c: c['id'])
def test_task_capability_corpus(case):
    notice = capability_notice(case['query'])
    if case['route'] == 'existing_pipeline':
        assert notice is None
    else:
        assert notice['capability_state'] == case['route']
        assert notice['execution_performed'] is False


def test_original_user_query_does_not_call_models_or_executor(monkeypatch):
    from app import local_workflow
    original = json.loads((ROOT/'room_reference.json').read_text(encoding='utf-8'))['original_query']
    def forbidden(*args, **kwargs):
        raise AssertionError('unsupported task must not reach model/tool/database')
    monkeypatch.setattr(local_workflow, '_model', forbidden)
    monkeypatch.setattr(local_workflow, '_tool_functions', forbidden)
    async def collect():
        return [event async for event in local_workflow.workflow_events(original, 'regression', None)]
    events = asyncio.run(collect())
    assert len(events) == 1
    assert events[0]['error_code'] == 'task_capability_unavailable'
    assert events[0]['task_kind'] == 'room_eigenmodes'
    assert events[0]['skill_usage'] == []
    assert 'run_id' not in events[0]
    assert '参数物理检查未通过' not in events[0]['report']
