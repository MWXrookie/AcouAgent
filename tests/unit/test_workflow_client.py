import asyncio
import json
import httpx
import pytest
from app import workflow_client as wc

REAL_CLIENT = httpx.AsyncClient

def run(monkeypatch, frames, status=200, content_type="text/event-stream"):
    calls = []
    def handler(request):
        calls.append(request)
        if request.url.path.endswith("/stop"):
            return httpx.Response(200, json={"result": "success"})
        body = "".join("data: " + json.dumps(frame) + "\n\n" for frame in frames)
        return httpx.Response(status, text=body, headers={"content-type": content_type})
    monkeypatch.setattr(wc.httpx, "AsyncClient",
                        lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(handler), **kw))
    async def collect():
        return [e async for e in wc.workflow_events("test", "portal-test", "test-token")]
    return asyncio.run(collect()), calls

def test_success_and_progress_hide_private_inputs(monkeypatch):
    events, calls = run(monkeypatch, [
        {"event":"node_started","task_id":"task-1","workflow_run_id":"run-1",
         "data":{"title":"代码生成","inputs":{"secret":"do-not-forward"}}},
        {"event":"workflow_finished","data":{"status":"succeeded","outputs":{"text":"report"}}}
    ])
    assert events[-1]["workflow_status"] == "succeeded"
    assert events[-1]["outputs"]["text"] == "report"
    assert "do-not-forward" not in json.dumps(events)
    assert len(calls) == 1
    assert json.loads(calls[0].content)["response_mode"] == "streaming"

def test_provider_failure_sanitized(monkeypatch):
    events, calls = run(monkeypatch, [
        {"event":"workflow_finished","data":{"status":"failed","error":"Read timed out secret-key"}}
    ])
    assert "超时" in events[-1]["report"]
    assert "secret-key" not in str(events)
    assert events[-1]["workflow_status"] == "error"
    assert len(calls) == 1

def test_truncated_stream_requests_stop_no_replay(monkeypatch):
    events, calls = run(monkeypatch, [{"event":"workflow_started","task_id":"task-1"}])
    assert events[-1]["stop_status"] == "requested"
    assert len(calls) == 2
    assert calls[-1].url.path.endswith("/task-1/stop")
    assert json.loads(calls[-1].content)["user"] == "portal-test"

@pytest.mark.parametrize("status", [401,403,429,500])
def test_http_error(monkeypatch,status):
    events,calls = run(monkeypatch, [],status=status)
    assert events[-1]["workflow_status"] == "error"
    assert len(calls) == 1
    assert str(status) in events[-1]["report"] or status in (401,403)

def test_invalid_response(monkeypatch):
    events,_ = run(monkeypatch, [],content_type="application/json")
    assert events[-1]["workflow_status"] == "error"

def test_empty_completed_report(monkeypatch):
    events,_ = run(monkeypatch,[{"event":"workflow_finished","data":{"status":"succeeded","outputs":{}}}])
    assert events[-1]["workflow_status"] == "error"

def test_deadline_cancels_and_stops(monkeypatch):
    calls=[]
    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"event":"workflow_started","task_id":"task-1"}\n\n'
            await asyncio.sleep(1)
    def handler(req):
        calls.append(req)
        if req.url.path.endswith("/stop"): return httpx.Response(200)
        return httpx.Response(200,stream=SlowStream(),headers={"content-type":"text/event-stream"})
    monkeypatch.setattr(wc,"DEADLINE_SECONDS",.02)
    monkeypatch.setattr(wc.httpx,"AsyncClient",lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(handler),**kw))
    async def collect(): return [e async for e in wc.workflow_events("q","u","test-token")]
    events=asyncio.run(collect())
    assert "超时" in events[-1]["report"]
    assert events[-1]["stop_status"]=="requested"
    assert len(calls)==2

def test_size_limit_stops(monkeypatch):
    monkeypatch.setattr(wc,"MAX_EVENT_BYTES",80)
    events,calls=run(monkeypatch,[{"event":"unknown","data":{"padding":"x"*100}}])
    assert events[-1]["workflow_status"]=="error"
    assert len(calls)==1

def test_no_provider_traceback_for_error_event(monkeypatch):
    events,calls=run(monkeypatch,[{"event":"error","message":"secret traceback"}])
    assert events[-1]["workflow_status"]=="error"
    assert "secret" not in str(events)
    assert len(calls)==1

def test_verified_skill_feedback_is_streamed_and_retained(monkeypatch):
 from app import skill_usage
 notice=dict(stage="reference_delivered",skill_id="id",version=1,name="water axial",message="reference delivered",model_adoption_authenticated=False)
 monkeypatch.setattr(skill_usage,"reference_notice",lambda data: notice)
 events,_=run(monkeypatch,[{"event":"node_finished","data":{"node_id":"local-basic-main-reader","inputs":{"secret":"hidden"}}},{"event":"workflow_finished","data":{"status":"succeeded","outputs":{"text":"report"}}}])
 assert events[-1]["skill_usage"]==[notice]
 assert any(e["event"]=="skill_usage" for e in events)
 assert "hidden" not in json.dumps(events)
