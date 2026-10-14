"""Repeatable synthetic metadata retrieval baseline, never physics or authorization.

Creates and removes its own temporary SQLite DB; fixture active/Q4 labels are
not trusted skills. Public search remains closed. No execution or model call.
"""
import json
import sqlite3
import tempfile
from pathlib import Path
from app.skill_store import SkillStore, normalize_parameters
from app.skill_retrieval import _shortlist

def run():
    with tempfile.TemporaryDirectory(prefix="acou-retrieval-benchmark-") as directory:
        store=SkillStore(Path(directory)/"fixtures.db")
        def add(label,frequency,*,status="active",archived=False,refs=(),bounds=None,scene="plane_wave_2d",dx=0.00025,grid=128):
            sid=store.create_candidate(scenario_type=scene,state_text=label,
                normalized_params={"frequency_hz":frequency,"dx_m":dx,"domain_N":[grid,128]},
                code_template="pass",postconditions={},physics_evidence={},source_run_id=label,
                validator_version="synthetic-fixture",quality_level="Q2",
                preconditions=bounds or {"dimensions":2,"frequency_hz":[frequency,frequency]},
                reference_skill_ids=list(refs))
            with store._db_existing_write() as db:
                db.execute("UPDATE simulation_skills SET status=?,archived=?,quality_level='Q4',updated_at=? WHERE skill_id=?",
                           (status,int(archived),"2000-01-01" if label=="exact-old" else "2026-01-01",sid))
            return sid
        exact=add("exact-old",500000,bounds={"dimensions":2,"frequency_hz":[490000,510000],"dx_m":[0.0002,0.0003]})
        for i in range(60):add("irrelevant-"+str(i),1000000+i)
        for i in range(60):
            add("irrelevant-dx-"+str(i),505000,dx=0.001,grid=128+i,
                bounds={"dimensions":2,"frequency_hz":[490000,510000],"dx_m":[0.001,0.001]})
        ranged=add("range",700000,bounds={"dimensions":2,"frequency_hz":[690000,710000]})
        archived=add("archived",800000,archived=True)
        questioned=add("questioned",900000,status="questioned")
        upstream=add("upstream",950000,status="questioned")
        derived=add("derived",960000,refs=[upstream])
        scene=add("other-scene",500000,scene="point_source_2d")
        cases=[
            ("exact_older_than_recent_window",{"frequency_hz":500000,"dx_m":0.00025,"domain_N":[128,128]},[exact]),
            ("equivalent_units",{"frequency_mhz":0.5,"dx_mm":0.25,"grid_size":[128,128]},[exact]),
            ("older_nonexact_range",{"frequency_hz":505000,"dx_m":0.00025,"domain_N":[128,128]},[exact]),
            ("declared_range",{"frequency_hz":705000,"domain_N":[128,128]},[ranged]),
            ("outside_range",{"frequency_hz":710001,"domain_N":[128,128]},[]),
            ("archived",{"frequency_hz":800000,"domain_N":[128,128]},[]),
            ("questioned",{"frequency_hz":900000,"domain_N":[128,128]},[]),
            ("derived_questioned",{"frequency_hz":960000,"domain_N":[128,128]},[]),
            ("missing_dimension",{"frequency_hz":705000},[]),
        ]
        results=[];true_positive=0;expected_total=0;false_positive=0
        for label,params,expected in cases:
            with store._read_db() as db:
                found=_shortlist(store,db,"plane_wave_2d",normalize_parameters(params),3)
            actual=[row["skill_id"] for row in found]
            true_positive+=len(set(actual)&set(expected));expected_total+=len(expected)
            false_positive+=len(set(actual)-set(expected))
            results.append({"case":label,"expected_count":len(expected),"returned_count":len(actual),
                            "passed":set(actual)==set(expected),"version_bound":all(row["version"]==1 for row in found)})
        with store._read_db() as db:
            fixture_count=db.execute("SELECT count(*) FROM simulation_skills").fetchone()[0]
        return {"kind":"synthetic_metadata_preselection_baseline","case_count":len(cases),
                "fixture_count":fixture_count,"cutoff":3,"preselection_recall":true_positive/expected_total,
                "false_positive_count":false_positive,"cases":results,
                "passed":all(row["passed"] and row["version_bound"] for row in results),
                "authenticated":False,"actual_use_authenticated":False,"physics_verified":False,
                "production_recall_accepted":False,"public_code_returned":False}

if __name__=="__main__":
    print(json.dumps(run(),ensure_ascii=False,sort_keys=True))
