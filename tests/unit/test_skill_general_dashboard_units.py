"""Dashboard units follow unique managed execution identity, never field text."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest
from app import dashboard


@pytest.fixture
def history(tmp_path,monkeypatch):
    monkeypatch.setattr(dashboard,"DB_PATH",str(tmp_path/"history.sqlite"))
    dashboard.init_db()
    with dashboard._get_db() as db:
        for run,tool,version in [("general","general_reference_execute","gateway-execution.v1"),
                ("source","automatic_source_execute","gateway-execution.v1"),
                ("legacy","run_jwave_code","gateway-execution.v1"),
                ("duplicate","general_reference_execute","gateway-execution.v1"),
                ("duplicate","general_reference_execute","gateway-execution.v1"),
                ("untrusted","general_reference_execute","legacy")]:
            db.execute("INSERT INTO executions(timestamp,tool_name,code,run_id,record_version) VALUES('now',?,'pass',?,?)",(tool,run,version))
    for run in ("general","source","legacy","duplicate","untrusted","missing"):
        dashboard.record_analysis_event(0,"normal",1.0,0.2,[32,32],True,
            "Pa numerical_source_normalization cannot override identity",None,None,"","",run)


def test_both_analysis_interfaces_derive_units_from_unique_execution(history):
    runs=["general","source","legacy","duplicate","untrusted","missing"]
    expected={"general":"numerical_source_normalization","source":"numerical_source_normalization","legacy":"Pa","duplicate":"unknown","untrusted":"unknown","missing":"unknown"}
    latest=dashboard.get_latest_analysis_by_run_ids(runs)
    assert {run:row["units"] for run,row in latest.items()}==expected
    updates=dashboard.get_analysis_updates()
    assert {row["run_id"]:row["units"] for row in updates}==expected
    assert {row["run_id"]:row["units"] for row in dashboard.get_analysis_updates(since_id=1)}=={k:v for k,v in expected.items() if k!="general"}


def test_dashboard_formats_each_metric_with_its_bound_unit():
    source=(Path(__file__).resolve().parents[2]/"app/web/dashboard.html").read_text(encoding="utf-8")
    assert "row('最大压力 (Pa)'" not in source
    assert "row('最大场量（各自单位）'" in source
    assert "Number(a.max_pressure).toPrecision(6) + ' ' + analysisUnit(a)" in source
    assert "Number(a.rms_pressure).toPrecision(6) + ' ' + analysisUnit(a)" in source
    assert "Number(r.analysis.max_pressure).toPrecision(6) + ' ' + analysisUnit(r.analysis)" in source
    node=shutil.which("node")
    if node is None:return
    functions=source[source.index("function analysisUnit(a)"):source.index("function renderRow(r)")]
    functions+=source[source.index("function _cmpMaxP(r)"):source.index("function _cmpDur(r)")]
    script=functions+"\nconsole.log(JSON.stringify(['numerical_source_normalization','Pa','unknown'].map(units=>_cmpMaxP({analysis:{max_pressure:1,units}}))));"
    result=subprocess.run([node,"-e",script],capture_output=True,text=True,encoding="utf-8",check=True)
    assert json.loads(result.stdout)==["1.00000 数值源归一化","1.00000 Pa","1.00000 单位未知"]
