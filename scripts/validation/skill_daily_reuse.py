"""Read-only stage-two evidence checks; no simulation, learning or trust promotion.

A trace file alone is NOT authoritative. verify_runtime also rechecks the
configured SQLite decision, current skill and managed execution history.
The operator must separately compare trace nodes with the native Dify run DB.
"""
import json
from app import execution,skill_local_profile as local,skill_library_service as service
from scripts.validation.host_forward_signature import canonical

BRANCHES={
 "main":dict(adapter="stage1_params_main",reader="local_basic_main_reader",
             wire="local_basic_main_wire",generator="1783991079665",runner="5655556822808",
             recipe="daily_main_recipe",code="daily_main_code"),
 "retry":dict(adapter="stage1_params_retry",reader="local_basic_retry_reader",
              wire="local_basic_retry_wire",generator="1786095555003",runner="1786095555004",
              recipe="daily_retry_recipe",code="daily_retry_code")}
PREFIX="\n\nValidated local fixed-profile reference (parameters must remain unchanged):\n"

def _one(value):
    if type(value) is not list or len(value)!=1 or type(value[0]) is not dict:
        raise ValueError("single_tool_result_required")
    return value[0]
def _text(value):
    if type(value) is list and len(value)==1:value=value[0]
    if type(value) is not str:raise ValueError("text_required")
    return value

def inspect_trace(trace,task,branch="main"):
    if branch not in BRANCHES:raise ValueError("branch")
    result=dict(task_id=task["task_id"],branch=branch,passed=False,
                model_adoption_verified=False,execution_reuse_verified=False,
                model_call_avoided=False,direct_recipe_execution_verified=False,
                actual_use_authenticated=False,trace_authority_checked=False,
                reason="incomplete_trace")
    try:
        if trace.get("complete") is not True:return result
        events=trace["events"]
        starts=[e["data"] for e in events if e.get("event")=="workflow_started"]
        if len(starts)!=1:raise ValueError("single_native_run_required")
        result["workflow_run_id"]=starts[0]["id"]
        if type(events) is not list or len(events)>100:raise ValueError("trace_budget")
        by={}
        for ev in events:
            if ev.get("event")!="node_finished":continue
            data=ev["data"];node=data["node_id"]
            if node in by:raise ValueError("duplicate_node")
            by[node]=data
        # Every observed search retains its entire result list and explicit fallback.
        for branch_ids in BRANCHES.values():
            search_id=branch_ids["adapter"].replace("params_","")+"_search"
            if search_id in by:
                response=_one(by[search_id]["outputs"]["json"])
                if "skills" in response and (type(response["skills"]) is not list or len(response["skills"])>3):
                    raise ValueError("retrieval_limit")
        ids=BRANCHES[branch];reader=by.get(ids["reader"])
        if by[ids["adapter"]]["inputs"].get("original_query")!=task["prompt"]:
            raise ValueError("original_task_binding_changed")
        delivered=False;metadata=None
        if reader:
            original=reader["inputs"]["original_context"]
            context=_text(reader["outputs"]["text"])
            delivered=context.startswith(original+PREFIX) and context!=original
            if delivered:metadata=local._json(context[len(original+PREFIX):])
        result["reference_delivered"]=delivered
        if not task["expected_reference"]:
            # A fallback is successful only if generation receives the untouched original.
            adapter=by.get(ids["adapter"])
            generator=by.get(ids["generator"])
            if delivered:raise ValueError("negative_reference_delivery")
            if not adapter or not generator:raise ValueError("fallback_not_reached")
            prefix=ids["adapter"].replace("params_","")
            guard=by[prefix+"_guard"]["outputs"]
            original=by[ids["adapter"]]["inputs"]["parameters_json"]
            merge=by[ids["reader"].replace("reader","merge")]["outputs"]["output"]
            if guard["generation_context"]!=original or guard["skill_injected"] is not False or merge!=original:
                raise ValueError("fallback_context_changed")
            if reader and _text(reader["outputs"]["text"])!=original:raise ValueError("fallback_reader_changed")
            result.update(passed=True,reason="negative_original_context_preserved")
            return result
        if not delivered:raise ValueError("missing_reference")
        adapter=by[ids["adapter"]]["outputs"]
        if local._json(adapter["retrieval_parameters_json"])!=task["parameters"]:
            raise ValueError("task_parameters_changed")
        code=metadata["code"]
        recipe=by.get(ids["recipe"]);combined=by.get(ids["code"])
        generator=by.get(ids["generator"])
        if recipe and recipe.get("outputs",{}).get("ready") is True:
            if generator is not None:raise ValueError("direct_route_also_called_model")
            if (recipe["outputs"].get("code")!=code or not combined
                    or execution._clean_code(combined["outputs"].get("output"))!=code):
                raise ValueError("direct_recipe_code_mismatch")
            reuse_mode="direct_fixed_recipe"
        else:
            if not generator or execution._clean_code(generator["outputs"]["text"])!=code:
                raise ValueError("model_code_not_exact_reference")
            if combined and execution._clean_code(combined["outputs"].get("output"))!=code:
                raise ValueError("aggregated_code_mismatch")
            reuse_mode="model_adopted_reference"
        if by[ids["wire"]]["outputs"]["generation_context"]!=context:
            raise ValueError("wire_did_not_deliver")
        run=_one(by[ids["runner"]]["outputs"]["json"])
        if (run["final_code"]!=code or execution._clean_code(by[ids["runner"]]["inputs"]["code"])!=code
                or run["exit_code"]!=0 or run["timed_out"] or run["stderr"]):
            raise ValueError("execution_mismatch_or_failed")
        end=[e["data"] for e in events if e.get("event")=="workflow_finished"]
        if len(end)!=1 or end[0]["status"]!="succeeded":raise ValueError("workflow_not_completed")
        result.update(passed=True,reason="trace_exact_reference_execution",
            reuse_mode=reuse_mode,model_adoption_observed=reuse_mode=="model_adopted_reference",
            model_call_avoided=reuse_mode=="direct_fixed_recipe",
            execution_run_id=run["run_id"],
            skill_id=metadata["skill_id"],skill_version=metadata["version"],
            code_sha256=metadata["code_sha256"],parameters_sha256=metadata["parameters_sha256"],
            decision_id=reader["inputs"]["decision_id"],
            generation_run_id=reader["inputs"]["generation_run_id"],
            workflow_run_id=next(e["data"]["id"] for e in events if e.get("event")=="workflow_started"))
        return result
    except (ValueError,TypeError,KeyError,IndexError,StopIteration) as exc:
        result["reason"]=str(exc);return result

def verify_runtime(trace,task,branch="main"):
    result=inspect_trace(trace,task,branch)
    if not result["passed"] or not task["expected_reference"]:return result
    try:
        nodes={e["data"]["node_id"]:e["data"] for e in trace["events"] if e.get("event")=="node_finished"}
        reader=nodes[BRANCHES[branch]["reader"]]
        with service.STORE._read_db() as db:
            current=local.verify_connection(service.STORE,db,dict(skill_id=result["skill_id"],version=result["skill_version"]),
                                           task["scenario_type"],task["parameters"])
            if current is None:raise ValueError("current_skill_unavailable")
            row=db.execute("""SELECT d.*,c.consumed_ns,c.metadata_current_json refs
                FROM skill_generation_decisions d JOIN skill_generation_consumptions c ON c.decision_id=d.decision_id
                WHERE d.decision_id=? AND c.generation_run_id=?""",
                (result["decision_id"],result["generation_run_id"])).fetchone()
            if (row is None or not row["created_ns"]<=row["consumed_ns"]<row["expires_ns"]
                    or local._json(row["refs"])!=[current]
                    or row["scenario_type"]!=task["scenario_type"]
                    or row["parameters_sha256"]!=result["parameters_sha256"]
                    or row["context_sha256"]!=local.pins.digest(reader["inputs"]["original_context"])):
                raise ValueError("committed_decision_mismatch")
            context=local._context(reader["inputs"]["original_context"],current)
            if context!=_text(reader["outputs"]["text"]):raise ValueError("authority_reference_mismatch")
        from app.skill_execution_history import checked_daily_history
        capsule,code,numeric=checked_daily_history(result["execution_run_id"])
        if local.pins.digest(code)!=result["code_sha256"] or result["execution_run_id"]==current["source_run_id"]:
            raise ValueError("actual_execution_not_new")
        raw_run=_one(nodes[BRANCHES[branch]["runner"]]["outputs"]["json"])
        if capsule["stdout"]!=raw_run["stdout"]:raise ValueError("history_raw_output_mismatch")
        result.update(execution_reuse_observed=True,authority_db_checked=True,
            numeric_pass=numeric["numeric_pass"],relative_errors=numeric.get("relative_errors"),
            maximum_relative_error=numeric.get("maximum_relative_error"))
    except local.ERRORS as exc:result.update(passed=False,execution_reuse_verified=False,reason=str(exc))
    return result



def finalize_evidence(runtime,native,expected_reference):
    result=dict(runtime)
    matched=(runtime.get("passed")is True and native.get("trace_authority_checked")is True
             and native.get("workflow_run_id")==runtime.get("workflow_run_id"))
    if expected_reference:
        matched=matched and runtime.get("authority_db_checked")is True and runtime.get("execution_reuse_observed")is True and runtime.get("numeric_pass")is True
        if runtime.get("reuse_mode")=="direct_fixed_recipe":
            matched=matched and native.get("model_nodes_absent_verified")is True
    result["passed"]=matched
    result["trace_authority_checked"]=matched
    result["model_adoption_verified"]=(matched and expected_reference
        and runtime.get("reuse_mode")=="model_adopted_reference")
    result["execution_reuse_verified"]=matched and expected_reference
    result["direct_recipe_execution_verified"]=(matched and expected_reference
        and runtime.get("reuse_mode")=="direct_fixed_recipe")
    result["actual_use_authenticated"]=False
    if not matched:result["reason"]="incomplete_authority_or_execution_evidence"
    return result
