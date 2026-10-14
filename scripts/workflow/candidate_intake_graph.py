"""Pure graph patch proposal. No Dify, database, credential or network writes."""
import copy
from pathlib import Path

def patch(graph, branches):
    """branches explicitly bind parameter/generator/executor/adapter node IDs.

    Only split an existing parameter->generator edge and add two executor
    selectors. Unknown layouts/colliding nodes reject instead of guessing.
    The returned graph is a draft artifact, never published by this module.
    """
    result=copy.deepcopy(graph)
    nodes=result["nodes"]; edges=result["edges"]
    ids=[node["id"] for node in nodes]
    if len(ids)!=len(set(ids)):raise ValueError("duplicate_graph_node")
    index={node["id"]:node for node in nodes}
    source=Path(__file__).with_name("skill_parameter_adapter.py").read_text()
    touched=set()
    for branch in branches:
        if set(branch)!={"parameter","generator","executor","adapter","scenario_type"}:
            raise ValueError("explicit_branch_binding_required")
        parameter,generator,executor,adapter=(branch[k] for k in ("parameter","generator","executor","adapter"))
        if any(type(v) is not str or not v for v in (parameter,generator,executor,adapter)):
            raise ValueError("invalid_branch_id")
        if len({parameter,generator,executor,adapter})!=4 or touched.intersection({executor,adapter}):
            raise ValueError("branch_collision")
        touched.update({executor,adapter})
        if not all(v in index for v in (parameter,generator,executor)):
            raise ValueError("missing_branch_node")
        if index[parameter]["data"].get("type")!="llm" or index[generator]["data"].get("type")!="llm":
            raise ValueError("unexpected_generation_branch")
        target=index[executor]["data"]
        if target.get("type")!="tool" or target.get("tool_name") not in {"run_jwave_code","run_jwave_code_with_retry"}:
            raise ValueError("unexpected_execution_tool")
        code_node={"id":adapter,"type":"custom","position":dict(index[parameter].get("position",{"x":0,"y":0})),
            "data":{"type":"code","title":"技能参数声明适配（未认证）","code_language":"python3","code":source,
                    "variables":[{"variable":"parameters_json","value_selector":[parameter,"text"]},
                                 {"variable":"scenario_type","value_selector":[]}],
                    "outputs":{k:{"type":t,"children":None} for k,t in {
                        "ready":"boolean","scenario_type":"string","candidate_parameters_json":"string",
                        "retrieval_parameters_json":"string","reason":"string"}.items()}}}
        # Embed only an explicit fixed scenario; absent scenario must come from
        # the parameter JSON. It is never inferred from user prose.
        scene=branch["scenario_type"]
        from scripts.workflow.skill_parameter_adapter import SCENARIOS
        if type(scene) is not str or scene and scene not in SCENARIOS:raise ValueError("invalid_branch_scenario")
        code_node["data"]["variables"]=code_node["data"]["variables"][:1]
        code_node["data"]["code"] += "\n\n_adapter_main = main\ndef main(parameters_json):\n    return _adapter_main(parameters_json, "+repr(scene)+")\n"
        params=target.setdefault("tool_parameters",{})
        expected={"scenario_type":{"type":"mixed","value":"{{#"+adapter+".scenario_type#}}"},
                  "params_json":{"type":"mixed","value":"{{#"+adapter+".candidate_parameters_json#}}" }}
        if adapter in index:
            if index[adapter]!=code_node or any(params.get(k)!=v for k,v in expected.items()):
                raise ValueError("adapter_node_collision")
            incoming=[edge for edge in edges if edge.get("source")==parameter and edge.get("target")==adapter]
            outgoing=[edge for edge in edges if edge.get("source")==adapter and edge.get("target")==generator]
            if len(incoming)!=1 or len(outgoing)!=1:
                raise ValueError("adapter_edges_drifted")
            continue
        if any(k in params for k in expected):raise ValueError("existing_intake_selectors")
        matches=[edge for edge in edges if edge.get("source")==parameter and edge.get("target")==generator]
        if len(matches)!=1:raise ValueError("direct_generation_edge_required")
        original=matches[0];edges.remove(original)
        first=copy.deepcopy(original);first.update(id=parameter+"-to-"+adapter, target=adapter)
        second=copy.deepcopy(original);second.update(id=adapter+"-to-"+generator, source=adapter)
        if any(edge.get("id") in {first["id"],second["id"]} for edge in edges):
            raise ValueError("edge_id_collision")
        if type(first.get("data")) is dict:first["data"]["targetType"]="code"
        if type(second.get("data")) is dict:second["data"]["sourceType"]="code"
        edges.extend([first,second]);nodes.append(code_node);index[adapter]=code_node
        params.update(expected)
    return result
