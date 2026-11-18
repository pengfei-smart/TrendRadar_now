import unittest

from trendradar.market_data.event_impact_rubric import (
    EVENT_IMPACT_RUBRIC_VERSION,
    get_dimension_rubric,
    get_event_impact_rubric,
)


class EventImpactRubricTests(unittest.TestCase):
    def test_rubric_has_stable_version_and_four_dimensions(self):
        rubric = get_event_impact_rubric()

        self.assertEqual(EVENT_IMPACT_RUBRIC_VERSION, "event-impact-rubric-v1")
        self.assertEqual(
            tuple(item.dimension for item in rubric),
            ("severity", "scope", "duration", "surprise"),
        )

    def test_dimension_weights_match_assessor_and_sum_to_one(self):
        rubric = get_event_impact_rubric()

        self.assertEqual(
            tuple((item.dimension, item.weight) for item in rubric),
            (
                ("severity", 0.35),
                ("scope", 0.25),
                ("duration", 0.20),
                ("surprise", 0.20),
            ),
        )
        self.assertAlmostEqual(sum(item.weight for item in rubric), 1.0)

    def test_each_dimension_has_five_ordered_anchor_points(self):
        for item in get_event_impact_rubric():
            with self.subTest(dimension=item.dimension):
                self.assertEqual(
                    tuple(anchor.score for anchor in item.anchors),
                    (0.0, 0.25, 0.5, 0.75, 1.0),
                )
                self.assertTrue(all(anchor.label for anchor in item.anchors))
                self.assertTrue(all(anchor.description for anchor in item.anchors))

    def test_dimension_lookup_is_case_and_whitespace_tolerant(self):
        self.assertEqual(
            get_dimension_rubric("  SURPRISE ").dimension,
            "surprise",
        )

    def test_dimension_lookup_rejects_unknown_dimension(self):
        with self.assertRaises(ValueError):
            get_dimension_rubric("relevance")

    def test_dimension_lookup_rejects_non_string(self):
        with self.assertRaises(TypeError):
            get_dimension_rubric(None)

    def test_surprise_requires_a_pre_event_expectation_baseline(self):
        rubric = get_dimension_rubric("surprise")

        self.assertTrue(any(
            "事前" in item for item in rubric.evidence_requirements
        ))
        self.assertTrue(any(
            "必须返回 None" in item for item in rubric.cautions
        ))

    def test_duration_is_not_stock_price_persistence(self):
        rubric = get_dimension_rubric("duration")

        self.assertTrue(any(
            "股价走势持续时间" in item for item in (rubric.purpose,)
        ))
        self.assertTrue(any(
            "股价上涨持续时间" in item for item in rubric.cautions
        ))


if __name__ == "__main__":
    unittest.main()
