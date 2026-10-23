"""Preparation-only Helmholtz probe: emit a request or score real executor output.

Never calls a model, grants skill trust, or writes a production database.
Reference arrays are read ONLY by the external scorer, never solver code.
"""
from __future__ import annotations

import argparse
import ast
import base64
import hashlib
import io
import json
from pathlib import Path
import re
import uuid
import zlib

import numpy as np

from app.executor_evidence import verify_executor_receipt

CASES = {"B01": (1_500_000.0, (32, 32), 0.01),
         "B02": (1_500_000.0, (32, 32), 0.01),
         "B03": (1_000_000.0, (32, 64), 0.03)}


def solver_code(case: str) -> str:
    omega, source, _ = CASES[case]
    # Version-sensitive call is intentionally part of the real executor probe.
    material = {
        "B01": "",
        "B02": "c = jw.FourierSeries(jnp.full(N, 1500.).at[50:90, 32:100].set(2300.), domain)",
        "B03": "rho = jw.FourierSeries(jnp.full(N, 1000.).at[64:, :].set(2000.), domain)",
    }[case]
    return f'''import base64, io, json, zlib
import numpy as np
import jax.numpy as jnp
import jwave as jw
from jwave.acoustics import helmholtz_solver
N = (128, 128)
domain = jw.Domain(N, (0.001, 0.001))
c, rho = 1500., 1000.
{material}
medium = jw.Medium(domain, sound_speed=c, density=rho, attenuation=0., pml_size=16)
grid = jnp.zeros(N, dtype=jnp.complex64).at[{source[0]}, {source[1]}].set(1.)
src = jw.FourierSeries(grid, domain)
u = helmholtz_solver(medium, {omega!r}, src, method="gmres", tol=1e-5)
field = np.asarray(u.on_grid).reshape(N).astype(np.complex64)
assert np.isfinite(field).all()
buf = io.BytesIO()
np.save(buf, field, allow_pickle=False)
blob = base64.b64encode(zlib.compress(buf.getvalue())).decode("ascii")
print("__SKILL_PROBE_START__" + json.dumps({{"case": "{case}", "quantity": "complex_pressure", "npy_zlib_base64": blob}}) + "__SKILL_PROBE_END__")
print("__ACOU_FIELD_START__")
print(json.dumps({{"data": np.abs(field).tolist(), "shape": list(N), "kind": "steady_state_amplitude", "downsample": 1}}))
print("__ACOU_FIELD_END__")
'''


def make_request(case: str) -> dict:
    code = solver_code(case)
    ast.parse(code)
    assert len(code.encode()) <= 20_000
    return {"code": code, "timeout_seconds": 30, "request_nonce": uuid.uuid4().hex}


def correlate(case: str, request: dict, response: dict) -> dict:
    """Correlate a saved internal response; not independent host attestation."""
    if request.get("code") != solver_code(case) or request.get("timeout_seconds") != 30:
        raise ValueError("request differs from the reviewed probe")
    checked = verify_executor_receipt(response.get("executor_receipt"), response,
                                      request["code"], 30, request.get("request_nonce"))
    if checked["matched"] is not True:
        raise ValueError("executor receipt mismatch")
    runtime = checked["receipt"]["runtime"]
    source = Path(__file__).resolve().parents[2] / "executor" / "executor.py"
    if runtime["executor_source_sha256"] != hashlib.sha256(source.read_bytes()).hexdigest():
        raise ValueError("executor source differs from the checked out baseline")
    if runtime["library_versions"] != {"jwave": "0.2.1", "jax": "0.4.38",
                                       "jaxlib": "0.4.38", "jaxdf": "0.2.8", "numpy": "1.26.4"}:
        raise ValueError("executor library baseline mismatch")
    return {"receipt_correlated": True, "runtime": runtime,
            "independent_host_attested": False}


def actual_field(response: dict, case: str) -> np.ndarray:
    if response.get("exit_code") != 0 or response.get("timed_out") is not False:
        raise ValueError("executor did not complete successfully")
    stdout = response.get("stdout", "")
    if not isinstance(stdout, str) or len(stdout.encode()) > 1024 * 1024:
        raise ValueError("output budget")
    matches = re.findall(r"__SKILL_PROBE_START__(.*?)__SKILL_PROBE_END__", stdout)
    if len(matches) != 1:
        raise ValueError("one complete field required")
    payload = json.loads(matches[0])
    if payload.get("case") != case or payload.get("quantity") != "complex_pressure":
        raise ValueError("case or quantity mismatch")
    compressed = base64.b64decode(payload["npy_zlib_base64"], validate=True)
    decompressor = zlib.decompressobj()
    raw = decompressor.decompress(compressed, 128 * 128 * 16 + 1024)
    if not decompressor.eof or decompressor.unused_data or decompressor.unconsumed_tail:
        raise ValueError("invalid or oversized compressed array")
    field = np.load(io.BytesIO(raw), allow_pickle=False)
    if field.shape != (128, 128) or not np.iscomplexobj(field) or not np.isfinite(field).all():
        raise ValueError("full finite complex 128x128 field required")
    return field


def compare(case: str, actual: np.ndarray, reference: np.ndarray) -> dict:
    if actual.shape != (128, 128) or reference.shape != actual.shape:
        raise ValueError("shape mismatch; interpolation forbidden")
    if not np.isfinite(actual).all() or not np.isfinite(reference).all():
        raise ValueError("nonfinite field")
    denominator = float(np.abs(reference).max())
    if denominator <= 0:
        raise ValueError("zero reference")
    error = float(np.abs(np.abs(actual) - np.abs(reference))[16:-16, 16:-16].max() / denominator)
    tolerance = CASES[case][2]
    return {"case": case, "relative_magnitude_linf": error,
            "numerator_pml_crop": 16, "denominator": "full_reference_max",
            "threshold_strict": tolerance, "numeric_pass": error < tolerance,
            "phase_compared": False, "actual_peak": float(np.abs(actual).max()),
            "reference_peak": denominator, "grants_skill_trust": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=CASES, required=True)
    parser.add_argument("--emit-request", type=Path)
    parser.add_argument("--response", type=Path)
    parser.add_argument("--request", type=Path)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--reference-sha256")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.emit_request:
        args.emit_request.write_text(json.dumps(make_request(args.case)), encoding="utf-8")
        return 0
    if not all((args.request, args.response, args.reference, args.reference_sha256)):
        parser.error("scoring requires --request, --response, --reference and --reference-sha256")
    reference_bytes = args.reference.read_bytes()
    if hashlib.sha256(reference_bytes).hexdigest() != args.reference_sha256:
        raise ValueError("reference SHA256 mismatch")
    response = json.loads(args.response.read_text(encoding="utf-8-sig"))
    binding = correlate(args.case, json.loads(args.request.read_text(encoding="utf-8-sig")), response)
    result = compare(args.case, actual_field(response, args.case), np.load(io.BytesIO(reference_bytes), allow_pickle=False))
    result.update(binding, reference_sha256=args.reference_sha256,
                  response_sha256=hashlib.sha256(args.response.read_bytes()).hexdigest(),
                  duration_ms=response.get("duration_ms"))
    text = json.dumps(result, indent=2, allow_nan=False)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if result["numeric_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
