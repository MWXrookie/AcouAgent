"""Tests for preparation tooling; these never execute a PDE or grant skill trust."""
import ast
import base64
import copy
import hashlib
import io
import json
import unittest
import zlib
from pathlib import Path

import numpy as np

from scripts.validation import skill_learning_probe as probe


class SkillLearningProbeTests(unittest.TestCase):
    def setUp(self):
        self.field = np.ones((128, 128), dtype=np.complex64)

    def response(self, field=None):
        stream = io.BytesIO()
        np.save(stream, self.field if field is None else field, allow_pickle=False)
        data = {"case": "B01", "quantity": "complex_pressure",
                "npy_zlib_base64": base64.b64encode(zlib.compress(stream.getvalue())).decode()}
        return {"exit_code": 0, "timed_out": False,
                "stdout": "__SKILL_PROBE_START__" + json.dumps(data) + "__SKILL_PROBE_END__"}

    def bound_response(self):
        request = probe.make_request("B01")
        response = self.response()
        response["stderr"] = ""
        runtime = {"executor_source_sha256": hashlib.sha256(
            (Path(probe.__file__).resolve().parents[2] / "executor/executor.py").read_bytes()).hexdigest(),
            "python_version": "3.11.17", "python_implementation": "CPython", "machine": "x86_64",
            "library_versions": {"jwave": "0.2.1", "jax": "0.4.38", "jaxlib": "0.4.38",
                                 "jaxdf": "0.2.8", "numpy": "1.26.4"}}
        digest = lambda value: hashlib.sha256(value.encode()).hexdigest()
        response["executor_receipt"] = {"version": "executor-receipt.v1",
            "request_nonce": request["request_nonce"], "code_sha256": digest(request["code"]),
            "stdout_sha256": digest(response["stdout"]), "stderr_sha256": digest(""),
            "exit_code": 0, "timed_out": False, "timeout_seconds": 30, "runtime": runtime,
            "runtime_sha256": digest(json.dumps(runtime, sort_keys=True, separators=(",", ":")))}
        return request, response

    def test_requests_are_executable_syntax_and_reference_blind(self):
        for case in probe.CASES:
            with self.subTest(case=case):
                request = probe.make_request(case)
                ast.parse(request["code"])
                self.assertEqual(request["timeout_seconds"], 30)
                self.assertEqual(len(request["request_nonce"]), 32)
                self.assertNotIn("reference", request["code"].lower())
                self.assertLess(len(request["code"].encode()), 20000)

    def test_identity_comparison_grants_no_trust(self):
        result = probe.compare("B01", self.field, self.field)
        self.assertEqual(result["relative_magnitude_linf"], 0)
        self.assertTrue(result["numeric_pass"])
        self.assertFalse(result["grants_skill_trust"])

    def test_twenty_percent_error_fails(self):
        self.assertFalse(probe.compare("B01", self.field * 1.2, self.field)["numeric_pass"])

    def test_phase_rotation_is_not_an_original_magnitude_failure(self):
        self.assertTrue(probe.compare("B01", self.field * 1j, self.field)["numeric_pass"])

    def test_numerator_crops_pml(self):
        actual = self.field.copy()
        actual[0, 0] = 100
        self.assertTrue(probe.compare("B01", actual, self.field)["numeric_pass"])

    def test_denominator_includes_full_reference(self):
        reference = self.field.copy()
        reference[0, 0] = 100
        actual = reference.copy()
        actual[32, 32] = 1.5
        self.assertAlmostEqual(probe.compare("B01", actual, reference)["relative_magnitude_linf"], .005)

    def test_threshold_is_strict(self):
        actual = np.ones((128, 128), dtype=np.float64)
        actual[32, 32] = 1.01
        self.assertFalse(probe.compare("B01", actual, np.ones_like(actual))["numeric_pass"])

    def test_bad_shapes_rejected(self):
        with self.assertRaises(ValueError):
            probe.compare("B01", self.field[:64], self.field)

    def test_nonfinite_rejected(self):
        self.field[32, 32] = np.nan
        with self.assertRaises(ValueError):
            probe.compare("B01", self.field, np.ones((128, 128)))

    def test_zero_reference_rejected(self):
        with self.assertRaises(ValueError):
            probe.compare("B01", self.field, np.zeros_like(self.field))

    def test_full_complex_export_round_trip(self):
        np.testing.assert_array_equal(probe.actual_field(self.response(), "B01"), self.field)

    def test_failed_or_duplicate_response_rejected(self):
        response = self.response()
        response["timed_out"] = True
        with self.assertRaises(ValueError):
            probe.actual_field(response, "B01")
        response = self.response()
        response["stdout"] *= 2
        with self.assertRaises(ValueError):
            probe.actual_field(response, "B01")

    def test_wrong_case_and_magnitude_only_rejected(self):
        with self.assertRaises(ValueError):
            probe.actual_field(self.response(), "B02")
        with self.assertRaises(ValueError):
            probe.actual_field(self.response(np.ones((128, 128))), "B01")

    def test_oversized_inflated_field_rejected(self):
        with self.assertRaises(ValueError):
            probe.actual_field(self.response(np.ones((1024, 1024), dtype=np.complex128)), "B01")

    def test_internal_receipt_correlated_without_host_attestation(self):
        request, response = self.bound_response()
        result = probe.correlate("B01", request, response)
        self.assertTrue(result["receipt_correlated"])
        self.assertFalse(result["independent_host_attested"])

    def test_changed_output_or_nonce_rejected(self):
        request, response = self.bound_response()
        for change in ("stdout", "nonce"):
            with self.subTest(change=change):
                bad = copy.deepcopy(response)
                if change == "stdout":
                    bad["stdout"] += "changed"
                else:
                    bad["executor_receipt"]["request_nonce"] = "0" * 32
                with self.assertRaises(ValueError):
                    probe.correlate("B01", request, bad)

    def test_different_code_is_not_reviewed_probe(self):
        request, response = self.bound_response()
        request["code"] += "\nprint('changed')\n"
        with self.assertRaises(ValueError):
            probe.correlate("B01", request, response)


if __name__ == "__main__":
    unittest.main()
