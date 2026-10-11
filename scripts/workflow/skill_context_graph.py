"""Offline fixed-policy handoff graph, no platform writes or credential access.

Authorization occurs on the gateway reader under current SQLite authority,
never in code-node JSON/boolean checks. Actual authenticated MCP transport and
current platform graph must still be verified before deployment.
"""
import copy
import json
from pathlib import Path
from scripts.workflow.skill_retrieval_graph import _code
from scripts.validation.plane_wave_run_binding import RECIPE_SHA256

def patch(graph,branches,builder_template,reader_template,*,retrieval_enabled=False,injection_enabled=False):
    if type(retrieval_enabled) is not bool or type(injection_enabled) is not bool:
        raise ValueError("strict_deployment_switches")
    for template,name in ((builder_template,"build_simulation_generation_context"),(reader_template,"read_simulation_generation_context")):
        data=template.get("data",{})
        if data.get("type")!="tool" or data.get("tool_name")!=name or not data.get("provider_id"):
            raise ValueError("explicit_gateway_tool_required")
    if builder_template["data"]["provider_id"]!=reader_template["data"]["provider_id"]:
        raise ValueError("same_gateway_provider_required")
    result=copy.deepcopy(graph);nodes=result["nodes"];edges=result["edges"]
    index={node["id"]:node for node in nodes}
    if len(index)!=len(nodes):raise ValueError("duplicate_node")
    used=set()
    pointer_source=Path(__file__).with_name("skill_handoff_pointer.py").read_text()
    wire_source=Path(__file__).with_name("skill_handoff_wire.py").read_text().replace(
        "from scripts.validation.plane_wave_run_binding import RECIPE_SHA256","RECIPE_SHA256="+repr(RECIPE_SHA256))
    for binding in branches:
        base={"adapter","generator","prefix","context_selector"}
        if set(binding) not in (base,base|{"predecessor","parameter_selector"}):raise ValueError("explicit_branch")
        adapter,generator,prefix=(binding[k] for k in ("adapter","generator","prefix"))
        selector=binding["context_selector"]
        # Validate the context selector before any indexed access or graph mutation.
        if (type(selector) is not list or len(selector)!=2
                or any(type(v) is not str or not v for v in selector)):
            raise ValueError("invalid_branch")
        predecessor=binding.get("predecessor",adapter)
        parameter_selector=binding.get("parameter_selector",selector)
        if (type(predecessor) is not str or predecessor not in index
                or type(parameter_selector) is not list or len(parameter_selector)!=2
                or any(type(v) is not str or not v for v in parameter_selector)
                or parameter_selector[0] not in index
                or predecessor!=adapter and (selector[0]!=predecessor or index[predecessor]["data"].get("type")!="code")):
            raise ValueError("explicit_pre_generation_predecessor")
        if (any(type(v) is not str or not v for v in (adapter,generator,prefix))
                or generator in used or type(selector) is not list or len(selector)!=2
                or any(type(v) is not str or not v for v in selector)
                or any(v not in index for v in (adapter,generator,selector[0]))):
            raise ValueError("invalid_branch")
        used.add(generator)
        if index[adapter]["data"].get("type")!="code" or index[generator]["data"].get("type")!="llm":
            raise ValueError("unexpected_branch")
        if [v.get("value_selector") for v in index[adapter]["data"].get("variables",[])
                if v.get("variable")=="parameters_json"]!=[parameter_selector]:
            raise ValueError("query_generation_context_mismatch")
        ids={role:prefix+"-"+role for role in ("gate","branch","builder","pointer","reader","wire","fallback","merge")}
        token="{{#"+".".join(selector)+"#}}";replacement="{{#"+ids["merge"]+".output#}}"
        present=set(ids.values())&set(index)
        if present:
            if present!=set(ids.values()):raise ValueError("partial_collision")
            original=index[ids["gate"]]["data"].get("acou_original_edge")
            if type(original) is not dict or original.get("source")!=predecessor or original.get("target")!=generator:
                raise ValueError("original_edge_missing")
            if len([e for e in edges if e["source"] in present or e["target"] in present])!=10:
                raise ValueError("edges_drifted")
            rebuilt=copy.deepcopy(result)
            rebuilt["nodes"]=[n for n in rebuilt["nodes"] if n["id"] not in present]
            rebuilt["edges"]=[e for e in rebuilt["edges"] if e["source"] not in present and e["target"] not in present]+[original]
            target=next(n for n in rebuilt["nodes"] if n["id"]==generator)
            if sum(v["text"].count(replacement) for v in target["data"]["prompt_template"])!=1:
                raise ValueError("prompt_drifted")
            for v in target["data"]["prompt_template"]:v["text"]=v["text"].replace(replacement,token)
            expected=patch(rebuilt,[binding],builder_template,reader_template,
                           retrieval_enabled=retrieval_enabled,injection_enabled=injection_enabled)
            def stable(value):
                copy_value=copy.deepcopy(value)
                copy_value["nodes"]=sorted(copy_value["nodes"],key=lambda n:n["id"])
                copy_value["edges"]=sorted(copy_value["edges"],key=lambda e:e["id"])
                return copy_value
            if stable(expected)!=stable(result):raise ValueError("graph_drifted")
            continue
        direct=[e for e in edges if e["source"]==predecessor and e["target"]==generator]
        prompts=index[generator]["data"].get("prompt_template")
        if len(direct)!=1 or type(prompts) is not list or any(type(v) is not dict or type(v.get("text")) is not str for v in prompts):
            raise ValueError("explicit_generation_edge_prompt")
        if sum(v["text"].count(token) for v in prompts)!=1:raise ValueError("context_occurs_once")
        gate=_code(ids["gate"],"服务端技能上下文开关","def main(ready):\n    return {'route':'use' if ready is True and "+
                   repr(retrieval_enabled and injection_enabled)+" else 'skip'}\n",
                   [{"variable":"ready","value_selector":[adapter,"ready"]}],{"route":"string"})
        gate["data"]["acou_original_edge"]=copy.deepcopy(direct[0])
        branch={"id":ids["branch"],"type":"custom","position":{"x":0,"y":0},"data":{"type":"if-else","title":"生成上下文或原输入",
            "cases":[{"case_id":"use","logical_operator":"and","conditions":[{"variable_selector":[ids["gate"],"route"],
                      "comparison_operator":"end with","value":"use"}]}]}}
        def tool(template,role,parameters,default):
            node=copy.deepcopy(template);node["id"]=ids[role]
            node["data"].update(tool_parameters=parameters,error_strategy="default-value",
                default_value=[{"key":"text","type":"string","value":default}],
                retry_config={"retry_enabled":False,"max_retries":0,"retry_interval":0})
            for key in ("params","paramSchemas"):node["data"].pop(key,None)
            return node
        common={"original_context":{"type":"mixed","value":token},
                "scenario_type":{"type":"mixed","value":"{{#"+adapter+".scenario_type#}}"},
                "parameters_json":{"type":"mixed","value":"{{#"+adapter+".retrieval_parameters_json#}}"}}
        builder=tool(builder_template,"builder",copy.deepcopy(common),"{}")
        pointer=_code(ids["pointer"],"只提取不可信决定编号",pointer_source,
            [{"variable":"result_json","value_selector":[ids["builder"],"json"]}],
            {"decision_id":"string","generation_run_id":"string"})
        parameters=copy.deepcopy(common)
        for key in ("decision_id","generation_run_id"):
            parameters[key]={"type":"mixed","value":"{{#"+ids["pointer"]+"."+key+"#}}"}
        reader=tool(reader_template,"reader",parameters,"")
        wire=_code(ids["wire"],"服务端已核验上下文格式",wire_source,
            [{"variable":"original_context","value_selector":selector},
             {"variable":"server_context","value_selector":[ids["reader"],"text"]}],{"generation_context":"string"})
        fallback=_code(ids["fallback"],"原输入回退","def main(original_context):\n    return {'generation_context':original_context}\n",
            [{"variable":"original_context","value_selector":selector}],{"generation_context":"string"})
        merge={"id":ids["merge"],"type":"custom","position":{"x":0,"y":0},
            "data":{"type":"variable-aggregator","title":"生成输入归一","output_type":"string",
                    "variables":[[ids["wire"],"generation_context"],[ids["fallback"],"generation_context"]]}}
        new=[gate,branch,builder,pointer,reader,wire,fallback,merge]
        kinds={n["id"]:n["data"]["type"] for n in new};kinds[predecessor]="code";kinds[generator]="llm"
        edges.remove(direct[0])
        for source,target,handle in ((predecessor,ids["gate"],"source"),(ids["gate"],ids["branch"],"source"),
            (ids["branch"],ids["builder"],"use"),(ids["branch"],ids["fallback"],"false"),
            (ids["builder"],ids["pointer"],"source"),(ids["pointer"],ids["reader"],"source"),
            (ids["reader"],ids["wire"],"source"),(ids["wire"],ids["merge"],"source"),
            (ids["fallback"],ids["merge"],"source"),(ids["merge"],generator,"source")):
            edge=copy.deepcopy(direct[0]);edge.update(id=source+"-to-"+target,source=source,target=target,sourceHandle=handle,
                data={"sourceType":kinds[source],"targetType":kinds[target]})
            if any(e["id"]==edge["id"] for e in edges):raise ValueError("edge_collision")
            edges.append(edge)
        nodes.extend(new);index.update({n["id"]:n for n in new})
        for v in prompts:v["text"]=v["text"].replace(token,replacement)
    return result
