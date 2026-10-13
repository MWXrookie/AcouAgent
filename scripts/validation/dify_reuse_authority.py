"""Read-only native Dify authority check for a captured daily reuse trace.

Run inside the installed Dify API environment. Large persisted display strings
may be truncated by Dify; complete raw numeric output is checked separately
against the gateway execution history and receipt by skill_daily_reuse.
"""
import json

def verify_native(trace,expected_app_id,branch="main"):
    from app_factory import create_app
    from sqlalchemy import select,text
    from sqlalchemy.orm import sessionmaker
    from extensions.ext_database import db
    from models.workflow import WorkflowRun,WorkflowNodeExecutionModel
    if branch not in ("main","retry"):raise ValueError("branch")
    starts=[e["data"] for e in trace["events"] if e.get("event")=="workflow_started"]
    ends=[e["data"] for e in trace["events"] if e.get("event")=="workflow_finished"]
    if trace.get("complete")is not True or len(starts)!=1 or len(ends)!=1:raise ValueError("complete_native_trace_required")
    start=starts[0]
    critical={"stage1_params_"+branch,"local_basic_"+branch+"_reader","local_basic_"+branch+"_wire",
              "local_basic_"+branch+"_fallback","local_basic_"+branch+"_merge",
              "stage1_"+branch+"_guard",
              "1783991048409" if branch=="main" else "1786095555002",
              "1783991079665" if branch=="main" else "1786095555003",
              "5655556822808" if branch=="main" else "1786095555004"}
    runner="5655556822808" if branch=="main" else "1786095555004"
    generator="1783991079665" if branch=="main" else "1786095555003"
    recipe="daily_"+branch+"_recipe";combined="daily_"+branch+"_code"
    def decoded(value):return json.loads(value) if type(value)is str else value
    _,app=create_app()
    with app.app_context():
        with sessionmaker(bind=db.engine)() as session:
            session.execute(text("SET TRANSACTION READ ONLY"))
            run=session.get(WorkflowRun,start["id"])
            if (run is None or run.app_id!=expected_app_id or run.workflow_id!=start["workflow_id"]
                    or run.status!=ends[0]["status"]):raise ValueError("native_run_mismatch")
            native_inputs=decoded(run.inputs)
            if any(native_inputs.get(k)!=v for k,v in start["inputs"].items()):raise ValueError("native_input_changed")
            rows=session.scalars(select(WorkflowNodeExecutionModel).where(WorkflowNodeExecutionModel.workflow_run_id==run.id)).all()
            index={r.node_id:r for r in rows}
            if len(index)!=len(rows):raise ValueError("duplicate_native_node")
            trace_index={e["data"]["node_id"]:e["data"] for e in trace["events"] if e.get("event")=="node_finished"}
            direct_recipe=(recipe in trace_index and trace_index[recipe].get("outputs",{}).get("ready") is True)
            forbidden=[]
            if direct_recipe:
                critical.update((recipe,combined));forbidden.append(generator)
                if branch=="main":
                    forbidden.extend(("1786091111003","1786091111004"))
                if any(node in index for node in forbidden):raise ValueError("native_model_node_executed")
            checked=[];display_omitted=[]
            for event in trace["events"]:
                if event.get("event")!="node_finished":continue
                d=event["data"];node=d["node_id"]
                r=index.get(node)
                if r is None or r.status!=d["status"]:raise ValueError("native_node_mismatch")
                if node not in critical:continue
                if decoded(r.inputs)!=d["inputs"]:raise ValueError("native_critical_input_changed")
                native_out=decoded(r.outputs)
                if node==runner:
                    # Raw stdout is complete in the separate gateway history, whereas
                    # Dify deliberately truncates its display persistence.
                    for k in ("run_id","final_code","exit_code","timed_out","stderr"):
                        if native_out.get(k)!=d["outputs"].get(k):raise ValueError("native_execution_changed")
                    display_omitted.append(node)
                elif native_out!=d["outputs"]:raise ValueError("native_critical_output_changed")
                checked.append(node)
            if not checked:raise ValueError("critical_nodes_absent")
            return dict(trace_authority_checked=True,workflow_run_id=run.id,native_status=run.status,
                critical_nodes_checked=checked,display_outputs_checked=False,
                model_nodes_absent_verified=direct_recipe,model_nodes_absent=forbidden,
                truncated_raw_checked_in_gateway=True,actual_use_authenticated=False)
