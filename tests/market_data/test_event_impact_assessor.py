import math
import unittest

from trendradar.market_data.event_impact_assessor import (
    EventImpactInput,
    assess_event_impact,
)


class EventImpactAssessorTests(unittest.TestCase):
    def test_full_dimensions_use_approved_weights(self):
        result = assess_event_impact(EventImpactInput(
            severity=1.0,
            scope=0.8,
            duration=0.6,
            surprise=0.4,
        ))

        self.assertAlmostEqual(result.event_strength, 0.75)
        self.assertEqual(result.assessment_confidence, 1.0)
        self.assertEqual(
            result.observed_dimensions,
            ("severity", "scope", "duration", "surprise"),
        )
        self.assertEqual(result.missing_dimensions, ())

    def test_all_dimensions_at_zero_produce_zero_strength(self):
        result = assess_event_impact(EventImpactInput(
            severity=0.0, scope=0.0, duration=0.0, surprise=0.0,
        ))

        self.assertEqual(result.event_strength, 0.0)
        self.assertEqual(result.assessment_confidence, 1.0)

    def test_missing_dimensions_are_not_treated_as_zero(self):
        result = assess_event_impact(EventImpactInput(severity=0.8))

        self.assertAlmostEqual(result.event_strength, 0.8)
        self.assertEqual(result.assessment_confidence, 0.35)
        self.assertEqual(result.observed_dimensions, ("severity",))
        self.assertEqual(
            result.missing_dimensions,
            ("scope", "duration", "surprise"),
        )

    def test_partial_dimensions_renormalize_observed_weights(self):
        result = assess_event_impact(EventImpactInput(
            severity=0.8,
            duration=0.4,
        ))

        expected = (0.8 * 0.35 + 0.4 * 0.20) / (0.35 + 0.20)
        self.assertAlmostEqual(result.event_strength, expected)
        self.assertAlmostEqual(result.assessment_confidence, 0.55)

    def test_no_observed_dimensions_return_no_strength(self):
        result = assess_event_impact(EventImpactInput())

        self.assertIsNone(result.event_strength)
        self.assertEqual(result.assessment_confidence, 0.0)
        self.assertEqual(result.observed_dimensions, ())
        self.assertEqual(
            result.missing_dimensions,
            ("severity", "scope", "duration", "surprise"),
        )

    def test_source_trust_and_direction_are_not_input_dimensions(self):
        item = EventImpactInput(
            severity=0.5, scope=0.5, duration=0.5, surprise=0.5,
        )
        result = assess_event_impact(item)

        self.assertEqual(result.event_strength, 0.5)
        self.assertTrue(any("不代表利好或利空" in text for text in result.explanation))
        self.assertTrue(any("来源可信度" in text for text in result.explanation))

    def test_rejects_out_of_range_dimension(self):
        for value in (-0.01, 1.01):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    assess_event_impact(EventImpactInput(severity=value))

    def test_rejects_non_finite_dimension(self):
        for value in (math.nan, math.inf, -math.inf):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    assess_event_impact(EventImpactInput(scope=value))

    def test_rejects_boolean_dimension(self):
        with self.assertRaises(TypeError):
            assess_event_impact(EventImpactInput(surprise=True))

    def test_rejects_non_numeric_dimension(self):
        with self.assertRaises(TypeError):
            assess_event_impact(EventImpactInput(duration="high"))

    def test_rejects_wrong_input_type(self):
        with self.assertRaises(TypeError):
            assess_event_impact({"severity": 0.8})


if __name__ == "__main__":
    unittest.main()
