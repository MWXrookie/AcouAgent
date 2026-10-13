"""Public skill feedback from current server authority, never producer trust flags."""
import json
from app import skill_local_catalog as catalog, skill_local_profile as local
from app import skill_library_service as service
from scripts.validation.host_forward_signature import canonical

READERS={"local-basic-main-reader","local-basic-retry-reader",
         "local_basic_main_reader","local_basic_retry_reader"}
RUNNERS={"5655556822808","1786095555004"}
def _notice(metadata,stage,run_id=""):
    spec=catalog.specification(metadata["scope"])
    return dict(stage=stage,skill_id=metadata["skill_id"],version=metadata["version"],
        name=spec["state_text"].split("；")[0],scope=metadata["scope"],
        execution_run_id=run_id,actual_use_authenticated=False,
        model_adoption_authenticated=False,
        message=("已提供技能参考：" if stage=="reference_delivered" else "已执行固定技能配方：")+
            spec["state_text"].split("；")[0]+
            ("（尚未确认生成代码采用）" if stage=="reference_delivered" else "（不代表模型参考关联已认证）"))
def reference_notice(data):
    """Reverify exact reader inputs against committed fresh generation decision."""
    try:
        if data.get("node_id") not in READERS:return None
        inputs=data["inputs"]
        fields=("decision_id","generation_run_id","scenario_type","parameters_json","original_context")
        if type(inputs) is not dict or any(type(inputs.get(k)) is not str for k in fields):return None
        context=local.read_context(**{k:inputs[k] for k in fields})
        prefix=inputs["original_context"]+"\n\nValidated local fixed-profile reference (parameters must remain unchanged):\n"
        if context==inputs["original_context"] or not context.startswith(prefix):return None
        output=data["outputs"].get("text")
        if type(output) is list and len(output)==1:output=output[0]
        if output!=context:return None
        metadata=local._json(context[len(prefix):])
        return _notice(metadata,"reference_delivered")
    except local.ERRORS:return None
def execution_notice(run_id):
    """Exact authenticated managed execution + fresh active source recheck; no model claim."""
    try:
        if not local.config.fixed_intake_enabled():return None
        if type(run_id) is not str or len(run_id)!=36:return None
        import uuid
        uuid.UUID(run_id)
        from app.skill_execution_history import checked_daily_history
        capsule,_,_=checked_daily_history(run_id);decoded=local._json(capsule["stdout"])
        spec=catalog.from_raw(decoded)
        with service.STORE._read_db() as db:
            rows=db.execute("SELECT s.skill_id,(SELECT MAX(version) FROM skill_versions v WHERE v.skill_id=s.skill_id) version FROM simulation_skills s WHERE scenario_type=? AND fingerprint=? LIMIT 2",
                (spec["scene"],local.pins.digest(canonical(dict(scenario_type=spec["scene"],parameters=spec["parameters"])).decode()))).fetchall()
            if len(rows)!=1:return None
            metadata=local.verify_connection(service.STORE,db,dict(rows[0]),spec["scene"],spec["parameters"])
            if metadata is None or run_id==metadata["source_run_id"]:return None
        return _notice(metadata,"recipe_execution_verified",run_id)
    except local.ERRORS:return None
def execution_node_notice(data):
    try:
        outputs=data.get("outputs")
        if type(outputs) is not dict:return None
        # Only fixed known execution nodes. Never interpret arbitrary LLM prose as evidence.
        if data.get("node_type")!="tool" or data.get("node_id") not in RUNNERS:return None
        values=outputs.get("json")
        if type(values) is not list or len(values)!=1 or type(values[0]) is not dict:return None
        return execution_notice(values[0].get("run_id"))
    except local.ERRORS:return None
