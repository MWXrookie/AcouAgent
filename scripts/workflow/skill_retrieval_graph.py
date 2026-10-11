"""Offline Dify retrieval subgraph; no platform write, execution or trust grant."""
import copy
import json
from pathlib import Path

def _code(node_id,title,source,variables,outputs):
    return {"id":node_id,"type":"custom","position":{"x":0,"y":0},
            "data":{"type":"code","title":title,"code_language":"python3","code":source,
                    "variables":variables,"outputs":{k:{"type":v,"children":None} for k,v in outputs.items()}}}

def patch(graph,branches,search_template,*,retrieval_enabled=False,injection_enabled=False):
    """Require already-adapted graph and explicit original context selector.

    Switches are deployment literals, never user/chat selectors. The supplied
    MCP template must identify search_simulation_skill; provider credentials are
    neither inspected nor manufactured here. False skips the tool entirely.
    Unknown layout or an existing modified subgraph rejects without mutation.
    """
    if type(retrieval_enabled) is not bool or type(injection_enabled) is not bool:
        raise ValueError("strict_deployment_switches")
    if search_template.get("data",{}).get("type")!="tool" or search_template["data"].get("tool_name")!="search_simulation_skill":
        raise ValueError("explicit_search_tool_template_required")
    result=copy.deepcopy(graph);nodes=result["nodes"];edges=result["edges"]
    index={n["id"]:n for n in nodes}
    if len(index)!=len(nodes):raise ValueError("duplicate_graph_node")
    used=set()
    fallback={"skills":[],"fallback":True,"trusted_run":False,"runtime_approved":False,
              "parameters_bound":False,"run_bound":False,"reason":"retrieval_failed"}
    guard_source=Path(__file__).with_name("skill_generation_guard.py").read_text()
    for branch in branches:
        if set(branch)!={"adapter","generator","prefix","context_selector"}:
            raise ValueError("explicit_generation_binding_required")
        adapter,generator,prefix=(branch[k] for k in ("adapter","generator","prefix"))
        selector=branch["context_selector"]
        if (any(type(v) is not str or not v for v in (adapter,generator,prefix))
                or type(selector) is not list or len(selector)!=2 or any(type(v) is not str or not v for v in selector)
                or generator in used or adapter not in index or generator not in index
                or selector[0] not in index):
            raise ValueError("invalid_branch_binding")
        used.add(generator)
        if index[adapter]["data"].get("type")!="code" or index[generator]["data"].get("type")!="llm":
            raise ValueError("unexpected_generation_branch")
        inputs=index[adapter]["data"].get("variables",[])
        extraction=[v.get("value_selector") for v in inputs if v.get("variable")=="parameters_json"]
        if extraction!=[selector]:
            raise ValueError("retrieval_and_generation_parameters_must_match")
        names={key:prefix+"-"+key for key in ("gate","branch","search","fallback","merge","guard")}
        present={value for value in names.values() if value in index}
        if present:
            if present!=set(names.values()):raise ValueError("subgraph_id_collision")
            original=index[names["gate"]]["data"].get("acou_original_edge")
            if type(original) is not dict or original.get("source")!=adapter or original.get("target")!=generator:
                raise ValueError("subgraph_id_collision")
            related=[e for e in edges if e.get("source") in present or e.get("target") in present]
            if len(related)!=8:raise ValueError("subgraph_edges_drifted")
            rebuilt=copy.deepcopy(result)
            rebuilt["nodes"]=[n for n in rebuilt["nodes"] if n["id"] not in present]
            rebuilt["edges"]=[e for e in rebuilt["edges"] if e.get("source") not in present and e.get("target") not in present]
            rebuilt["edges"].append(copy.deepcopy(original))
            target=next(n for n in rebuilt["nodes"] if n["id"]==generator)
            replacement="{{#"+names["guard"]+".generation_context#}}"
            token="{{#"+".".join(selector)+"#}}"
            prompts=target["data"].get("prompt_template",[])
            if sum(v.get("text","").count(replacement) for v in prompts)!=1:
                raise ValueError("patched_prompt_drifted")
            for prompt in prompts:prompt["text"]=prompt["text"].replace(replacement,token)
            expected=patch(rebuilt,[branch],search_template,retrieval_enabled=retrieval_enabled,
                           injection_enabled=injection_enabled)
            # Ignore ordering only; unrelated graph content still must match.
            def canonical(value):
                copy_value=copy.deepcopy(value)
                copy_value["nodes"]=sorted(copy_value["nodes"],key=lambda n:n["id"])
                copy_value["edges"]=sorted(copy_value["edges"],key=lambda e:e["id"])
                return copy_value
            if canonical(expected)!=canonical(result):raise ValueError("patched_subgraph_drifted")
            continue
        matches=[e for e in edges if e.get("source")==adapter and e.get("target")==generator]
        if len(matches)!=1:raise ValueError("direct_adapter_generation_edge_required")
        token="{{#"+".".join(selector)+"#}}"
        prompts=index[generator]["data"].get("prompt_template")
        if type(prompts) is not list or not all(type(v) is dict and type(v.get("text")) is str for v in prompts):
            raise ValueError("explicit_prompt_template_required")
        if sum(v["text"].count(token) for v in prompts)!=1:
            raise ValueError("original_context_selector_must_occur_once")
        gate=_code(names["gate"],"技能检索开关",
            "def main(ready):\n    return {'route': 'search' if ready is True and "+repr(retrieval_enabled)+" else 'skip'}\n",
            [{"variable":"ready","value_selector":[adapter,"ready"]}],{"route":"string"})
        branch_node={"id":names["branch"],"type":"custom","position":{"x":0,"y":0},
            "data":{"type":"if-else","title":"只读检索或回退","cases":[{"case_id":"search",
              "logical_operator":"and","conditions":[{"variable_selector":[names["gate"],"route"],
              "comparison_operator":"end with","value":"search"}]}]}}
        search=copy.deepcopy(search_template);search["id"]=names["search"]
        data=search["data"];data.update(title="检索声学技能（只读）",
            tool_parameters={"scenario_type":{"type":"mixed","value":"{{#"+adapter+".scenario_type#}}"},
                             "parameters_json":{"type":"mixed","value":"{{#"+adapter+".retrieval_parameters_json#}}"},
                             "limit":{"type":"constant","value":3}},
            retry_config={"retry_enabled":False,"max_retries":0,"retry_interval":0},
            error_strategy="default-value",default_value=[{"key":"text","type":"string","value":json.dumps(fallback)}])
        for key in ("params","paramSchemas"):data.pop(key,None)
        closed=_code(names["fallback"],"关闭时保留原流程",
            "def main():\n    return {'text': "+repr(json.dumps(dict(fallback,reason="retrieval_disabled")))+"}\n",
            [],{"text":"string"})
        merge={"id":names["merge"],"type":"custom","position":{"x":0,"y":0},
            "data":{"type":"variable-aggregator","title":"检索结果归一","output_type":"string",
                    "variables":[[names["search"],"text"],[names["fallback"],"text"]]}}
        guard=_code(names["guard"],"生成前技能守卫",
            guard_source+"\n_guard_main=main\ndef main(original_context,retrieval_result_json):\n"
                         +"    return _guard_main(original_context,retrieval_result_json,"
                         +repr(retrieval_enabled)+","+repr(injection_enabled)+")\n",
            [{"variable":"original_context","value_selector":selector},
             {"variable":"retrieval_result_json","value_selector":[names["merge"],"output"]}],
            {"generation_context":"string","skill_injected":"boolean","referenced_skill_ids":"array[string]","fallback_reason":"string"})
        original=matches[0];edges.remove(original)
        gate["data"]["acou_original_edge"]=copy.deepcopy(original)
        additions=[]
        for source,target,handle in ((adapter,names["gate"],"source"),(names["gate"],names["branch"],"source"),
                (names["branch"],names["search"],"search"),(names["branch"],names["fallback"],"false"),
                (names["search"],names["merge"],"source"),(names["fallback"],names["merge"],"source"),
                (names["merge"],names["guard"],"source"),(names["guard"],generator,"source")):
            edge=copy.deepcopy(original)
            edge.update(id=source+"-to-"+target,source=source,target=target,sourceHandle=handle)
            edge["data"]={"sourceType":("if-else" if source==names["branch"] else
                "tool" if source==names["search"] else "variable-aggregator" if source==names["merge"] else "code"),
                "targetType":"llm" if target==generator else "if-else" if target==names["branch"] else
                    "tool" if target==names["search"] else "variable-aggregator" if target==names["merge"] else "code"}
            additions.append(edge)
        if {e["id"] for e in additions}&{e.get("id") for e in edges}:raise ValueError("edge_id_collision")
        edges.extend(additions);new=[gate,branch_node,search,closed,merge,guard]
        nodes.extend(new);index.update({n["id"]:n for n in new})
        replacement="{{#"+names["guard"]+".generation_context#}}"
        for prompt in prompts:prompt["text"]=prompt["text"].replace(token,replacement)
    return result
