"""模型无关的自动纠错：根据执行失败结果返回修正代码。"""

from typing import Any

from app import cache_store, model_client


def _truncate_utf8(value: object, limit: int, *, keep_tail: bool = False) -> str:
    """Return a UTF-8 byte-bounded string without splitting a code point."""
    text = value if isinstance(value, str) else str(value or "")
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    bounded = encoded[-limit:] if keep_tail else encoded[:limit]
    return bounded.decode("utf-8", errors="ignore")


def _fix_prompt(
    code: str, result: dict[str, Any], requirements_context: str = ""
) -> tuple[str, str | None]:
    """Build one bounded retry prompt and return its cache signature."""
    stderr = result.get("stderr", "")
    stdout = result.get("stdout", "")
    exit_code = result.get("exit_code")
    error_sig = cache_store._extract_error_signature(stderr, exit_code, stdout)
    cache_hint = cache_store._query_cache(error_sig) if error_sig else None

    prompt = (
        "You are a Python debugging assistant. The following Python code was executed "
        "in a container with jwave, jax, jaxlib, jaxdf, equinox, and jaxtyping "
        "installed. JAX runs CPU-only. The code failed. Your job: return ONLY the "
        "corrected Python code. No markdown fences, no explanations — just raw, "
        "runnable Python.\n\n"
    )
    if requirements_context:
        prompt += (
            "REQUIREMENTS THAT MUST BE PRESERVED:\n"
            + _truncate_utf8(requirements_context, 16000)
            + "\n\n"
        )

    if cache_hint:
        prompt += (
            "**KNOWN FIX EXPERIENCE** (confidence: {:.0f}%): {}"
            " If applicable, apply this fix directly.\n\n"
        ).format(cache_hint["confidence"] * 100, cache_hint["fix_hint"])
    else:
        prompt += (
            "**jwave common error cheat sheet (match by error message)**:\n"
            "- AttributeError: has no attribute 'shape' and object is FourierSeries → use .params.shape\n"
            "- AttributeError: has no attribute 'data' → use .params\n"
            "- AttributeError: has no attribute 't' → use .to_array()\n"
            "- AttributeError: 'FourierSeries' object has no attribute 'from_array' → use FourierSeries(data, domain)\n"
            "- TypeError: Sources.__init__() takes N positional arguments → use positional args, no keyword args\n"
            "- TypeError: signals must be array-like → signals must be 2D jnp array, shape=(num_sources, Nt)\n"
            "- TypeError: positions must be → positions must be tuple of 1D arrays\n"
            "- **CRITICAL - Sources returns all zeros**: positions must be INTEGERS (int32), NOT floats. "
            "Use jnp.array([64]) not jnp.array([64.0]). Float positions cause JAX .at[] index to fail silently in JIT, producing zero output.\n"
            "- **CRITICAL - pressure.params[0] is t=0 (all zeros)!**: simulate_wave_propagation returns a FourierSeries "
            "with shape (Nt, Nx, Ny, 1). params[0] extracts ONLY the first time step (t=0) where nothing has propagated yet. "
            "Correct: use jnp.max(jnp.abs(p.params)) for global max, or p.params[-1] for the final frame.\n"
            "- **CRITICAL - .max() returns 0 on negative grids**: don't use x.max() or pressure.max(). "
            "ALWAYS use jnp.max(jnp.abs(x)). This is a JAX behavior where .max() returns 0 when all values are negative.\n"
            "- **CRITICAL - p0/initial pressure returns all zeros**: if the user asked for \"initial pressure\", "
            "\"Gaussian pulse\", \"ring\", \"ellipse\", or \"p0\", the code should use simulate_wave_propagation(medium, time_axis, p0=p0) "
            "and NOT create Sources. Putting the Gaussian pressure into Sources() will produce zero output because Sources "
            "expects time-domain signals at point positions, not spatial pressure distributions. "
            "Fix: remove Sources entirely, create p0=FourierSeries(pressure_array, domain), then call "
            "simulate_wave_propagation with p0=p0 (no u0!). Use jnp.linspace + jnp.meshgrid to build the grid coordinates.\n"
            "- **CRITICAL - wrong argument name for initial pressure**: the ONLY valid kwarg is p0. "
            "simulate_wave_propagation does NOT accept initial_pressure=, initi..., pressure_0=, or p0_field=. "
            "TypeError like 'unexpected keyword argument' → rename to p0.\n"
            "- **CRITICAL - ring/ellipse p0 geometry**: build the ring with meshgrid coordinates:\n"
            "  x = jnp.linspace(0, Nx*dx, Nx); y = jnp.linspace(0, Ny*dx, Ny); XX, YY = jnp.meshgrid(x, y, indexing='ij')\n"
            "  r = jnp.sqrt((XX-cx)**2 + (YY-cy)**2)\n"
            "  ring: p0_grid = A*jnp.exp(-((r-R0)**2)/(2*sigma**2))  (R0=ring radius)\n"
            "  ellipse: p0_grid = A*jnp.exp(-(((XX-cx)/a)**2 + ((YY-cy)/b)**2) / 2)\n"
            "  p0 = FourierSeries(p0_grid, domain)  # shape (Nx, Ny), then simulate_wave_propagation(..., p0=p0)\n"
            "- **CRITICAL - broadcast_shapes error**: the p0 field shape must match the domain grid (Nx, Ny). "
            "Construct p0 as a grid array of shape (Nx, Ny), then p0 = FourierSeries(grid_array, domain). "
            "Use jnp.meshgrid(x, y, indexing='ij') for correct array ordering.\n"
            "- **CRITICAL - signal shape mismatch / empty signal**: NEVER create jnp.zeros((0, Nt)) as a placeholder "
            "for no-source simulations. If no Sources are needed, simply omit the parameter: "
            "p = simulate_wave_propagation(medium, time_axis, p0=p0).\n\n"
        )

    prompt += (
        "EXECUTION RESULT:\n"
        f"- exit_code: {result.get('exit_code')}\n"
        f"- timed_out: {result.get('timed_out')}\n"
        f"- stdout (bounded tail):\n{_truncate_utf8(result.get('stdout', ''), 12000, keep_tail=True)}\n"
        f"- stderr (bounded tail):\n{_truncate_utf8(result.get('stderr', ''), 12000, keep_tail=True)}\n"
        "\nFAILED CODE:\n"
        f"```python\n{_truncate_utf8(code, 20000)}\n```\n"
        "\nReturn ONLY the fixed Python code:"
    )
    return prompt, error_sig


def _clean_fixed_code(fixed: str) -> str:
    for prefix in ("```python\n", "```python", "```\n", "```"):
        if fixed.startswith(prefix):
            fixed = fixed[len(prefix):]
    if fixed.endswith("\n```"):
        fixed = fixed[:-4]
    elif fixed.endswith("```"):
        fixed = fixed[:-3]
    return fixed.strip()


def _llm_fix_code(
    profile_id: str | None, code: str, result: dict[str, Any], run_id: str | None = None
) -> str:
    """Synchronous retry entry retained for MCP tool callers."""
    prompt, _ = _fix_prompt(code, result)
    fixed = model_client.complete_sync(
        profile_id,
        "retry",
        [{"role": "user", "content": prompt}],
        run_id=run_id,
    )["content"]
    return _clean_fixed_code(fixed)


async def fix_code(
    profile_id: str | None,
    code: str,
    result: dict[str, Any],
    *,
    run_id: str | None = None,
    requirements_context: str = "",
) -> dict[str, str | None]:
    """Async retry entry for local orchestration, including cache lookup metadata."""
    prompt, error_signature = _fix_prompt(code, result, requirements_context)
    response = await model_client.complete(
        profile_id,
        "retry",
        [{"role": "user", "content": prompt}],
        run_id=run_id,
    )
    return {
        "code": _clean_fixed_code(response["content"]),
        "error_signature": error_signature,
    }
