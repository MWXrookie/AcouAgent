import hashlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import pytest
from host_observer import forwarder as f

RUN="12345678-1234-4234-8234-123456789012"
NONCE="a"*32
TOKEN="test-only-token-"+("x"*32)
IDENTITY={"container_id":"a"*64,"image_id":"sha256:"+("b"*64),"started_at":"2026-10-08T00:00:00Z",
          "running":True,"restart_count":0,"addresses":"127.0.0.1 "}

@pytest.fixture
def endpoint(monkeypatch):
    calls=[]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body=self.rfile.read(int(self.headers["Content-Length"]))
            calls.append((self.path,body,self.headers.get("X-Executor-Token")))
            raw=getattr(server,"raw",json.dumps({"stdout":"actual-output","stderr":"","exit_code":0,"timed_out":False}).encode())
            time.sleep(getattr(server,"header_delay",0))
            self.send_response(getattr(server,"status",200))
            self.send_header("Content-Length",str(getattr(server,"declared_length",len(raw))));self.end_headers()
            try:self.wfile.write(raw)
            except BrokenPipeError:pass
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    monkeypatch.setattr(f,"_snapshot",lambda:dict(IDENTITY))
    target=f.Target("127.0.0.1",server.server_port,IDENTITY["container_id"],IDENTITY["image_id"],IDENTITY["started_at"])
    yield target,server,calls
    server.shutdown();server.server_close();thread.join(timeout=2)

def run(target):
    return f.forward_fixed_request(target,run_id=RUN,nonce=NONCE,shared_token=TOKEN)

def test_actual_loopback_forwarding_hashes_wire_bytes_and_hides_raw_data(endpoint):
    target,server,calls=endpoint;r=run(target);o=r["observation"]
    assert len(calls)==1 and calls[0][0]=="/execute" and calls[0][2]==TOKEN
    assert json.loads(calls[0][1])["request_nonce"]==NONCE
    assert o["request_sha256"]==hashlib.sha256(calls[0][1]).hexdigest()
    assert o["stdout_sha256"]==hashlib.sha256(b"actual-output").hexdigest()
    assert all(not o[k] for k in ["authenticated","request_observed","parameters_bound","trusted_run"])
    assert "actual-output" not in json.dumps(o) and TOKEN not in json.dumps(o)
    assert o["peer_ip"]=="127.0.0.1"

@pytest.mark.parametrize("status",[301,302,401,500])
def test_http_failure_never_redirects_or_retries(endpoint,status):
    target,server,calls=endpoint;server.status=status
    with pytest.raises(ValueError,match="http_failure"):run(target)
    assert len(calls)==1

def test_restarted_identity_after_response_rejected(endpoint,monkeypatch):
    target,server,calls=endpoint
    snapshots=iter([IDENTITY,dict(IDENTITY,restart_count=1)])
    monkeypatch.setattr(f,"_snapshot",lambda:next(snapshots))
    with pytest.raises(ValueError,match="lifecycle_changed"):run(target)
    assert len(calls)==1

def test_wrong_target_rejected_before_forwarding(endpoint,monkeypatch):
    target,server,calls=endpoint
    monkeypatch.setattr(f,"_snapshot",lambda:dict(IDENTITY,image_id="sha256:"+("c"*64)))
    with pytest.raises(ValueError,match="identity_mismatch"):run(target)
    assert not calls

@pytest.mark.parametrize("raw",[b'{"stdout":"a","stdout":"b"}',b'{"stdout":NaN}',b'[]',b'{"stdout":"","stderr":"","exit_code":true,"timed_out":false}'])
def test_invalid_response_fails_closed(endpoint,raw):
    target,server,calls=endpoint;server.raw=raw
    with pytest.raises((ValueError,TypeError)):run(target)

def test_response_budget(endpoint,monkeypatch):
    target,server,calls=endpoint
    monkeypatch.setattr(f,"MAX_RESPONSE",8)
    with pytest.raises(ValueError,match="response_budget"):run(target)


def test_truncated_content_length_rejected(endpoint):
    target,server,calls=endpoint;server.declared_length=1000
    with pytest.raises(ValueError,match="truncated"):run(target)

def test_total_deadline_includes_headers(endpoint,monkeypatch):
    target,server,calls=endpoint;server.header_delay=0.5
    monkeypatch.setattr(f,"MAX_REQUEST_SECONDS",0.1)
    start=time.monotonic()
    with pytest.raises((OSError,ValueError)):run(target)
    assert time.monotonic()-start<0.4
    assert len(calls)==1
