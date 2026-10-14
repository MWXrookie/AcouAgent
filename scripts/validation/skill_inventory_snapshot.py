"""Read-only observation of the configured local skill store; no execution or writes."""
import json
from app import config, skill_library_service as service, skill_local_catalog as catalog
from app import skill_local_profile as local

def observe():
    items=[]
    with service.STORE._read_db() as db:
        rows=db.execute("""SELECT s.skill_id,s.scenario_type,s.state_text,s.status,
            s.quality_level,s.archived,v.version,v.normalized_params,v.code_hash,
            v.source_run_id,v.validator_version,v.physics_evidence,l.capsule_json,
            l.capsule_sha256 FROM simulation_skills s
            JOIN skill_versions v ON s.skill_id=v.skill_id
            JOIN skill_local_sources l ON l.skill_id=v.skill_id AND l.version=v.version
            WHERE v.version=(SELECT MAX(version) FROM skill_versions WHERE skill_id=s.skill_id)
            ORDER BY s.skill_id LIMIT 101""").fetchall()
        if len(rows)>100: raise ValueError("inventory_budget")
        for row in rows:
            params=json.loads(row["normalized_params"])
            spec=catalog.matching(row["scenario_type"],params)
            if spec is None: raise ValueError("unreviewed_scope")
            checked=local.verify_connection(service.STORE,db,
                dict(skill_id=row["skill_id"],version=row["version"]),spec["scene"],params)
            if checked is None: raise ValueError("source_not_current")
            _,numeric=local._capsule_checked(json.loads(row["capsule_json"]))
            # Only fixed public numeric output; never export stdout, receipt or secrets.
            items.append(dict(scope=spec["scope"],name=row["state_text"],
                skill_id=row["skill_id"],version=row["version"],status=row["status"],
                quality_level=row["quality_level"],archived=row["archived"],
                scenario_type=spec["scene"],parameters=params,code_sha256=row["code_hash"],
                validator_sha256=spec["validator_sha256"],source_run_id=row["source_run_id"],
                capsule_sha256=row["capsule_sha256"],numeric=numeric,
                latest_source_rechecked=True))
        schemas=[r[0] for r in db.execute("SELECT version FROM schema_migrations ORDER BY version")]
    return dict(kind="readonly_local_inventory",items=items,schema=schemas,
        learning_enabled=config.SKILL_LEARNING_ENABLED,
        scope="managed gateway/internal executor only; no model or user authentication",
        new_solver_runs=0)
if __name__=="__main__":
    print(json.dumps(observe(),ensure_ascii=False,allow_nan=False,sort_keys=True))
