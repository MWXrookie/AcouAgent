"""Pure, idempotent migration of the reviewed AcouAgent workflow graph.

Reads graph JSON on stdin, writes patched JSON on stdout. Does not publish.
Keep a backup and compare-and-swap the database separately during deployment.
"""
import copy
import json
import sys

UNUSED_ANALYSIS = "1783990970058"
START = "1783990934733"


def optimize(graph):
    graph = copy.deepcopy(graph)
    nodes = {}
    for node in graph["nodes"]:
        if node["id"] in nodes:
            previous, current = copy.deepcopy(nodes[node["id"]]), copy.deepcopy(node)
            for candidate in (previous, current):
                for prompt in candidate["data"].get("prompt_template", []):
                    prompt.pop("id", None)
            if previous != current:
                raise ValueError("conflicting duplicate node")
        nodes[node["id"]] = node
    if UNUSED_ANALYSIS in nodes:
        # The analysis result must have no variable consumers.
        for node_id, node in nodes.items():
            if node_id != UNUSED_ANALYSIS and UNUSED_ANALYSIS in json.dumps(node["data"]):
                raise ValueError("analysis output still consumed")
        nodes.pop(UNUSED_ANALYSIS)
    edges, seen = [], set()
    for edge in graph["edges"]:
        if edge["target"] == UNUSED_ANALYSIS:
            continue
        if edge["source"] == UNUSED_ANALYSIS:
            edge["source"] = START
            if "data" in edge:
                edge["data"]["sourceType"] = "start"
        key = (edge["source"], edge["target"], edge.get("sourceHandle"), edge.get("targetHandle"))
        if key not in seen:
            edges.append(edge)
            seen.add(key)
    for node in nodes.values():
        data = node["data"]
        if data.get("type") == "llm" and data["model"].get("name") == "deepseek-coder":
            data["model"]["name"] = "deepseek-v4-flash"
            data["model"]["completion_params"]["temperature"] = 0.2
            data["model"]["completion_params"]["max_tokens"] = 8192
    for node in nodes.values():
        data = node["data"]
        if data.get("type") == "llm" and data.get("title", "").startswith("代码生成") and data["model"].get("name") == "deepseek-v4-flash":
            data["model"]["completion_params"]["thinking"] = False
            data["model"]["completion_params"]["max_tokens"] = 4096
    for node_id, params_id in [("1786091111002", "1783991048409"),
                               ("1786095555006", "1786095555002")]:
        if node_id in nodes:
            nodes[node_id]["data"]["tool_parameters"]["params_json"] = {
                "type": "mixed", "value": "{{#" + params_id + ".text#}}"}
    explanation = nodes.get("1786091111004", {}).get("data", {})
    marker = "# 报告事实边界"
    for prompt in explanation.get("prompt_template", []):
        if prompt.get("role") == "user" and marker not in prompt["text"]:
            prompt["text"] += (
                "\n\\n" + marker + "\n用户需求：{{#1783990934733.query#}}"
                "\n提取参数（需求值，不是实测证明）：{{#1783991048409.text#}}"
                "\n只引用分析中实际提供的数值。理论距离可按c*t计算，但不得冒充实测波前或验证通过。"
                "\n二维点源不得套用三维球面1/r定律；水介质不得使用空气20微帕参考声压。"
                "\n若未定量测量传播距离或衰减，明确写未核验；normal/Q1不等于物理正确。")
    graph["nodes"], graph["edges"] = list(nodes.values()), edges
    return graph


if __name__ == "__main__":
    print(json.dumps(optimize(json.load(sys.stdin)), ensure_ascii=False))
