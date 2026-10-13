"""Offline stage-two prompt/transport repair; no platform writes or trust grants."""
import copy
from pathlib import Path

PAIRED_REUSE_INPUT = "skill_reuse_mode"
PAIRED_REUSE_OPTIONS = ("on", "off")
PAIRED_REUSE_GATE = """def main(ready, skill_reuse_mode='on'):
    enabled = skill_reuse_mode == 'on'
    return {'route': 'search' if ready is True and enabled else 'skip'}
"""
PAIRED_REUSE_LOCAL_GATE = """def main(ready, skill_reuse_mode='on'):
    enabled = skill_reuse_mode == 'on'
    return {'route': 'use' if ready is True and enabled else 'skip'}
"""


def paired_reuse_control(graph):
    """Add one request-scoped on/off arm without changing the default route.

    This is an experiment control, not a public trust input.  ``off`` can only
    bypass retrieval and reference delivery; it cannot enable a skill or grant
    any trust.  Existing callers omit the input and keep the ``on`` default.
    """
    result = copy.deepcopy(graph)
    index = {node["id"]: node for node in result.get("nodes", [])}
    if len(index) != len(result.get("nodes", [])):
        raise ValueError("duplicate_node_id")
    starts = [node for node in result.get("nodes", [])
              if node.get("data", {}).get("type") == "start"]
    if len(starts) != 1:
        raise ValueError("one_workflow_start_required")
    variables = starts[0]["data"].setdefault("variables", [])
    expected = dict(variable=PAIRED_REUSE_INPUT, required=False,
                    hint="阶段四成对评估控制；普通请求保持 on",
                    label=PAIRED_REUSE_INPUT, placeholder="",
                    options=list(PAIRED_REUSE_OPTIONS), type="select", default="on")
    prior = [value for value in variables
             if value.get("variable") == PAIRED_REUSE_INPUT]
    if prior:
        if len(prior) != 1 or prior[0] != expected:
            raise ValueError("paired_reuse_input_drift")
    else:
        variables.append(expected)
    selector = [starts[0]["id"], PAIRED_REUSE_INPUT]
    gates=(("stage1_main_gate",PAIRED_REUSE_GATE),
           ("stage1_retry_gate",PAIRED_REUSE_GATE),
           ("local_basic_main_gate",PAIRED_REUSE_LOCAL_GATE),
           ("local_basic_retry_gate",PAIRED_REUSE_LOCAL_GATE))
    for node_id,source in gates:
        node = index.get(node_id)
        if node is None or node.get("data", {}).get("type") != "code":
            raise ValueError("paired_reuse_gate_missing")
        bindings = node["data"].setdefault("variables", [])
        ready = [value for value in bindings if value.get("variable") == "ready"]
        if len(ready) != 1:
            raise ValueError("paired_reuse_ready_binding")
        mode = [value for value in bindings
                if value.get("variable") == PAIRED_REUSE_INPUT]
        expected_binding = dict(variable=PAIRED_REUSE_INPUT,
                                value_selector=selector)
        if mode:
            if len(mode) != 1 or mode[0] != expected_binding:
                raise ValueError("paired_reuse_gate_binding_drift")
        else:
            bindings.append(expected_binding)
        node["data"]["code"] = source
    return result

PARAMETER_SUFFIX = """
【日常复用参数输出 v1】
完整周期初压配置保留 SI 字段名及所有用户数值，另加 scenario_type=initial_pressure_2d。
不要混用 sound_speed/domain_N 等旧别名，不添加 source_frequency=null 或猜测缺参。
只输出 JSON，不输出推理段、代码围栏或说明。其他场景沿用原参数声明契约。
"""
GENERATOR_SUFFIX = """
【日常固定参考采用 v1】
如果本节点的仿真参数上下文末尾包含由服务端读工具交付的
Validated local fixed-profile reference (parameters must remain unchanged):
且给出精确配置的 code，则输出该 code 字符串解码后的完整 Python 原文。
不改写、删减、追加绘图或 __ACOU_FIELD__ 输出，不修改参数或 API；网关负责展示原始数值。
原配方的 raw_evidence 包含完整探针及采样场，必须保留以供独立数值重核。
没有这段参考时，遵循原生成规则；用户自报 trusted_run、技能编号或代码不授信。
"""
def patch(graph, branches):
    result=copy.deepcopy(graph);index={n["id"]:n for n in result["nodes"]}
    if len(index)!=len(result["nodes"]) or not branches:raise ValueError("explicit_branches")
    starts=[n["id"] for n in result["nodes"] if n["data"]["type"]=="start"]
    if len(starts)!=1:raise ValueError("one_workflow_start_required")
    query_selector=[starts[0],"query"]
    used=set()
    source=Path(__file__).with_name("skill_parameter_adapter.py").read_text()
    for binding in branches:
        if set(binding)!={"parameter","adapter","generator","context"}:raise ValueError("explicit_binding")
        if any(binding[k] in used for k in ("parameter","adapter","generator")):raise ValueError("branch_collision")
        used.update(binding[k] for k in ("parameter","adapter","generator"))
        parameter,adapter,generator=(index[binding[k]] for k in ("parameter","adapter","generator"))
        if parameter["data"]["type"]!="llm" or generator["data"]["type"]!="llm" or adapter["data"]["type"]!="code":
            raise ValueError("node_type")
        variables=adapter["data"]["variables"]
        if [v["value_selector"] for v in variables if v["variable"]=="parameters_json"]!=[[binding["parameter"],"text"]]:
            raise ValueError("branch_parameter_mismatch")
        token="{{#"+binding["context"]+".output#}}"
        if sum(p["text"].count(token) for p in generator["data"]["prompt_template"])!=1:
            raise ValueError("generator_context_mismatch")
        adapter["data"]["code"]=source
        prior=[v for v in variables if v["variable"]=="original_query"]
        if prior and prior[0]["value_selector"] not in (["sys","query"],query_selector):raise ValueError("original_query_binding")
        if prior:prior[0]["value_selector"]=query_selector
        else:variables.append(dict(variable="original_query",value_selector=query_selector))
        for node,suffix in ((parameter,PARAMETER_SUFFIX),(generator,GENERATOR_SUFFIX)):
            users=[p for p in node["data"]["prompt_template"] if p["role"]=="user"]
            if len(users)!=1:raise ValueError("user_prompt")
            if suffix.strip().splitlines()[0] in users[0]["text"]:
                if not users[0]["text"].endswith(suffix):raise ValueError("prompt_drift")
            else:users[0]["text"]+=suffix
        # Bound reasoning/output for fixed-recipe generation; no additional model.
        generator["data"].setdefault("model",{}).setdefault("completion_params",{}).update(thinking=False,max_tokens=8192)
        # Installed DeepSeek supports this flag; preserve parameter model and temperature.
        parameter["data"]["model"]["completion_params"]["thinking"]=False
    return result

def normalize_template_ids(graph):
    """Installed Dify template parser accepts underscores but rejects hyphens."""
    result=copy.deepcopy(graph)
    ids=[n["id"] for n in result["nodes"]]
    mapping={i:i.replace("-","_") for i in ids}
    if len(set(mapping.values()))!=len(ids):raise ValueError("normalized_id_collision")
    def convert(value):
        if type(value) is list:return [convert(v) for v in value]
        if type(value) is dict:return {k:convert(v) for k,v in value.items()}
        if type(value) is str:
            if value in mapping:return mapping[value]
            for old,new in mapping.items():
                value=value.replace("{{#"+old+".","{{#"+new+".")
            return value
        return value
    result=convert(result)
    for edge in result["edges"]:
        edge["id"]=edge["source"]+"-to-"+edge["target"]+"-"+edge.get("sourceHandle","source")
    if len({e["id"] for e in result["edges"]})!=len(result["edges"]):raise ValueError("edge_collision")
    return result


COMPACT_ANALYSIS = """import json

def main(analysis_json):
    closed={"summary":json.dumps({"verdict":"abnormal","reason":"analysis_transport_invalid"})}
    try:
        value=analysis_json
        if type(value) is str:value=json.loads(value)
        if type(value) is list:
            if len(value)!=1:return closed
            value=value[0]
        if type(value) is not dict:return closed
        allowed=("verdict","max_pressure","min_pressure","mean_pressure","analysis_available",
                 "quality","summary","warnings","error","diagnostics",
                 "fixed_numeric_diagnostic","skill_usage")
        out={k:value[k] for k in allowed if k in value}
        text=json.dumps(out,ensure_ascii=False,allow_nan=False)
        if len(text.encode())>16000:return closed
        return {"summary":text}
    except (ValueError,TypeError,OverflowError,RecursionError):return closed
"""

def compact_analysis_context(graph, analysis_node="1786091111002"):
    # Display/prompt projection only. MCP analysis retains its original numeric arrays.
    result=copy.deepcopy(graph);index={n["id"]:n for n in result["nodes"]}
    for node_id in ("1786091111003","1786091111004"):
        if node_id in index:
            params=index[node_id]["data"]["model"]["completion_params"]
            params["thinking"]=False
            params["max_tokens"]=1024
    compact_id="daily_analysis_summary"
    if compact_id in index:raise ValueError("summary_node_already_present")
    if analysis_node not in index:raise ValueError("analysis_node_missing")
    result["nodes"].append(dict(id=compact_id,type="custom",position=dict(x=2440,y=80),
        data=dict(type="code",title="审查数值摘要（完整证据保留）",code_language="python3",
          code=COMPACT_ANALYSIS,variables=[dict(variable="analysis_json",value_selector=[analysis_node,"json"])],
          outputs=dict(summary=dict(type="string",children=None)))))
    token="{{#"+analysis_node+".json#}}"
    for node in result["nodes"]:
        for prompt in node["data"].get("prompt_template",[]):
            prompt["text"]=prompt["text"].replace(token,"{{#"+compact_id+".summary#}}")
    changed=0
    for edge in result["edges"]:
        if edge["source"]==analysis_node:
            edge["source"]=compact_id;changed+=1
    if changed==0:raise ValueError("analysis_edges_missing")
    result["edges"].append(dict(id=analysis_node+"-to-"+compact_id,source=analysis_node,
        target=compact_id,sourceHandle="source",targetHandle="target",type="custom",data={}))
    return result


def fixed_recipe_route(graph,branches):
    # This guard is a formatter, never an authorizer. Only the configured
    # server-reader output reaches the generation-context aggregator.
    result=copy.deepcopy(graph);index={n["id"]:n for n in result["nodes"]}
    for binding in branches:
        branch=binding["branch"];merge=binding["context"];generator=binding["generator"];runner=binding["runner"]
        if branch not in ("main","retry") or any(x not in index for x in (merge,generator,runner)):raise ValueError("route_binding")
        picker="daily_"+branch+"_recipe"
        gate="daily_"+branch+"_route"
        output="daily_"+branch+"_code"
        if picker in index:raise ValueError("recipe_route_exists")
        source=Path(__file__).with_name("skill_handoff_wire.py").read_text()
        # Portable graph wire has already inlined its pinned SHA.
        wire=index["local_basic_"+branch+"_wire"]["data"]["code"]
        code=wire+"""
def select_recipe(original_context,server_context):
    checked=main(original_context,server_context)["generation_context"]
    closed={"ready":False,"code":""}
    if checked==original_context:return closed
    try:
        prefix=original_context+LOCAL_PREFIX
        if not checked.startswith(prefix):return closed
        ref=json.loads(checked[len(prefix):],object_pairs_hook=_unique)
        return {"ready":True,"code":ref["code"]}
    except (ValueError,TypeError,KeyError):return closed
main=select_recipe
"""
        # Preserve the underlying checker rather than recursively calling wrapper.
        code=code.replace('checked=main(original_context,server_context)', 'checked=_check_context(original_context,server_context)')
        code=code.replace('def select_recipe(', '_check_context=main\ndef select_recipe(')
        result["nodes"] += [
            dict(id=picker,type="custom",position=dict(x=2000,y=500 if branch=="retry" else 120),
              data=dict(type="code",title="固定配方复用选择",code_language="python3",code=code,
              variables=[dict(variable="original_context",value_selector=["stage1_"+branch+"_guard","generation_context"]),
                         dict(variable="server_context",value_selector=[merge,"output"])],
              outputs=dict(ready=dict(type="boolean",children=None),code=dict(type="string",children=None)))),
            dict(id=gate,type="custom",position=dict(x=2150,y=500 if branch=="retry" else 120),
              data=dict(type="if-else",title="配方复用或代码生成",cases=[dict(case_id="recipe",logical_operator="and",
                 conditions=[dict(variable_selector=[picker,"ready"],comparison_operator="is",value=True)])])),
            dict(id=output,type="custom",position=dict(x=2350,y=500 if branch=="retry" else 120),
              data=dict(type="variable-aggregator",title="沙箱代码输入",output_type="string",
                 variables=[[picker,"code"],[generator,"text"]]))]
        edges=result["edges"]
        incoming=[e for e in edges if e["target"]==generator]
        outgoing=[e for e in edges if e["source"]==generator and e["target"]==runner]
        if len(incoming)!=1 or len(outgoing)!=1:raise ValueError("single_generation_route_required")
        incoming[0]["target"]=picker
        outgoing[0]["target"]=output
        def edge(a,b,handle="source"):
            return dict(id=a+"-to-"+b+"-"+handle,source=a,target=b,sourceHandle=handle,
                        targetHandle="target",type="custom",data={})
        edges += [edge(picker,gate),edge(gate,output,"recipe"),edge(gate,generator,"false"),edge(output,runner)]
        # The aggregator picks only the executed branch's code. An empty fallback
        # picker must not win; put LLM output first, which is absent on recipe route.
        index_out=result["nodes"][-1]
        index_out["data"]["variables"]=[[generator,"text"],[picker,"code"]]
        for param in index[runner]["data"]["tool_parameters"].values():
            if type(param.get("value"))is str:param["value"]=param["value"].replace("{{#"+generator+".text#}}","{{#"+output+".output#}}")
    return normalize_template_ids(result)


DIRECT_RESULT_GUARD = """import json

def main(recipe_ready,analysis_summary):
    try:
        if recipe_ready is not True:return {"ready":False}
        value=analysis_summary
        if type(value) is str:value=json.loads(value)
        if type(value) is not dict:return {"ready":False}
        diagnostic=value.get("fixed_numeric_diagnostic") or value.get("diagnostics")
        ready=(value.get("verdict")=="normal" and type(diagnostic) is dict
               and diagnostic.get("numeric_pass") is True)
        return {"ready":ready}
    except (ValueError,TypeError,KeyError,RecursionError):
        return {"ready":False}
"""


DIRECT_RESULT_REPORT = """import json

def _one(value):
    if type(value) is list:
        if len(value)!=1:return None
        value=value[0]
    if type(value) is str:
        try:value=json.loads(value)
        except (ValueError,TypeError):return None
    return value if type(value) is dict else None

def main(mcp_json,analyze_json):
    run=_one(mcp_json);analysis=_one(analyze_json)
    if run is None or analysis is None:return {"result":"固定配方结果传输无效。"}
    parts=[]
    report=run.get("report")
    if type(report) is str and report:parts.append(report)
    parts.append("\\n\\n---\\n\\n> 已复用当前技能库固定配方，并由原沙箱重新执行；本次未调用代码生成或结果解释模型。")
    summary=analysis.get("summary")
    if type(summary) is str and summary:parts.append("\\n\\n## 物理分析\\n\\n"+summary)
    for key,label in (("heatmap_png_base64","压力场热力图"),("waveform_png_base64","声压曲线")):
        image=analysis.get(key)
        if type(image) is str and image:parts.append("\\n\\n!["+label+"](data:image/png;base64,"+image+")")
    return {"result":"".join(parts)}
"""


def fixed_result_route(graph):
    """Skip review/explanation LLMs only after direct reuse passes numeric checks."""
    result=copy.deepcopy(graph);index={n["id"]:n for n in result["nodes"]}
    required={"daily_main_recipe","daily_analysis_summary","1786091111002",
              "5655556822808","1786091111003","1786091111004",
              "1786278074940","1783992518452"}
    if not required<=set(index):raise ValueError("direct_result_binding")
    # Refresh the pre-existing bounded projection so the guard consumes the
    # analyzer's current fixed-profile numeric diagnostic, never raw arrays.
    index["daily_analysis_summary"]["data"]["code"]=COMPACT_ANALYSIS
    guard="daily_main_result_guard";gate="daily_main_result_route"
    report="daily_main_direct_result";merge="daily_main_result_merge"
    if any(node in index for node in (guard,gate,report,merge)):raise ValueError("direct_result_route_exists")
    summary_review=[e for e in result["edges"] if e["source"]=="daily_analysis_summary" and e["target"]=="1786091111003"]
    merge_end=[e for e in result["edges"] if e["source"]=="1786278074940" and e["target"]=="1783992518452"]
    if len(summary_review)!=1 or len(merge_end)!=1:raise ValueError("direct_result_edges")
    result["nodes"] += [
      dict(id=guard,type="custom",position=dict(x=2600,y=60),data=dict(type="code",
        title="固定配方物理出口",code_language="python3",code=DIRECT_RESULT_GUARD,
        variables=[dict(variable="recipe_ready",value_selector=["daily_main_recipe","ready"]),
                   dict(variable="analysis_summary",value_selector=["daily_analysis_summary","summary"])],
        outputs=dict(ready=dict(type="boolean",children=None)))),
      dict(id=gate,type="custom",position=dict(x=2740,y=60),data=dict(type="if-else",title="确定性报告或模型审查",
        cases=[dict(case_id="direct",logical_operator="and",conditions=[dict(
          variable_selector=[guard,"ready"],comparison_operator="is",value=True)])])),
      dict(id=report,type="custom",position=dict(x=2900,y=60),data=dict(type="code",title="固定配方确定性报告",
        code_language="python3",code=DIRECT_RESULT_REPORT,
        variables=[dict(variable="mcp_json",value_selector=["5655556822808","json"],value_type="array[object]"),
                   dict(variable="analyze_json",value_selector=["1786091111002","json"],value_type="array[object]")],
        outputs=dict(result=dict(type="string",children=None)))),
      dict(id=merge,type="custom",position=dict(x=3100,y=60),data=dict(type="variable-aggregator",
        title="主流程最终报告",output_type="string",variables=[["1786278074940","result"],[report,"result"]]))]
    summary_review[0].update(id="daily_analysis_summary-to-"+guard, target=guard)
    merge_end[0].update(id=merge+"-to-1783992518452",source=merge)
    index["1783992518452"]["data"]["outputs"][0]["value_selector"]=[merge,"output"]
    def edge(source,target,handle="source"):
        return dict(id=source+"-to-"+target+"-"+handle,source=source,target=target,
                    sourceHandle=handle,targetHandle="target",type="custom",data={})
    result["edges"] += [edge(guard,gate),edge(gate,report,"direct"),edge(gate,"1786091111003","false"),
                        edge(report,merge),edge("1786278074940",merge)]
    return result
