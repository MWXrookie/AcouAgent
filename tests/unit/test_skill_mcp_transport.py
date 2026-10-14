"""Actual FastMCP HTTP middleware with synthetic auth, no external service."""
import asyncio
import json
import pytest
import httpx
from fastmcp import FastMCP
try:
    from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
except ImportError:
    from fastmcp.server.auth import StaticTokenVerifier
from app import config
from app.tools import register

def test_default_off_context_tools_require_actual_http_auth_and_fallback(tmp_path,monkeypatch):
    monkeypatch.setattr(config,"SKILL_LIBRARY_ENABLED",False)
    monkeypatch.setattr(config,"SKILL_RETRIEVAL_ENABLED",False)
    monkeypatch.setattr(config,"SKILL_INJECTION_ENABLED",False)
    token="isolated-synthetic-token-never-production"*2
    verifier=StaticTokenVerifier(tokens={token:{"client_id":"isolated-test","scopes":["mcp:tools"]}},
        required_scopes=["mcp:tools"])
    mcp=FastMCP("isolated-skill-transport",auth=verifier)
    register(mcp)
    app=mcp.http_app(path="/mcp",stateless_http=True)
    async def run():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://isolated") as client:
                headers={"accept":"application/json, text/event-stream"}
                request={"jsonrpc":"2.0","id":1,"method":"tools/call","params":{
                    "name":"read_simulation_generation_context","arguments":{
                        "original_context":"original","scenario_type":"initial_pressure_2d",
                        "parameters_json":"{}","decision_id":"","generation_run_id":""}}}
                denied=await client.post("/mcp",json=request,headers=headers)
                assert denied.status_code==401
                wrong=await client.post("/mcp",json=request,headers=dict(headers,Authorization="Bearer wrong-synthetic"))
                assert wrong.status_code==401
                headers["Authorization"]="Bearer "+token
                response=await client.post("/mcp",json=request,headers=headers)
                assert response.status_code==200,response.text
                # SSE framing is transport data, not a trust claim.
                chunks=[line[6:] for line in response.text.splitlines() if line.startswith("data: ")]
                body=json.loads(chunks[0]) if chunks else response.json()
                assert body["result"]["content"][0]["text"]=="original"
                assert body["result"].get("isError") is not True
                request["id"]=2
                request["params"]={"name":"build_simulation_generation_context","arguments":{
                    "original_context":"original","scenario_type":"initial_pressure_2d","parameters_json":"{}"}}
                response=await client.post("/mcp",json=request,headers=headers)
                assert response.status_code==200,response.text
                chunks=[line[6:] for line in response.text.splitlines() if line.startswith("data: ")]
                body=json.loads(chunks[0]) if chunks else response.json()
                payload=json.loads(body["result"]["content"][0]["text"])
                assert payload["context"]=="original"
                assert payload["skill_usage"]["fallback"] is True
    asyncio.run(asyncio.wait_for(run(),timeout=10))

from tests.unit.test_skill_reference_registry import proof
from tests.unit.test_skill_reference_consumer import available
from tests.unit.test_skill_host_intake import ready
from tests.unit.test_skill_authority import prepared,root

def test_authenticated_http_positive_reader_rechecks_root_withdrawal(proof):
    from app import skill_reference_registry as registry,skill_authority
    store,key,call,evidence=proof
    assert registry.register(evidence)["recorded"]
    token="synthetic-fixed-profile-http-test"*2
    verifier=StaticTokenVerifier(tokens={token:{"client_id":"test","scopes":["mcp:tools"]}},
        required_scopes=["mcp:tools"])
    mcp=FastMCP("isolated-positive-transport",auth=verifier);register(mcp)
    app=mcp.http_app(path="/mcp",stateless_http=True)
    async def run():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://isolated") as client:
                headers={"accept":"application/json, text/event-stream","Authorization":"Bearer "+token}
                async def invoke(name,args,identifier):
                    response=await client.post("/mcp",headers=headers,json={"jsonrpc":"2.0","id":identifier,
                        "method":"tools/call","params":{"name":name,"arguments":args}})
                    assert response.status_code==200,response.text
                    chunks=[line[6:] for line in response.text.splitlines() if line.startswith("data: ")]
                    body=json.loads(chunks[0]) if chunks else response.json()
                    assert body["result"].get("isError") is not True
                    return body["result"]["content"][0]["text"]
                args=dict(original_context="http original",scenario_type="plane_wave_2d",
                    parameters_json=call["parameters_json"])
                built=json.loads(await invoke("build_simulation_generation_context",args,1))
                assert not built["skill_usage"]["fallback"]
                assert built["skill_usage"]["actual_use_authenticated"] is False
                args.update(decision_id=built["skill_usage"]["decision_id"],
                    generation_run_id=built["skill_usage"]["generation_intent_id"])
                assert await invoke("read_simulation_generation_context",args,2)==built["context"]
                assert skill_authority.record_root_statement(root(key,2,"revoke"))["recorded"]
                assert await invoke("read_simulation_generation_context",args,3)=="http original"
    asyncio.run(asyncio.wait_for(run(),timeout=10))


def test_actual_loopback_mcp_build_read_and_revoke(proof):
    """Real loopback TCP/SSE, synthetic root/auth and analytic trace only."""
    import socket
    import uvicorn
    from app import skill_reference_registry as registry,skill_authority
    store,key,call,evidence=proof
    assert registry.register(evidence)["recorded"]
    token="synthetic-loopback-mcp-only"*3
    verifier=StaticTokenVerifier(tokens={token:{"client_id":"test","scopes":["mcp:tools"]}},
        required_scopes=["mcp:tools"])
    mcp=FastMCP("isolated-loopback",auth=verifier);register(mcp)
    app=mcp.http_app(path="/mcp",stateless_http=True)
    async def run():
        sock=socket.socket();sock.bind(("127.0.0.1",0));sock.listen(16)
        port=sock.getsockname()[1]
        server=uvicorn.Server(uvicorn.Config(app,log_level="error",access_log=False,
            lifespan="on",timeout_graceful_shutdown=1))
        task=asyncio.create_task(server.serve(sockets=[sock]))
        try:
            deadline=asyncio.get_running_loop().time()+2
            while not server.started:
                if task.done():await task
                if asyncio.get_running_loop().time()>deadline:raise TimeoutError("local test server startup")
                await asyncio.sleep(.01)
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}",timeout=3,trust_env=False) as client:
                headers={"accept":"application/json, text/event-stream"}
                query={"jsonrpc":"2.0","id":1,"method":"tools/call","params":{
                    "name":"search_simulation_skill","arguments":{
                        "scenario_type":"plane_wave_2d","parameters_json":call["parameters_json"],"limit":3}}}
                assert (await client.post("/mcp",headers=headers,json=query)).status_code==401
                headers["Authorization"]="Bearer "+token
                async def invoke(name,args,identifier):
                    response=await client.post("/mcp",headers=headers,json={
                        "jsonrpc":"2.0","id":identifier,"method":"tools/call",
                        "params":{"name":name,"arguments":args}})
                    assert response.status_code==200,response.text
                    chunks=[line[6:] for line in response.text.splitlines() if line.startswith("data: ")]
                    body=json.loads(chunks[0]) if chunks else response.json()
                    assert body["result"].get("isError") is not True
                    return body["result"]["content"][0]["text"]
                from scripts.workflow.skill_handoff_pointer import main as pointer
                from scripts.workflow.skill_handoff_wire import main as wire
                metadata=json.loads(await invoke("search_simulation_skill",
                    dict(scenario_type="plane_wave_2d",parameters_json=call["parameters_json"],limit=3),5))
                assert len(metadata["skills"])==1 and "code" not in metadata["skills"][0]
                pointers=[]
                for branch in ("main","retry"):
                    args=dict(original_context=branch+" original",scenario_type="plane_wave_2d",
                        parameters_json=call["parameters_json"])
                    built=json.loads(await invoke("build_simulation_generation_context",args,2))
                    assert not built["skill_usage"]["fallback"]
                    assert not built["skill_usage"]["actual_use_authenticated"]
                    args.update(pointer(json.dumps(built)))
                    read=await invoke("read_simulation_generation_context",args,3)
                    assert wire(args["original_context"],read)["generation_context"]==built["context"]
                    mismatched=dict(args,original_context="forged original")
                    assert await invoke("read_simulation_generation_context",mismatched,6)=="forged original"
                    pointers.append(args)
                assert skill_authority.record_root_statement(root(key,2,"revoke"))["recorded"]
                for args in pointers:
                    assert await invoke("read_simulation_generation_context",args,4)==args["original_context"]
        finally:
            server.should_exit=True
            try:await asyncio.wait_for(task,timeout=2)
            except asyncio.TimeoutError:
                task.cancel()
                try:await task
                except asyncio.CancelledError:pass
            sock.close()
    asyncio.run(asyncio.wait_for(run(),timeout=12))
