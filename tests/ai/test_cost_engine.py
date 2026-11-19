import unittest
from decimal import Decimal

from trendradar.ai.cost_engine import estimate_deepseek_cost


class DeepSeekCostEngineTests(unittest.TestCase):
    def estimate(self, **overrides):
        values = {
            "model": "deepseek/deepseek-v4-pro",
            "prompt_tokens": 1_000_000,
            "completion_tokens": 100_000,
            "prompt_cache_hit_tokens": 200_000,
            "prompt_cache_miss_tokens": 800_000,
            "is_peak": False,
        }
        values.update(overrides)
        return estimate_deepseek_cost(**values)

    def test_calculates_pro_off_peak_cost_with_cache_split(self):
        result = self.estimate()
        self.assertEqual("calculated", result.status)
        self.assertEqual(Decimal("0.03"), result.cache_hit_cost)
        self.assertEqual(Decimal("3.6"), result.cache_miss_cost)
        self.assertEqual(Decimal("1.35"), result.output_cost)
        self.assertEqual(Decimal("4.98"), result.total_cost)
        self.assertEqual("CNY", result.currency)

    def test_peak_rates_are_twice_off_peak(self):
        off_peak = self.estimate()
        peak = self.estimate(is_peak=True)
        self.assertEqual(off_peak.total_cost * 2, peak.total_cost)

    def test_flash_legacy_alias_uses_flash_price(self):
        result = self.estimate(
            model="litellm/deepseek-v4-flash",
            prompt_tokens=1_000_000,
            completion_tokens=100_000,
            prompt_cache_hit_tokens=200_000,
            prompt_cache_miss_tokens=800_000,
        )
        self.assertEqual("deepseek-flash", result.model_key)
        self.assertEqual(Decimal("0.84"), result.total_cost)

    def test_unknown_cache_breakdown_is_partial_not_zero(self):
        result = self.estimate(prompt_cache_hit_tokens=None)
        self.assertEqual("partial", result.status)
        self.assertIsNone(result.total_cost)
        self.assertEqual("token_usage_incomplete", result.reason)

    def test_cache_buckets_must_reconcile_to_prompt_tokens(self):
        result = self.estimate(prompt_cache_hit_tokens=100_000)
        self.assertEqual("partial", result.status)
        self.assertEqual("cache_breakdown_does_not_reconcile", result.reason)

    def test_unknown_peak_period_is_partial(self):
        result = self.estimate(is_peak=None)
        self.assertEqual("partial", result.status)
        self.assertEqual("peak_period_unknown", result.reason)

    def test_unknown_model_is_unsupported(self):
        result = self.estimate(model="openai/gpt-6")
        self.assertEqual("unsupported", result.status)
        self.assertIsNone(result.total_cost)

    def test_rejects_non_boolean_peak_flag(self):
        with self.assertRaises(TypeError):
            self.estimate(is_peak=1)


if __name__ == "__main__":
    unittest.main()
