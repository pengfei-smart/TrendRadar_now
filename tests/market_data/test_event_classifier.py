import unittest

from trendradar.market_data.event_classifier import (
    classify_market_event,
    classify_market_events,
)
from trendradar.market_data.models import MarketEvent


def make_event(title, summary="", event_type="unknown", direction="neutral"):
    return MarketEvent(
        event_id="event-1",
        source="test",
        title=title,
        published_at="2026-11-18T09:00:00+08:00",
        event_type=event_type,
        direction=direction,
        region="US",
        summary=summary,
    )


class EventClassifierTests(unittest.TestCase):
    def test_classifies_monetary_policy(self):
        result = classify_market_event(make_event("美联储宣布降息"))

        self.assertEqual(result.event_category, "monetary_policy")
        self.assertEqual(result.classification_status, "auto_classified")
        self.assertIn("美联储", result.matched_terms)

    def test_classifies_inflation_data_from_summary(self):
        result = classify_market_event(make_event(
            "Monthly data released",
            "US consumer price index CPI rises",
        ))

        self.assertEqual(result.event_category, "inflation_data")
        self.assertEqual(result.classification_status, "auto_classified")

    def test_classifies_employment_data(self):
        result = classify_market_event(make_event("美国非农就业报告公布"))

        self.assertEqual(result.event_category, "employment_data")

    def test_classifies_industry_policy(self):
        result = classify_market_event(make_event("工信部发布产业政策新规"))

        self.assertEqual(result.event_category, "industry_policy")

    def test_classifies_supply_demand(self):
        result = classify_market_event(make_event("锂盐行业出现供应短缺"))

        self.assertEqual(result.event_category, "supply_demand")

    def test_classifies_commodity_price(self):
        result = classify_market_event(make_event("国际原油价格大幅波动"))

        self.assertEqual(result.event_category, "commodity_price")

    def test_classifies_corporate_earnings(self):
        result = classify_market_event(make_event("公司发布年度报告及业绩预告"))

        self.assertEqual(result.event_category, "corporate_earnings")

    def test_unknown_event_is_not_forced_into_a_category(self):
        result = classify_market_event(make_event("公司召开年度股东大会"))

        self.assertEqual(result.event_category, "unknown")
        self.assertEqual(result.classification_status, "unknown")
        self.assertEqual(result.classification_confidence, 0.0)

    def test_english_abbreviations_do_not_match_inside_unrelated_words(self):
        result = classify_market_event(make_event("Happiness improves across the region"))

        self.assertEqual(result.event_category, "unknown")

    def test_classification_does_not_change_direction_or_event(self):
        event = make_event("美联储宣布降息", direction="neutral")
        result = classify_market_event(event)

        self.assertEqual(event.direction, "neutral")
        self.assertEqual(event.event_type, "unknown")
        self.assertEqual(result.event_id, event.event_id)

    def test_batch_classification_preserves_input_order(self):
        events = [
            make_event("美联储宣布降息"),
            make_event("国际原油价格变化"),
        ]
        results = classify_market_events(events)

        self.assertEqual(
            tuple(item.event_category for item in results),
            ("monetary_policy", "commodity_price"),
        )

    def test_rejects_non_market_event(self):
        with self.assertRaises(TypeError):
            classify_market_event({"title": "美联储宣布降息"})


if __name__ == "__main__":
    unittest.main()
