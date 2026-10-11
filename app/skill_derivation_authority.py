"""Same-snapshot upstream authority checks for independently observed children.

A child still needs its own signed run and raw physical evidence. Upstream
active labels alone never grant admission. This is only the fixed profile;
it neither generalizes parameters nor authenticates model/user use.
"""
from contextvars import ContextVar
import json
from app import skill_deadline as deadline
from scripts.validation.host_forward_signature import unique,reject

_STATE=ContextVar("fixed_derivation_authority",default=None)

def verify(store,db,skill_id,version,now):
    if not db.in_transaction:raise ValueError("derivation_authority_transaction")
    state=_STATE.get()
    token=None
    if state is None:
        state={"stack":set(),"visited":set()}
        token=_STATE.set(state)
    key=(skill_id,version)
    try:
        with deadline.limited(2):
            deadline.check()
            if key in state["stack"] or len(state["stack"])>=4:
                raise ValueError("derivation_authority_cycle_or_depth")
            if key not in state["visited"] and len(state["visited"])>=8:
                raise ValueError("derivation_authority_node_budget")
            state["visited"].add(key);state["stack"].add(key)
            try:
                row=db.execute("SELECT substr(reference_skill_ids,1,4097) refs FROM skill_versions WHERE skill_id=? AND version=?",key).fetchone()
                if row is None or len(row["refs"].encode())>4096:
                    raise ValueError("derivation_authority_declaration")
                declared=json.loads(row["refs"],object_pairs_hook=unique,parse_constant=reject)
                edges=[r[0] for r in db.execute("SELECT reference_skill_id FROM skill_references WHERE skill_id=? AND version=? LIMIT 4",key)]
                if (type(declared) is not list or len(declared)>3
                        or any(type(v) is not str for v in declared)
                        or len(declared)!=len(set(declared)) or sorted(declared)!=sorted(edges)):
                    raise ValueError("derivation_authority_edges")
                from app import skill_reference_registry as registry,controlled_dispatch
                for parent in edges:
                    deadline.check()
                    current=db.execute("""SELECT s.scenario_type,s.status,s.archived,s.quality_level,
                        max(v.version) version FROM simulation_skills s
                        JOIN skill_versions v ON v.skill_id=s.skill_id WHERE s.skill_id=?
                        GROUP BY s.skill_id""",(parent,)).fetchone()
                    if (current is None or current["status"]!="active" or current["archived"]
                            or current["quality_level"]!="Q4"):
                        raise ValueError("upstream_authority_lifecycle")
                    candidate={"skill_id":parent,"version":current["version"]}
                    verified=registry.verified_metadata_connection(store,db,candidate,
                        current["scenario_type"],controlled_dispatch.fixed_retrieval_parameters(),now)
                    if verified is None:raise ValueError("upstream_authority_unavailable")
                deadline.check()
            finally:
                state["stack"].remove(key)
    finally:
        if token is not None:_STATE.reset(token)
