"""Untrusted response -> pointer only; never authorizes code or trust flags."""
import json
import uuid
def _unique(pairs):
    obj={}
    for k,v in pairs:
        if k in obj:raise ValueError("duplicate_key")
        obj[k]=v
    return obj
def main(result_json):
    denied={"decision_id":"","generation_run_id":""}
    try:
        # Current Dify MCPTool puts JSON TextContent in its json array.
        if type(result_json) is list:
            if len(result_json)!=1 or type(result_json[0]) is not dict:return denied
            result_json=json.dumps(result_json[0],ensure_ascii=False,allow_nan=False)
        if type(result_json) is not str or len(result_json.encode())>150000:return denied
        body=json.loads(result_json,object_pairs_hook=_unique,
                        parse_constant=lambda _:(_ for _ in ()).throw(ValueError("nonfinite")))
        usage=body["skill_usage"]
        first=usage["decision_id"];second=usage["generation_intent_id"]
        if any(type(v) is not str or str(uuid.UUID(v))!=v for v in (first,second)):return denied
        return {"decision_id":first,"generation_run_id":second}
    except (ValueError,TypeError,KeyError,RecursionError,UnicodeError):return denied
