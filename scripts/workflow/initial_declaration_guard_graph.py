"""Add a shared, fail-closed initial-declaration guard to an exported Dify graph.

Offline only. The portable guard embeds the current canonical pure-Python
validator, without a second copy of physical rules or any reference answers.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from scripts.workflow.skill_stage1_graph import load_json

GUARD_ID = "acouagent-initial-declaration-guard-v1"
_WRAPPER = '''
import json

def _unique_declaration(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("explicit_initial_duplicate_key")
        result[key] = value
    return result

def _nonfinite_declaration(value):
    raise ValueError("explicit_initial_nonfinite")

def main(query):
    if type(query) is not str or len(query.encode("utf-8")) > 65536:
        raise ValueError("initial_declaration_query_budget")
    start = query.find("{")
    if start < 0:
        return {"checked": True}
    text = query[start:]
    marked = any(source in text for source in INITIAL_SOURCES)
    try:
        if len(text.encode("utf-8")) > 8192:
            raise ValueError("initial_declaration_json_budget")
        value = json.loads(text, object_pairs_hook=_unique_declaration,
                           parse_constant=_nonfinite_declaration)
    except (ValueError, TypeError, RecursionError, UnicodeError):
        if marked:
            raise ValueError("explicit_initial_declaration_invalid") from None
        return {"checked": True}
    params = value.get("simulation_params", value) if type(value) is dict else None
    if type(params) is not dict or not is_initial(params):
        if marked:
            raise ValueError("explicit_initial_declaration_invalid")
        return {"checked": True}
    checked = validate_initial(params)
    if checked["valid"] is not True:
        fields = sorted({item["field"] for item in checked["errors"]})
        raise ValueError("explicit_initial_contract_invalid:" + ",".join(fields))
    return {"checked": True}
'''


def guard_source() -> str:
    validator = Path(__file__).resolve().parents[2] / "app" / "initial_pressure_validation.py"
    return validator.read_text(encoding="utf-8") + "\n" + _WRAPPER


def patch(graph: dict, *, native_deepseek_flash: bool = False) -> dict:
    result = copy.deepcopy(graph)
    nodes, edges = result["nodes"], result["edges"]
    index = {node["id"]: node for node in nodes}
    if len(index) != len(nodes):
        raise ValueError("duplicate_node_id")
    starts = [node for node in nodes if node["data"]["type"] == "start"]
    if len(starts) != 1:
        raise ValueError("one_start_required")
    start = starts[0]
    if not any(v.get("variable") == "query" for v in start["data"].get("variables", [])):
        raise ValueError("query_input_required")
    data = {
        "type": "code", "title": "原始初压声明校验（不补参）", "code_language": "python3",
        "code": guard_source(),
        "variables": [{"variable": "query", "value_selector": [start["id"], "query"]}],
        "outputs": {"checked": {"type": "boolean", "children": None}},
    }
    if GUARD_ID in index:
        if index[GUARD_ID]["data"] != data:
            raise ValueError("guard_node_drifted")
        outgoing = [e for e in edges if e["source"] == start["id"]]
        incoming = [e for e in edges if e["target"] == GUARD_ID]
        if (len(outgoing) != 1 or outgoing[0]["target"] != GUARD_ID
                or len(incoming) != 1 or incoming[0] != outgoing[0]
                or not any(e["source"] == GUARD_ID for e in edges)):
            raise ValueError("guard_edges_drifted")
    else:
        outgoing = [e for e in edges if e["source"] == start["id"]]
        if not outgoing or any(e["target"] == start["id"] for e in edges):
            raise ValueError("start_edges_invalid")
        first = copy.deepcopy(outgoing[0])
        first.update(id=start["id"] + "-to-" + GUARD_ID, target=GUARD_ID)
        if any(e["id"] == first["id"] for e in edges):
            raise ValueError("guard_edge_collision")
        first.setdefault("data", {}).update(sourceType="start", targetType="code")
        for edge in outgoing:
            edge["source"] = GUARD_ID
            edge.setdefault("data", {})["sourceType"] = "code"
        position = dict(start.get("position", {"x": 0, "y": 0}))
        position["x"] += 160
        nodes.append({"id": GUARD_ID, "type": "custom", "position": position, "data": data})
        edges.append(first)
    if native_deepseek_flash:
        for node in nodes:
            model = node["data"].get("model", {})
            if node["data"]["type"] != "llm" or model.get("name") != "deepseek-v4-flash":
                continue
            if model.get("provider") != "langgenius/deepseek/deepseek":
                raise ValueError("unexpected_deepseek_provider")
            model["name"] = "deepseek-flash"
    return result


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--native-deepseek-flash", action="store_true")
    args = parser.parse_args(argv)
    result = patch(load_json(args.graph), native_deepseek_flash=args.native_deepseek_flash)
    encoded = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    with Path(args.output).open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(encoded)


if __name__ == "__main__":
    main()
