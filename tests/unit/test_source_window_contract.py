import unittest
from scripts.validation.source_window_contract import inspect_window

class SourceWindowTests(unittest.TestCase):
    def base(self, **changes):
        args=dict(start_s=0., end_s=30., center_s=15., sigma_s=5.,
                  frequency_hz=100000., field_kind="time_resolved", distance_kind="measured")
        args.update(changes)
        return inspect_window(**args)

    def test_covered_boundary(self):
        result=self.base()
        self.assertTrue(result["three_sigma_envelope_covered"])
        self.assertFalse(result["physics_accepted"])
        self.assertFalse(result["trusted_run"])

    def test_actual_truncated_window(self):
        result=self.base(end_s=10., field_kind="temporal_max_abs", distance_kind="theoretical")
        self.assertEqual(result["issues"], ["source_center_outside_window", "three_sigma_envelope_truncated"])

    def test_center_boundary_does_not_mean_full_pulse(self):
        self.assertTrue(self.base(end_s=15.)["source_center_in_window"])
        self.assertFalse(self.base(end_s=15.)["three_sigma_envelope_covered"])

    def test_peak_map_cannot_certify_distance(self):
        self.assertIn("distance_measurement_evidence_missing",
                      self.base(field_kind="temporal_max_abs")["issues"])

    def test_invalid_inputs(self):
        for change in [dict(end_s=0),dict(start_s=-1),dict(sigma_s=0),
                       dict(frequency_hz=-1),dict(center_s=float("nan")),
                       dict(end_s=float("inf")),dict(sigma_s=True),
                       dict(frequency_hz="100"),dict(field_kind="unknown"),
                       dict(distance_kind="self_reported")]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.base(**change)

if __name__ == "__main__":
    unittest.main()
