import json
import pytest
from app import config,skill_retrieval,skill_reference_registry as registry,skill_authority
from tests.unit.test_skill_reference_registry import proof
from tests.unit.test_skill_reference_consumer import available
from tests.unit.test_skill_host_intake import ready
from tests.unit.test_skill_authority import prepared,root
def test_public_search_returns_verified_metadata_without_code_or_writes(proof,monkeypatch):
    store,key,call,evidence=proof
    assert registry.register(evidence)["recorded"]
    monkeypatch.setattr(config,"SKILL_INJECTION_ENABLED",False)
    with store._read_db() as db:
        before={name:db.execute("SELECT count(*) FROM "+name).fetchone()[0] for name in
            ("skill_events","skill_generation_decisions","skill_generation_consumptions","skill_retrievals")}
    got=skill_retrieval.search("plane_wave_2d",call["parameters_json"])
    assert len(got["skills"])==1 and not got["fallback"] and got["reason"]=="fixed_profile_metadata_only"
    assert got["skills"][0]["skill_id"]==call["skill_id"]
    assert not any(got[k] for k in ("trusted_run","runtime_approved","run_bound","parameters_bound"))
    assert set(got["skills"][0])=={"skill_id","version","scenario_type","scope","parameters_sha256",
        "code_sha256","source_run_id","quality_level","status"}
    with store._read_db() as db:
        assert before=={name:db.execute("SELECT count(*) FROM "+name).fetchone()[0] for name in before}
    assert skill_authority.record_root_statement(root(key,2,"revoke"))["recorded"]
    assert skill_retrieval.search("plane_wave_2d",call["parameters_json"])["skills"]==[]
@pytest.mark.parametrize("fault",["parameters","capsule_hash","candidate","archived","expired"])
def test_stale_or_unverified_metadata_not_returned(proof,monkeypatch,fault):
    store,key,call,evidence=proof
    assert registry.register(evidence)["recorded"]
    params=call["parameters_json"]
    if fault=="parameters":
        data=json.loads(params);data["initial_sigma_m"]=0.004;params=json.dumps(data)
    elif fault=="expired":
        monkeypatch.setattr(registry.time,"time_ns",lambda:181_000_000_000)
    else:
        with store._db_existing_write() as db:
            if fault=="capsule_hash":
                db.execute("DROP TRIGGER skill_reference_capsules_no_update")
                db.execute("UPDATE skill_reference_capsules SET capsule_sha256=?",("0"*64,))
            elif fault=="candidate":db.execute("UPDATE simulation_skills SET status='candidate'")
            else:db.execute("UPDATE simulation_skills SET archived=1")
    assert skill_retrieval.search("plane_wave_2d",params)["skills"]==[]

def test_portal_backend_search_is_bounded_read_only_and_not_management(proof):
    import asyncio
    import httpx
    from starlette.applications import Starlette
    from starlette.routing import Route
    from app import skill_api
    store,key,call,evidence=proof
    assert registry.register(evidence)["recorded"]
    class Routes:
        def __init__(self):self.routes=[]
        def custom_route(self,path,methods):
            def wrapper(fn):
                self.routes.append(Route(path,fn,methods=methods));return fn
            return wrapper
    routes=Routes();skill_api.register(routes);app=Starlette(routes=routes.routes)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://isolated") as client:
            body=dict(scenario_type="plane_wave_2d",parameters=json.loads(call["parameters_json"]),limit=3)
            response=await client.post("/skills/search",json=body)
            assert response.status_code==200
            assert len(response.json()["skills"])==1
            assert response.headers["cache-control"]=="no-store"
            assert (await client.post("/skills/search",json=dict(body,limit=4))).status_code==400
            assert (await client.post("/skills/search",json=dict(body,code="arbitrary"))).status_code==400
            assert (await client.post("/skills/search",content="x"*4097)).status_code==413
            assert (await client.post("/skills/search",content='{"scenario_type":"x","scenario_type":"y"}')).status_code==400
    with store._read_db() as db:
        before=db.execute("SELECT count(*) FROM skill_events").fetchone()[0]
        consumed=db.execute("SELECT count(*) FROM skill_generation_consumptions").fetchone()[0]
    asyncio.run(run())
    with store._read_db() as db:
        assert db.execute("SELECT count(*) FROM skill_events").fetchone()[0]==before
        assert db.execute("SELECT count(*) FROM skill_generation_consumptions").fetchone()[0]==consumed
