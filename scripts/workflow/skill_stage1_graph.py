"""Compose stage-one Dify graph changes without publishing or executing code.

Input is an exported JSON graph. Bind node IDs explicitly; never infer them
from titles. Re-run against the original export, not a previously patched graph.
"""
import argparse
import copy
import json
from pathlib import Path
from scripts.workflow import candidate_intake_graph, skill_parameter_contract, skill_retrieval_graph


def compose(graph, branches, search_template, *, retrieval_enabled=False,
            selector_corrections=()):
    if type(branches) is not list or not branches:
        raise ValueError("explicit_branches_required")
    required = {"parameter", "generator", "executor", "adapter", "scenario_type", "prefix"}
    if any(type(b) is not dict or set(b) != required for b in branches):
        raise ValueError("explicit_branch_binding_required")
    for key in ("parameter", "generator", "executor", "adapter", "prefix"):
        values = [b[key] for b in branches]
        if any(type(v) is not str or not v for v in values) or len(set(values)) != len(values):
            raise ValueError("branch_collision")
    data = search_template.get("data", {})
    if data.get("provider_type") != "mcp" or type(data.get("provider_id")) is not str or not data["provider_id"]:
        raise ValueError("explicit_mcp_provider_required")
    result = copy.deepcopy(graph)
    index = {n["id"]: n for n in result["nodes"]}
    # A correction is explicit and must select exactly the extractor belonging
    # to this generator; this prevents first-attempt parameters leaking to retry.
    for correction in selector_corrections:
        if set(correction) != {"generator", "old_parameter"}:
            raise ValueError("explicit_selector_correction_required")
        matches = [b for b in branches if b["generator"] == correction["generator"]]
        if len(matches) != 1:
            raise ValueError("correction_branch_required")
        branch = matches[0]
        old = "{{#" + correction["old_parameter"] + ".text#}}"
        new = "{{#" + branch["parameter"] + ".text#}}"
        prompts = index[branch["generator"]]["data"]["prompt_template"]
        if old == new or sum(p["text"].count(old) for p in prompts) != 1:
            raise ValueError("unique_old_selector_required")
        for prompt in prompts:
            prompt["text"] = prompt["text"].replace(old, new)
    result = skill_parameter_contract.patch(result, [b["parameter"] for b in branches])
    intake = [{k: b[k] for k in ("parameter", "generator", "executor", "adapter", "scenario_type")}
              for b in branches]
    result = candidate_intake_graph.patch(result, intake)
    retrieval = [dict(adapter=b["adapter"], generator=b["generator"], prefix=b["prefix"],
                      context_selector=[b["parameter"], "text"]) for b in branches]
    # Stage one only retrieves metadata; no caller-provided result authorizes code.
    result = skill_retrieval_graph.patch(result, retrieval, search_template,
                                        retrieval_enabled=retrieval_enabled, injection_enabled=False)
    # Dify MCPTool converts JSON TextContent to its json array, not text.
    # Normalize that actual transport output before the string aggregator.
    for branch in branches:
        prefix = branch["prefix"]
        search, merge, response = (prefix + suffix for suffix in ("-search", "-merge", "-response"))
        nodes = {n["id"]: n for n in result["nodes"]}
        if response in nodes:
            raise ValueError("response_node_collision")
        source = (
            "import json\n"
            "def main(result):\n"
            "    fallback = {'skills': [], 'fallback': True, 'trusted_run': False, "
            "'runtime_approved': False, 'parameters_bound': False, 'run_bound': False, "
            "'reason': 'retrieval_failed'}\n"
            "    try:\n"
            "        value = result[0] if type(result) is list and len(result) == 1 else fallback\n"
            "        if type(value) is not dict: value = fallback\n"
            "        text = json.dumps(value, ensure_ascii=False, allow_nan=False)\n"
            "        if len(text.encode('utf-8')) > 8192: text = json.dumps(fallback)\n"
            "        return {'text': text}\n"
            "    except (ValueError, TypeError, OverflowError, RecursionError, UnicodeError):\n"
            "        return {'text': json.dumps(fallback)}\n")
        node = dict(id=response, type="custom", position={"x": 0, "y": 0}, data={
            "type": "code", "title": "MCP检索JSON响应归一", "code_language": "python3",
            "code": source, "variables": [{"variable": "result", "value_selector": [search, "json"]}],
            "outputs": {"text": {"type": "string", "children": None}}})
        edge = next(e for e in result["edges"] if e["source"] == search and e["target"] == merge)
        result["edges"].remove(edge)
        first, second = copy.deepcopy(edge), copy.deepcopy(edge)
        first.update(id=search+"-to-"+response, target=response)
        first["data"]["targetType"] = "code"
        second.update(id=response+"-to-"+merge, source=response)
        second["data"]["sourceType"] = "code"
        result["edges"].extend([first, second])
        result["nodes"].append(node)
        nodes[merge]["data"]["variables"] = [[response, "text"], [prefix+"-fallback", "text"]]
    return result


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def load_json(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError("workflow_file_budget")
    return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite_json")))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph", required=True)
    parser.add_argument("--bindings", required=True,
                        help="JSON object with branches, search_template, selector_corrections")
    parser.add_argument("--output", required=True, help="New JSON graph file; never overwrite")
    parser.add_argument("--enable-retrieval", action="store_true",
                        help="Enable graph lookup only; server flags remain independently required")
    args = parser.parse_args(argv)
    bindings = load_json(args.bindings)
    if set(bindings) != {"branches", "search_template", "selector_corrections"}:
        raise ValueError("explicit_binding_document_required")
    patched = compose(load_json(args.graph), retrieval_enabled=args.enable_retrieval, **bindings)
    payload = json.dumps(patched, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    # Validate/serialize before opening output; refusal leaves existing files intact.
    with Path(args.output).open("xb") as stream:
        stream.write(payload)


if __name__ == "__main__":
    main()
