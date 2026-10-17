import asyncio
from pathlib import Path

import httpx
from starlette.applications import Starlette
from starlette.routing import Route

from app import config, portal, skill_library_service as service


class Registry:
    def __init__(self):
        self.routes=[]

    def custom_route(self,path,methods):
        def decorate(fn):
            self.routes.append(Route(path,fn,methods=methods))
            return fn
        return decorate


def test_dashboard_api_adds_only_server_verified_skill_version_link(monkeypatch):
    run_id="11111111-1111-4111-8111-111111111111"
    skill_id="22222222-2222-4222-8222-222222222222"
    row={"id":1,"run_id":run_id,"timed_out":False,"code":"pass"}
    monkeypatch.setattr(config,"SKILL_LIBRARY_ENABLED",True)
    monkeypatch.setattr(portal,"get_executions",lambda **kwargs:[dict(row)])
    monkeypatch.setattr(portal,"get_latest_analysis_by_run_ids",lambda ids:{run_id:{
        "run_id":run_id,"verdict":"normal","max_pressure":1.0,"rms_pressure":0.2,
        "field_shape":[64,64],"has_signal":True,"summary":"finite field"}})
    monkeypatch.setattr(portal,"get_analysis_updates",lambda **kwargs:[{
        "analysis_id":7,"run_id":run_id,"verdict":"normal","max_pressure":1.0,
        "rms_pressure":0.2,"field_shape":[64,64],"has_signal":True,
        "summary":"finite field"}])
    monkeypatch.setattr(portal,"get_stats",lambda:{"total":1})
    monkeypatch.setattr(service.STORE,"read_execution_links",lambda ids:{run_id:{
        "skill_id":skill_id,"version":3,"stage":"recipe_execution_verified",
        "execution_reuse_verified":True,"actual_use_authenticated":False,
        "model_adoption_authenticated":False}})
    registry=Registry();portal.register(registry);app=Starlette(routes=registry.routes)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://isolated") as client:
            response=await client.get("/dashboard/api/executions")
            assert response.status_code==200
            got=response.json()["executions"][0]["skill_reference"]
            assert got["skill_id"]==skill_id and got["version"]==3
            assert got["execution_reuse_verified"] and not got["actual_use_authenticated"]
            analysis=response.json()["executions"][0]["analysis"]
            assert analysis["verdict"]=="normal" and analysis["summary"]=="finite field"
            assert response.json()["analysis_updates"][0]["analysis_id"]==7
    asyncio.run(run())


def test_stage_three_workbench_has_filters_states_and_verified_deep_links():
    root=Path(__file__).resolve().parents[2]
    skills=(root/"app/web/skills.html").read_text(encoding="utf-8")
    design=(root/"app/web/design.js").read_text(encoding="utf-8")
    dashboard=(root/"app/web/dashboard.html").read_text(encoding="utf-8")
    for token in ("skill-search","scenario-filter","status-filter","list-state","permission-state","evidence-grid"):
        assert token in skills
    assert "列表展示不代表本次仿真已调用" in design
    assert "'/skills?skill_id='" in design
    assert "prefers-reduced-motion" in (root/"app/web/design.css").read_text(encoding="utf-8")
    assert "skill_id=" in dashboard and "version=" in dashboard
    assert "X-Admin-Token" in dashboard and 'id="admin-token" type="password"' in dashboard
    assert "detailState" in dashboard and "renderAnalysis" in dashboard
    assert "analysis_since" in dashboard and "lastAnalysisId" in dashboard
    assert "URLSearchParams(location.search)" in skills
    assert "版本参数无效，未加载最新版本" in skills
