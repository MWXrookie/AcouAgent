"""Reanalyse retained isolated benchmark into a temporary Q2 library only.

No production DB, simulation, feature toggle or authenticated trust changes.
Input is diagnostic evidence, never an authenticated execution receipt.
"""
import hashlib
import json
import tempfile
from pathlib import Path
from app import physics_consistency as gate
from app.skill_store import SkillStore, normalize_parameters
from app.physics_gate import _unique, _reject_constant
from scripts.validation.consistency_experiment import recipe

EVIDENCE = Path("auxiliary/reference/candidate-intake-20261009/physics-final.jsonl")

def run():
    if EVIDENCE.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("evidence_budget")
    records = {}
    profiles = {"base": (64, .1, False), "grid": (128, .1, False),
                "time": (64, .05, False), "equivalent": (64, .1, True)}
    for line in EVIDENCE.read_text(encoding="utf-8").splitlines():
        item = json.loads(line, object_pairs_hook=_unique, parse_constant=_reject_constant)
        if "record" not in item:
            continue
        record = item["record"]
        name = record["case"]
        if name not in profiles or name in records:
            raise ValueError("ambiguous_experiment")
        code = recipe(*profiles[name])
        if (record["code_sha256"] != hashlib.sha256(code.encode()).hexdigest()
                or type(record["exit_code"]) is not int or record["exit_code"] != 0
                or record["timed_out"] is not False):
            raise ValueError("recipe_or_execution_mismatch")
        data = json.loads(record["stdout"], object_pairs_hook=_unique, parse_constant=_reject_constant)
        size, cfl, equivalent = profiles[name]
        expected = dict(domain_N=[size,size], dx_m=.032/size,
            sound_speed_m_s=1500.0, density_kg_m3=1000.0,
            pml_size=0, cfl=cfl, t_end_s=2.2e-6)
        if data["parameters"] != expected or data["pressure_sample_origin"] != "after_first_update":
            raise ValueError("configuration_mismatch")
        if abs(data["sample_time_s"]-(data["output_index"]+1)*data["dt_s"]) > 1e-12:
            raise ValueError("sample_time_mismatch")
        records[name] = (record, data)
    if set(records) != set(profiles):
        raise ValueError("missing_experiment")
    a,b,c,d = [records[name][1] for name in profiles]
    from app.controlled_consistency import assess
    result=assess({name:dict(code_sha256=record["code_sha256"],stdout=record["stdout"])
        for name,(record,_) in records.items()})
    checks=result["checks"]
    if not result["numeric_pass"]:
        raise ValueError("numerical_bundle_failed")
    params = normalize_parameters(dict(a["parameters"], solver_mode="time_domain",
        medium_type="homogeneous",source_type="gaussian_initial_pressure",
        initial_pressure_pa=1.0, initial_sigma_m=.002, initial_velocity="zero"))
    bounds = dict(dimensions=2,solver_mode="time_domain",medium_type="homogeneous",
        parameters_sha256=hashlib.sha256(json.dumps(params,sort_keys=True,
            separators=(",",":"),ensure_ascii=False,allow_nan=False).encode()).hexdigest())
    evidence = dict(gate.FALSE_TRUST, scope="isolated_reanalysis_candidate_only",
        physics_checks=checks, raw_output_sha256={name:hashlib.sha256(record["stdout"].encode()).hexdigest()
            for name,(record,_) in records.items()})
    with tempfile.TemporaryDirectory(prefix="acou-consistency-candidate-") as directory:
        store = SkillStore(Path(directory)/"skills.db")
        args = dict(scenario_type="initial_pressure_2d",state_text="周期均匀介质高斯初压隔离候选",
            normalized_params=params,preconditions=bounds,code_template=recipe(),
            postconditions={"numerical_consistency":True},physics_evidence=evidence,
            source_run_id="isolated-reanalysis-"+hashlib.sha256(EVIDENCE.read_bytes()).hexdigest()[:24],
            validator_version="consistency-candidate-check.v2",quality_level="Q2",idempotent_source_run=True)
        sid = store.create_candidate(**args)
        assert store.create_candidate(**args) == sid
        skill = store.get_skill(sid)
        assert skill["status"] == "candidate" and skill["quality_level"] == "Q2" and skill["success_count"] == 1
        result = dict(scope=evidence["scope"], checks=checks,status=skill["status"],
            quality_level=skill["quality_level"],success_count=skill["success_count"],
            temporary_database_removed_on_return=True, **gate.FALSE_TRUST)
    return result

if __name__ == "__main__":
    print(json.dumps(run(),ensure_ascii=False,allow_nan=False))
