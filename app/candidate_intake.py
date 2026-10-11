"""Internal Q2 intake from recorded execution, never authenticated learning."""
import hashlib
import json
import sqlite3
import re
from pathlib import Path
from app import config, dashboard, skill_library_service
from app.skill_store import SCENARIO_TYPES, normalize_parameters, validate_candidate_content
from app.physics_gate import _unique, _reject_constant, assess_quality

FALSE_TRUST = dict(trusted_run=False, runtime_approved=False,run_bound=False,parameters_bound=False)
ALLOWED = {"sound_speed","density","source_frequency","domain_N","domain_dx","cfl",
           "pml_size","t_end","source_index","sensor_index","solver_mode","medium_type",
           "source_kind","initial_pressure_pa","initial_sigma_m","initial_velocity",
         "source_geometry","sensor_indices","smooth_initial"}
def enabled():
    return config.SKILL_LIBRARY_ENABLED is True and config.SKILL_LEARNING_ENABLED is True

def prepare(code,scenario_type,params_json):
    """Capture declared request before dispatch; parameters remain unverified."""
    if not enabled():
        return None
    try:
        if scenario_type not in SCENARIO_TYPES or type(params_json) is not str or len(params_json.encode())>4096:
            return None
        params=json.loads(params_json,object_pairs_hook=_unique,parse_constant=_reject_constant)
        if type(params) is not dict or not set(params)<=ALLOWED:
            return None
        if params.get("solver_mode") not in {"time_domain","frequency_domain"}:
            return None
        if params.get("medium_type") not in {"water","homogeneous_water","homogeneous","heterogeneous","tissue","air"}:
            return None
        shape=params.get("domain_N")
        if (type(shape) is not list or len(shape) not in (2,3)
                or any(type(v) is not int or v<32 for v in shape)
                or len(shape)!=(3 if scenario_type.endswith("_3d") else 2)):
            return None
        if "source_kind" in params and params["source_kind"] not in {"continuous_sinusoid","gaussian_initial_pressure"}:
            return None
        if scenario_type=="initial_pressure_2d" and params.get("source_kind")!="gaussian_initial_pressure":
            return None
        if params.get("source_kind")=="gaussian_initial_pressure" and scenario_type!="initial_pressure_2d":
            return None
        if "source_geometry" in params and params["source_geometry"] not in {"planar_x_uniform_y","radial_gaussian","point"}:
            return None
        if "smooth_initial" in params and type(params["smooth_initial"]) is not bool:return None
        if "sensor_indices" in params:
            points=params["sensor_indices"];pml=params.get("pml_size")
            if (type(points) is not list or not 1<=len(points)<=3 or type(pml) is not int
                    or any(type(point) is not list or len(point)!=len(shape)
                        or any(type(v) is not int or not pml<=v<n-pml for v,n in zip(point,shape))
                        for point in points)):return None
        normalized=normalize_parameters({("source_type" if k=="source_kind" else "density_kg_m3" if k=="density" else
            "t_end_s" if k=="t_end" else k):v for k,v in params.items()})
        normalized["dimensions"]=len(params["domain_N"])
        validate_candidate_content(code,normalized)
        return {"version":"candidate-request.v1","scenario_type":scenario_type,
            "params_json":json.dumps(params,sort_keys=True,separators=(",",":"),allow_nan=False),
            "code_sha256":hashlib.sha256(code.encode()).hexdigest()}
    except (ValueError,TypeError,OverflowError,RecursionError,UnicodeError):
        return None

def complete(run_id):
    """Ignore caller analysis/grades; reread unique server-owned final code/output."""
    denied=dict(FALSE_TRUST,recorded=False)
    if not enabled():
        return dict(denied,reason="candidate_accumulation_disabled")
    if type(run_id) is not str or not 1<=len(run_id)<=128:
        return dict(denied,reason="invalid_run_reference")
    db=None
    try:
        db=sqlite3.connect(Path(dashboard.DB_PATH).resolve().as_uri()+"?mode=ro",uri=True,timeout=1)
        db.row_factory=sqlite3.Row
        db.set_progress_handler(lambda:1,1_000_000)
        db.execute("BEGIN")
        # Bound transferred values before parsing/hashing; LIMIT alone does
        # not bound a corrupt/legacy row or a missing-index historical scan.
        rows=db.execute("""SELECT substr(code,1,20001) code,substr(code_sha256,1,65) code_sha256,
            substr(stdout,1,400001) stdout,exit_code,timed_out,
            substr(record_version,1,129) record_version,
            substr(executor_evidence_json,1,16001) executor_evidence_json
            FROM executions WHERE run_id=? LIMIT 2""",(run_id,)).fetchall()
        if len(rows)!=1:
            return dict(denied,reason="missing_or_ambiguous_execution")
        row=dict(rows[0])
    except (sqlite3.Error,OSError,ValueError):
        return dict(denied,reason="execution_store_unavailable")
    finally:
        if db is not None:db.close()
    storing=False
    try:
        if row["record_version"]!="gateway-execution.v1":
            return dict(denied,reason="legacy_unverified_execution")
        evidence_raw=row["executor_evidence_json"] or "{}"
        if type(evidence_raw) is not str or len(evidence_raw.encode())>16000:
            return dict(denied,reason="intake_context_mismatch")
        evidence=json.loads(evidence_raw,
                            object_pairs_hook=_unique,parse_constant=_reject_constant)
        if type(evidence) is not dict:
            return dict(denied,reason="missing_intake_context")
        context=evidence.get("candidate_intake")
        if type(context) is not dict or set(context)!={"version","scenario_type","params_json","code_sha256"}:
            return dict(denied,reason="missing_intake_context")
        code=row["code"]
        if (type(code) is not str or len(code.encode())>20000
                or hashlib.sha256(code.encode()).hexdigest()!=row["code_sha256"]
                or context!=prepare(code,context["scenario_type"],context["params_json"])):
            return dict(denied,reason="intake_context_mismatch")
        if type(row["stdout"]) is not str or len(row["stdout"].encode())>400000:
            return dict(denied,reason="candidate_output_budget")
        # Analysis from recorded output, without images, model calls or caller metrics.
        from app import analysis
        if (row["stdout"].count(analysis._FIELD_START)!=1
                or row["stdout"].count(analysis._FIELD_END)!=1):
            return dict(denied,reason="candidate_field_payload_ambiguous")
        match = re.search(re.escape(analysis._FIELD_START) + r"\s*(\{.*?\})\s*" +
                          re.escape(analysis._FIELD_END), row["stdout"], re.DOTALL)
        payload = json.loads(match.group(1), object_pairs_hook=_unique,
                             parse_constant=_reject_constant) if match else None
        if type(payload) is not dict or "data" not in payload:
            return dict(denied,reason="candidate_quality_insufficient")
        def numeric_tree(value, depth=0):
            if depth > 5:
                return False
            if type(value) is list:
                return bool(value) and all(numeric_tree(item, depth+1) for item in value)
            return type(value) in (int,float)
        if not numeric_tree(payload["data"]):
            return dict(denied,reason="candidate_quality_insufficient")
        matrix,shape=analysis._to_float_matrix(payload["data"]) if payload else (None,None)
        if matrix is None:
            return dict(denied,reason="candidate_quality_insufficient")
        params=json.loads(context["params_json"])
        expected=params["domain_N"]
        actual=shape[:-1] if len(shape)==len(expected)+1 and shape[-1]==1 else shape
        declared=payload.get("shape")
        if type(declared) is list and len(declared)==len(expected)+1 and declared[-1]==1:
            declared=declared[:-1]
        stride=payload.get("downsample",1)
        if (type(stride) is not int or not 1<=stride<=64
                or type(declared) is not list
                or any(type(v) is not int for v in declared)
                or declared not in (expected,actual)
                or actual!=[(size+stride-1)//stride for size in expected]):
            return dict(denied,reason="candidate_output_geometry_unavailable")
        metrics=analysis._compute_metrics(matrix,shape)
        # Q2 checks the declared full grid only after export geometry is coherent;
        # this does not authenticate its correspondence to executed configuration.
        metrics["field_shape"]=expected
        metrics["verdict"]=analysis._verdict(row["exit_code"],bool(row["timed_out"]),metrics)
        quality=assess_quality(exit_code=row["exit_code"],timed_out=bool(row["timed_out"]),
                               analysis=metrics,params_json=context["params_json"])
        if quality["quality_level"]!="Q2":
            return dict(denied,reason="candidate_quality_insufficient")
        from app.physics_timing import assess_sensor_timing
        if assess_sensor_timing(analysis._extract_sensor(row["stdout"]))["status"] == "insufficient":
            return dict(denied,reason="candidate_window_insufficient")
        params=json.loads(context["params_json"])
        normalized=normalize_parameters({("source_type" if k=="source_kind" else "density_kg_m3" if k=="density" else
            "t_end_s" if k=="t_end" else k):v for k,v in params.items()})
        normalized["dimensions"]=len(params["domain_N"])
        bounds={"dimensions":len(params["domain_N"]),"solver_mode":params["solver_mode"],
                "medium_type":params["medium_type"],
                "parameters_sha256":hashlib.sha256(json.dumps(normalized,sort_keys=True,
                    separators=(",",":"),ensure_ascii=False,allow_nan=False).encode()).hexdigest()}
        if "source_frequency" in params:
            bounds["frequency_hz"]=[params["source_frequency"],params["source_frequency"]]
        storing=True
        skill_id=skill_library_service.STORE.create_candidate(
            scenario_type=context["scenario_type"],state_text="已记录仿真的未认证候选案例",
            normalized_params=normalized,code_template=code,
            preconditions=bounds,
            postconditions={"has_signal":True},
            physics_evidence=dict(FALSE_TRUST,scope="gateway_record_candidate_only",
                stdout_sha256=hashlib.sha256(row["stdout"].encode()).hexdigest(),
                parameters_sha256=hashlib.sha256(context["params_json"].encode()).hexdigest(),
                code_sha256=row["code_sha256"],physics_checks=quality["physics_checks"]),
            source_run_id=run_id,validator_version="candidate-intake.v1",quality_level="Q2",
            idempotent_source_run=True)
        return dict(denied,recorded=True,reason="candidate_only",skill_id=skill_id)
    except (sqlite3.Error,OSError):
        return dict(denied,reason="candidate_store_unavailable")
    except (ValueError,TypeError,OverflowError,RecursionError,KeyError,UnicodeError):
        return dict(denied,reason="candidate_content_rejected" if storing else "candidate_prerequisites_failed")


def recover_pending(limit=20, after_execution_id=0):
    """Internal bounded recovery; reuse stored context, never rerun a simulation.

    Scan at most limit history rows using the primary-key cursor. Missing
    context is skipped; a storage/read failure retains the last safe cursor.
    No arbitrary supplied parameters, code, database path or trust inputs.
    """
    result = dict(FALSE_TRUST, examined=0, recorded=0, rejected=0,
                  next_after_execution_id=after_execution_id)
    if not enabled():
        return dict(result,reason="candidate_accumulation_disabled")
    if (type(limit) is not int or not 1<=limit<=50
            or type(after_execution_id) is not int or not 0<=after_execution_id<=2**63-1):
        raise ValueError("candidate_scan_limit")
    db=None
    try:
        db=sqlite3.connect(Path(dashboard.DB_PATH).resolve().as_uri()+"?mode=ro",uri=True,timeout=1)
        db.set_progress_handler(lambda:1,1_000_000)
        rows=db.execute("SELECT id,run_id FROM executions WHERE id>? ORDER BY id LIMIT ?",
                        (after_execution_id,limit)).fetchall()
    except (sqlite3.Error,OSError,ValueError):
        return dict(result,reason="execution_store_unavailable")
    finally:
        if db is not None:db.close()
    for execution_id,run_id in rows:
        outcome=complete(run_id)
        result["examined"]+=1
        result["recorded" if outcome["recorded"] else "rejected"]+=1
        if outcome["reason"] in {"candidate_store_unavailable","execution_store_unavailable"}:
            return dict(result,reason=outcome["reason"])
        result["next_after_execution_id"]=execution_id
    return dict(result,reason="candidate_scan_complete")
