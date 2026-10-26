"""Real managed execution and model repair twice; observe the second cache lookup.

This is a code-repair regression, not a physical benchmark or skill admission.
Requires an explicitly disposable gateway and an already configured model profile.
"""
import argparse
import json
import math
import os

from app import cache_store, llm, tools


def run(profile):
    if os.environ.get("SKILL_TEST_DISPOSABLE") != "true":
        raise ValueError("explicit_disposable_gateway_required")
    functions = {}
    class Registry:
        def tool(self, fn):
            functions[fn.__name__] = fn
            return fn
    tools.register(Registry())
    original = llm._safe_query_cache
    lookups = []
    def observe(signature):
        result = original(signature)
        lookups.append(result is not None)
        return result
    llm._safe_query_cache = observe
    records = []
    try:
        for _ in range(2):
            result = functions["run_jwave_code_with_retry"](
                "import numppy as np\nprint(float(np.linalg.norm(np.arange(4))))\n",
                timeout_seconds=15, max_retries=1, model_profile=profile)
            assert result["exit_code"] == 0 and result["timed_out"] is False
            assert result["total_attempts"] == 2 and result["history"][0]["exit_code"] != 0
            assert math.isclose(float(result["stdout"].strip()), math.sqrt(14), rel_tol=1e-12)
            records.append(dict(run_id=result["run_id"], attempts=result["total_attempts"],
                                stdout=result["stdout"].strip(), exit_code=result["exit_code"]))
        assert lookups == [False, True], lookups
        stats = cache_store.get_cache_stats()
        return dict(kind="real_execution_and_model_cache_repair", runs=records,
                    observed_lookup_hits=lookups, stats=stats,
                    physical_benchmark=False, grants_scientific_quality=False, production_database_writes=0)
    finally:
        llm._safe_query_cache = original


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-profile", required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.model_profile), ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
