"""Offline Dify model-error routing; declarations and physical results stay strict."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path

from scripts.workflow.initial_declaration_guard_graph import GUARD_ID, guard_source
from scripts.workflow.skill_stage1_graph import load_json

FALLBACK = "acouagent-parameter-model-fallback-v1"
SUCCESS = "acouagent-parameter-model-success-v1"
AGGREGATOR = "acouagent-parameter-model-result-v1"
SUCCESS_CODE = '''def main(parameters_json):
    return {"packet": {"parameters_json": parameters_json, "model_available": True}}
'''
FALLBACK_WRAPPER = '''
_declaration_guard = main
def main(query):
    _declaration_guard(query)
    start = query.find("{")
    if start < 0:
        raise ValueError("parameter_model_unavailable_no_explicit_initial_declaration")
    value = json.loads(query[start:], object_pairs_hook=_unique_declaration,
                       parse_constant=_nonfinite_declaration)
    params = value.get("simulation_params", value) if type(value) is dict else None
    if type(params) is not dict or not is_initial(params):
        raise ValueError("parameter_model_unavailable_no_explicit_initial_declaration")
    params = dict(params)
    # Scenario metadata only; every physical value remains the user's own value.
    scene = value.get("scenario_type", "initial_pressure_2d")
    if scene not in {"initial_pressure_2d", "plane_wave_2d", "interface_2d"}:
        raise ValueError("explicit_initial_scenario_invalid")
    params["scenario_type"] = scene
    return {"packet": {"parameters_json": json.dumps(params, ensure_ascii=False,
            sort_keys=True, separators=(",", ":"), allow_nan=False), "model_available": False}}
'''
REPORT_CODE = '''def main(report, model_available):
    if type(report) is not str or type(model_available) is not bool:
        raise ValueError("model_status_report_invalid")
    if model_available is False:
        report += "\\n\\n参数提取模型暂不可用；本次仅使用通过共享校验的用户原始显式声明，未补猜物理参数。"
    return {"report": report, "model_parameter_extraction_available": model_available}
'''
REVIEW_NOTE = "模型审查暂不可用；本次结论以真实执行与独立物理验证为准，未用模型文本替代数值证据。"
EXPLANATION_NOTE = "模型解释暂不可用；真实仿真数据、图与确定性物理结论保留。"


def _code(identifier, title, source, variables, outputs, position):
    return {"id": identifier, "type": "custom", "position": dict(position), "data": {
        "type": "code", "title": title, "code_language": "python3", "code": source,
        "variables": [{"variable": k, "value_selector": v} for k, v in variables.items()],
        "outputs": {k: {"type": v, "children": None} for k, v in outputs.items()},
    }}


def _edge(source, target, source_type, target_type, handle="source"):
    return {"id": source + "-" + handle + "-to-" + target, "type": "custom",
            "source": source, "target": target, "sourceHandle": handle, "targetHandle": "target",
            "data": {"sourceType": source_type, "targetType": target_type}}


def patch(graph, *, parameter, review, explanation):
    result = copy.deepcopy(graph)
    nodes, edges = result["nodes"], result["edges"]
    index = {n["id"]: n for n in nodes}
    if len(index) != len(nodes) or len({parameter, review, explanation}) != 3:
        raise ValueError("explicit_unique_model_bindings_required")
    if any(k in index for k in (FALLBACK, SUCCESS, AGGREGATOR)):
        raise ValueError("model_fallback_already_present")
    if GUARD_ID not in index or index[GUARD_ID]["data"].get("code") != guard_source():
        raise ValueError("shared_guard_required")
    for key in (parameter, review, explanation):
        if key not in index or index[key]["data"]["type"] != "llm":
            raise ValueError("explicit_model_node_required")
        if index[key]["data"].get("error_strategy") is not None:
            raise ValueError("existing_model_error_strategy")
    starts = [n for n in nodes if n["data"]["type"] == "start"]
    if len(starts) != 1:
        raise ValueError("one_start_required")
    query = [starts[0]["id"], "query"]
    token = "{{#" + parameter + ".text#}}"
    replacement = "{{#" + AGGREGATOR + ".output.parameters_json#}}"
    def remap(value):
        if type(value) is list:
            return [AGGREGATOR, "output", "parameters_json"] if value == [parameter, "text"] else [remap(x) for x in value]
        if type(value) is dict:
            return {k: remap(v) for k, v in value.items()}
        return value.replace(token, replacement) if type(value) is str else value
    # Preserve the existing contract consumers, including context prompts.
    nodes[:] = [remap(n) for n in nodes]
    index = {n["id"]: n for n in nodes}
    outgoing = [e for e in edges if e["source"] == parameter]
    if not outgoing or any(e.get("sourceHandle", "source") != "source" for e in outgoing):
        raise ValueError("parameter_success_edges_required")
    for edge in outgoing:
        edge["source"] = AGGREGATOR
        edge.setdefault("data", {})["sourceType"] = "variable-aggregator"
    position = index[parameter].get("position", {"x": 0, "y": 0})
    nodes.extend([
        _code(SUCCESS, "提取结果与模型状态", SUCCESS_CODE, {"parameters_json": [parameter, "text"]}, {"packet": "object"}, position),
        _code(FALLBACK, "仅使用原始声明（模型不可用）", guard_source() + FALLBACK_WRAPPER,
              {"query": query}, {"packet": "object"}, position),
        {"id": AGGREGATOR, "type": "custom", "position": dict(position), "data": {
            "type": "variable-aggregator", "title": "参数与可用性合流", "output_type": "object",
            "variables": [[SUCCESS, "packet"], [FALLBACK, "packet"]],
        }},
    ])
    index[parameter]["data"]["error_strategy"] = "fail-branch"
    edges.extend([_edge(parameter, SUCCESS, "llm", "code"), _edge(SUCCESS, AGGREGATOR, "code", "variable-aggregator"),
                  _edge(parameter, FALLBACK, "llm", "code", "fail-branch"),
                  _edge(FALLBACK, AGGREGATOR, "code", "variable-aggregator")])
    for key, note in ((review, REVIEW_NOTE), (explanation, EXPLANATION_NOTE)):
        index[key]["data"].update(error_strategy="default-value",
            default_value=[{"key": "text", "type": "string", "value": note}])
    ends = [n for n in nodes if n["data"]["type"] == "end"]
    if not ends:
        raise ValueError("end_required")
    for end in ends:
        outputs = end["data"].get("outputs", [])
        reports = [v for v in outputs if v.get("variable") == "text" and v.get("value_type") == "string"]
        marker = "acouagent-model-status-" + end["id"]
        if len(reports) != 1 or marker in index or any(v.get("variable") == "model_parameter_extraction_available" for v in outputs):
            raise ValueError("unambiguous_report_output_required")
        incoming = [e for e in edges if e["target"] == end["id"]]
        if not incoming:
            raise ValueError("end_edges_required")
        nodes.append(_code(marker, "报告标记模型可用性", REPORT_CODE,
            {"report": reports[0]["value_selector"], "model_available": [AGGREGATOR, "output", "model_available"]},
            {"report": "string", "model_parameter_extraction_available": "boolean"}, end.get("position", position)))
        for edge in incoming:
            edge["target"] = marker
            edge.setdefault("data", {})["targetType"] = "code"
        edges.append(_edge(marker, end["id"], "code", "end"))
        reports[0]["value_selector"] = [marker, "report"]
        outputs.append({"variable": "model_parameter_extraction_available", "value_type": "boolean",
                        "value_selector": [marker, "model_parameter_extraction_available"]})
    if len({e["id"] for e in edges}) != len(edges):
        raise ValueError("edge_id_collision")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph", required=True)
    parser.add_argument("--expected-graph-sha256", required=True)
    parser.add_argument("--parameter-node", required=True)
    parser.add_argument("--review-node", required=True)
    parser.add_argument("--explanation-node", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    graph = load_json(args.graph)
    actual = hashlib.sha256(json.dumps(graph, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if actual != args.expected_graph_sha256:
        raise ValueError("source_graph_revision_mismatch")
    result = patch(graph, parameter=args.parameter_node, review=args.review_node, explanation=args.explanation_node)
    with Path(args.output).open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()
