"""Default-closed public read-only retrieval and internal fixed-profile handoff.

Public labels/flags/JSON never authorize reuse. Internal generation uses only
server-held validated local profiles or signed evidence and fresh transactional authorization; production
activation, general physics and actual use remain unverified.
"""
import json
import sqlite3

from app import config, skill_library_service
from app.skill_store import SCENARIO_TYPES, normalize_parameters, preconditions_match, skill_fingerprint


def _unique(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("duplicate_parameter")
        obj[key] = value
    return obj


def _nonfinite(value):
    raise ValueError("nonfinite_parameter")


def _request(scenario_type, parameters_json, limit):
    if type(limit) is not int or not 1 <= limit <= 3:
        raise ValueError("limit_must_be_1_to_3")
    if not isinstance(scenario_type, str) or scenario_type not in SCENARIO_TYPES:
        raise ValueError("unsupported_scenario")
    if not isinstance(parameters_json, str) or len(parameters_json) > 4096 or len(parameters_json.encode()) > 4096:
        raise ValueError("parameter_size_limit")
    from app import skill_general_library as general
    try:
        parsed = json.loads(parameters_json, object_pairs_hook=_unique, parse_constant=_nonfinite)
    except (ValueError, TypeError, RecursionError):
        raise ValueError("invalid_parameters") from None
    if general.is_general_query(parsed):
        return general.normalize_query(scenario_type, parameters_json)
    try:
        parameters = normalize_parameters(json.loads(parameters_json,
            object_pairs_hook=_unique, parse_constant=_nonfinite))
    except (ValueError, TypeError, RecursionError):
        raise ValueError("invalid_parameters") from None
    dimensions=parameters.get("dimensions")
    shape=parameters.get("grid_shape")
    if dimensions is not None and (type(dimensions) is not int or dimensions not in (2,3)):
        raise ValueError("invalid_query_dimensions")
    actual_dimension=len(shape) if type(shape) is list else dimensions
    expected_dimension=3 if scenario_type.endswith("_3d") else 2
    if (actual_dimension is not None and actual_dimension!=expected_dimension
            or dimensions is not None and actual_dimension!=dimensions):
        raise ValueError("scenario_geometry_conflict")
    return parameters



def _shortlist(store, db, scenario_type, parameters, limit):
    """Metadata prerequisites only; final authorization needs a fresh decision.

    Prefer exact canonical case identity before recency. Never load code or
    raw evidence. The bounded scan and dependency walk share a read snapshot.
    """
    from app import skill_general_library as general
    if general.is_general_query(parameters):
        fingerprint = general.query_fingerprint(scenario_type, parameters)
        rows = db.execute("""SELECT skill_id,(SELECT MAX(version) FROM skill_versions v
            WHERE v.skill_id=s.skill_id) version FROM simulation_skills s
            WHERE scenario_type=? AND fingerprint=? AND status='active'
            AND quality_level='Q4' AND archived=0 LIMIT ?""",
            (scenario_type,fingerprint,limit)).fetchall()
        return [dict(row) for row in rows]
    fingerprint = skill_fingerprint(scenario_type, parameters)
    columns = """SELECT skill_id, substr(preconditions,1,4097) preconditions,
        (SELECT MAX(version) FROM skill_versions v WHERE v.skill_id=s.skill_id) version
        FROM simulation_skills s WHERE scenario_type=? AND status='active'
        AND quality_level IN ('Q3','Q4') AND archived=0 """
    # Declared applicability is filtered before the 50-row metadata window,
    # retaining the existing recency index and SQLite VM budget. Every selected
    # record still undergoes strict Python bounds and derivation checks.
    predicates=[];filter_args=[]
    def guarded(condition):
        return ("CASE WHEN length(CAST(preconditions AS BLOB))<=4096 "
                "AND json_valid(preconditions) THEN ("+condition+") ELSE 0 END")
    shape=parameters.get("grid_shape")
    dimensions=parameters.get("dimensions")
    if dimensions is None and type(shape) is list:dimensions=len(shape)
    for key,actual in (("dimensions",dimensions),("medium_type",parameters.get("medium_type")),
                       ("solver_mode",parameters.get("solver_mode"))):
        path="'$."+key+"'"
        absent="json_type(preconditions,"+path+") IS NULL"
        correct_type=(type(actual) is int and actual in (2,3)) if key=="dimensions" else type(actual) is str
        if correct_type:
            predicates.append(guarded(absent+" OR (json_type(preconditions,"+path+")=? AND json_extract(preconditions,"+path+")=?)"))
            filter_args.extend(["integer" if key=="dimensions" else "text",actual])
        else:
            predicates.append(guarded(absent))
    for key in ("frequency_hz","dx_m"):
        actual=parameters.get(key)
        values=actual if key=="dx_m" and type(actual) is list else [actual]
        path="'$."+key+"'"
        absent="json_type(preconditions,"+path+") IS NULL"
        if values and all(type(v) in (int,float) for v in values):
            low="'$."+key+"[0]'";high="'$."+key+"[1]'"
            predicates.append(guarded(absent+" OR (json_type(preconditions,"+path+")='array'"
                " AND json_array_length(preconditions,"+path+")=2"
                " AND json_type(preconditions,"+low+") IN ('integer','real')"
                " AND json_type(preconditions,"+high+") IN ('integer','real')"
                " AND json_extract(preconditions,"+low+")>0"
                " AND json_extract(preconditions,"+low+")<=?"
                " AND json_extract(preconditions,"+high+")>=?)"))
            filter_args.extend([min(values),max(values)])
        else:
            predicates.append(guarded(absent))
    import hashlib
    parameter_sha=hashlib.sha256(json.dumps(parameters,sort_keys=True,separators=(",",":"),
                                           ensure_ascii=False,allow_nan=False).encode()).hexdigest()
    predicates.append(guarded("json_type(preconditions,'$.parameters_sha256') IS NULL OR "
                              "(json_type(preconditions,'$.parameters_sha256')='text' AND "
                              "json_extract(preconditions,'$.parameters_sha256')=?)"))
    filter_args.append(parameter_sha)
    columns+=" AND "+" AND ".join(predicates)+" "
    # Separate indexed exact lookup from recent cases: expression ORDER BY on
    # fingerprint would sort the entire eligible catalog before LIMIT.
    rows = db.execute(columns+"AND fingerprint=? LIMIT 1",
                      (scenario_type,*filter_args,fingerprint)).fetchall()
    rows += db.execute(columns+"""AND fingerprint!=?
        ORDER BY quality_level DESC,updated_at DESC,skill_id LIMIT ?""",
        (scenario_type,*filter_args,fingerprint,50-len(rows))).fetchall()
    selected = []
    for row in rows:
        if len(row["preconditions"]) > 4096:
            continue
        try:
            if not preconditions_match(json.loads(row["preconditions"],object_pairs_hook=_unique,parse_constant=_nonfinite),parameters):
                continue
            if not store._inspect_derivation_connection(db,row["skill_id"],row["version"])["references_current"]:
                continue
        except (ValueError, TypeError, RecursionError):
            continue
        selected.append({"skill_id":row["skill_id"],"version":row["version"]})
        if len(selected) == limit:
            break
    return selected


def search(scenario_type, parameters_json, limit=3):
    parameters = _request(scenario_type, parameters_json, limit)
    result = {"skills": [], "fallback": True, "trusted_run": False,
              "runtime_approved": False, "parameters_bound": False,
              "run_bound": False, "reason": "retrieval_disabled"}
    if config.SKILL_LIBRARY_ENABLED is not True or config.SKILL_RETRIEVAL_ENABLED is not True:
        return result
    store = skill_library_service.STORE
    try:
        from app import skill_reference_registry,skill_deadline
        import time
        with skill_deadline.limited(2),store._read_db() as db:
            candidates=_shortlist(store, db, scenario_type, parameters, limit)
            metadata=[]
            for candidate in candidates:
                try:
                    from app import skill_local_profile
                    from app import skill_general_library as general
                    checked=general.verify_connection(store,db,candidate,scenario_type,parameters)
                    if checked is None:
                        checked=skill_local_profile.verify_connection(store,db,candidate,scenario_type,parameters)
                    if checked is None:
                        checked=skill_reference_registry.verified_metadata_connection(
                            store,db,candidate,scenario_type,parameters,time.time_ns())
                    if checked is not None:metadata.append(checked)
                except (ValueError,TypeError,KeyError,IndexError,UnicodeError,OverflowError,RecursionError,ImportError):
                    continue
        # Public metadata is not a generation/execution authorization. The
        # pointer consumer rechecks source and current authority before code.
        return dict(result,skills=metadata,fallback=not bool(metadata),
            reason="fixed_profile_metadata_only" if metadata else
                "provenance_authentication_unavailable" if candidates else "no_applicable_skill")
    except (sqlite3.Error, OSError, ValueError, TypeError, RecursionError,TimeoutError):
        return dict(result, reason="skill_store_unavailable")


def generation_context(original_context, *, scenario_type, parameters_json, reference_evidence=None, _existing_store=False):
    """Internal controller adapter, before generation; not deployed in Dify.

    Search remains publicly read-only. When all three server switches are
    enabled, this controller records one fallback intent in the skill DB.
    This intent does not prove the model ran, a skill was used, or user identity.
    No model, executor or network call occurs here. Optional server-held signed
    reference evidence can select the internal fixed-profile consumer; public
    read-only MCP search never accepts this evidence or returns code.
    """
    import uuid
    from app import skill_generation_decision as decisions
    if not isinstance(original_context, str):
        raise ValueError("context_must_be_text")
    if config.SKILL_INJECTION_ENABLED is not True:
        return {"context": original_context, "skill_usage": {"skills": [], "fallback": True,
                                                             "reason": "injection_disabled"}}
    if not decisions.enabled():
        return {"context": original_context,"skill_usage": {"skills":[],"fallback":True,
                                                            "reason":"retrieval_disabled"}}
    prepared=decisions.prepare(scenario_type=scenario_type,parameters_json=parameters_json,
                              original_context=original_context,_existing_store=_existing_store)
    usage={key:value for key,value in prepared.items() if key not in
           {"context","nonce","decision_id","prepared"}}
    usage["audit_recorded"]=False
    if prepared["prepared"]:
        generation_run_id=str(uuid.uuid4())
        consumed=None
        if reference_evidence is None:
            from app import skill_general_library as general
            general_result=general.consume(decision_id=prepared["decision_id"],nonce=prepared["nonce"],
                generation_run_id=generation_run_id,scenario_type=scenario_type,
                parameters_json=parameters_json,original_context=original_context)
            if general_result["consumed"]:consumed=general_result
        if reference_evidence is None and consumed is None:
            from app import skill_local_profile
            local=skill_local_profile.consume(decision_id=prepared["decision_id"],nonce=prepared["nonce"],
                generation_run_id=generation_run_id,scenario_type=scenario_type,
                parameters_json=parameters_json,original_context=original_context)
            if local["consumed"]:consumed=local
        if consumed is None:
            if reference_evidence is None:
                from app import skill_reference_registry
                reference_evidence=skill_reference_registry.find(scenario_type,parameters_json)
            if reference_evidence is not None:
                from app import skill_reference_consumer
                required={"skill_id","version","approval_document","observation_document","stdout","stderr"}
                if type(reference_evidence) is not dict or set(reference_evidence)!=required:
                    consumed=dict(decisions._base(original_context,"reference_evidence_invalid"),consumed=False)
                else:
                    consumed=skill_reference_consumer.consume(decision_id=prepared["decision_id"],nonce=prepared["nonce"],
                        generation_run_id=generation_run_id,scenario_type=scenario_type,
                        parameters_json=parameters_json,original_context=original_context,**reference_evidence)
            else:
                consumed=decisions.consume(decision_id=prepared["decision_id"],nonce=prepared["nonce"],
                    generation_run_id=generation_run_id,scenario_type=scenario_type,
                    parameters_json=parameters_json,original_context=original_context)
        usage={key:value for key,value in consumed.items() if key not in {"context","consumed"}}
        usage["audit_recorded"]=consumed["consumed"]
        if consumed["consumed"]:
            usage.update(decision_id=prepared["decision_id"],generation_intent_id=generation_run_id)
    return {"context":consumed["context"] if prepared["prepared"] and consumed.get("consumed") else original_context,
            "skill_usage":usage}


def record_fallback_usage(*, run_id, request_nonce, scenario_type, parameters_json, reason):
    """Server-internal audit; not callable through MCP/HTTP search.

    Fixed history path rechecks pre-dispatch context. This association is not
    authenticated execution. Empty hit list only; no caller-provided success.
    """
    import hashlib
    from app import controlled_dispatch, dashboard
    denied = {"audit_recorded":False,"trusted_run":False,"fallback":True}
    if config.SKILL_LIBRARY_ENABLED is not True or config.SKILL_RETRIEVAL_ENABLED is not True:
        return dict(denied,reason="retrieval_disabled")
    try:
        params = _request(scenario_type, parameters_json, 3)
        context = controlled_dispatch.inspect_file(dashboard.DB_PATH,run_id,request_nonce)
        if not context["dispatch_context_matched"]:
            return dict(denied,reason="dispatch_context_unavailable")
        # The only supported audit profile is the fixed initial-pressure
        # plane-wave experiment. Caller queries cannot relabel its scene or
        # invent a continuous frequency. This remains a fallback diagnostic,
        # not proof that retrieval was used or that a run was authenticated.
        if (scenario_type != "plane_wave_2d"
                or params != controlled_dispatch.fixed_retrieval_parameters()):
            return dict(denied,reason="dispatch_query_mismatch")
        digest = hashlib.sha256(json.dumps(params,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()
        return skill_library_service.STORE.record_fallback_retrieval(
            run_id,request_nonce,scenario_type,digest,reason)
    except (ValueError,TypeError,RecursionError,sqlite3.Error,OSError):
        return dict(denied,reason="audit_unavailable")
