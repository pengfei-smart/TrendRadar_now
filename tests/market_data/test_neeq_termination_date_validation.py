# 本文件测试NEEQ终止挂牌日期语义验证器及禁止映射规则。
"""Tests for pure NEEQ termination date semantic validation."""

import unittest

from trendradar.market_data.neeq_termination_date_validation import (
    validate_neeq_date_mapping,
    validate_neeq_termination_procedure,
)


class NEEQTerminationDateValidationTests(unittest.TestCase):
    def _procedure(self, **overrides):
        value = {
            "procedure_id": "procedure-1",
            "procedure_type": "decision_issued",
            "event_date": "2019-07-19",
            "date_semantics": "decision_issued",
            "date_status": "pending",
        }
        value.update(overrides)
        return value

    def _actual_evidence(self, **overrides):
        value = {
            "evidence_kind": "final_termination_notice",
            "source_authority": "neeq_official",
            "evidence_status": "crosschecked",
            "metadata": {
                "confirmed_legal_fact": True,
                "date_semantics": "actual_termination",
            },
        }
        value.update(overrides)
        return value

    def test_valid_decision_issued_procedure_passes(self):
        result = validate_neeq_termination_procedure(self._procedure())
        self.assertTrue(result["valid"])
        self.assertEqual(result["errors"], [])

    def test_procedure_type_and_date_semantics_must_match(self):
        result = validate_neeq_termination_procedure(self._procedure(
            procedure_type="conditional_termination",
        ))
        self.assertFalse(result["valid"])
        self.assertIn(
            "procedure_semantics_mismatch",
            {item["code"] for item in result["errors"]},
        )

    def test_invalid_calendar_date_is_rejected(self):
        result = validate_neeq_termination_procedure(self._procedure(
            event_date="2019-02-30",
        ))
        self.assertFalse(result["valid"])
        self.assertIn("invalid_event_date", {item["code"] for item in result["errors"]})

    def test_conditional_termination_is_warning_not_actual_fact(self):
        result = validate_neeq_termination_procedure(self._procedure(
            procedure_type="conditional_termination",
            date_semantics="conditional_termination",
            event_date="2019-07-29",
            date_status="confirmed",
        ))
        self.assertTrue(result["valid"])
        self.assertIn(
            "conditional_date_not_actual",
            {item["code"] for item in result["warnings"]},
        )

    def test_announcement_date_is_not_actual_fact(self):
        result = validate_neeq_termination_procedure(self._procedure(
            procedure_type="final_termination",
            date_semantics="announcement",
            event_date="2020-05-12",
        ))
        self.assertTrue(result["valid"])
        self.assertIn(
            "announcement_date_not_actual",
            {item["code"] for item in result["warnings"]},
        )

    def test_actual_termination_requires_direct_confirmed_evidence(self):
        result = validate_neeq_termination_procedure(self._procedure(
            procedure_type="final_termination",
            date_semantics="actual_termination",
            event_date="2019-09-17",
            date_status="confirmed",
        ))
        self.assertFalse(result["valid"])
        self.assertIn(
            "actual_termination_evidence_required",
            {item["code"] for item in result["errors"]},
        )

    def test_actual_termination_passes_only_with_explicit_direct_evidence(self):
        result = validate_neeq_termination_procedure(
            self._procedure(
                procedure_type="final_termination",
                date_semantics="actual_termination",
                event_date="2019-09-17",
                date_status="confirmed",
            ),
            self._actual_evidence(),
        )
        self.assertTrue(result["valid"])
        self.assertEqual(result["errors"], [])

    def test_sponsor_relay_cannot_confirm_actual_termination(self):
        evidence = self._actual_evidence(source_authority="sponsor_official")
        result = validate_neeq_termination_procedure(
            self._procedure(
                procedure_type="final_termination",
                date_semantics="actual_termination",
                event_date="2019-09-17",
                date_status="confirmed",
            ),
            evidence,
        )
        self.assertFalse(result["valid"])
        self.assertIn(
            "actual_termination_authority_untrusted",
            {item["code"] for item in result["errors"]},
        )

    def test_interface_candidate_cannot_map_to_delisting_date(self):
        result = validate_neeq_date_mapping(
            "official_result_api_candidate", "delisting_date",
        )
        self.assertFalse(result["valid"])
        self.assertEqual(
            result["errors"][0]["code"],
            "unsafe_actual_termination_mapping",
        )

    def test_conditional_date_cannot_map_to_actual_termination(self):
        result = validate_neeq_date_mapping(
            "conditional_termination", "actual_termination_date",
        )
        self.assertFalse(result["valid"])

    def test_actual_date_mapping_requires_explicit_confirmation_flag(self):
        denied = validate_neeq_date_mapping(
            "actual_termination", "delisting_date",
            source_is_confirmed_actual=False,
        )
        allowed = validate_neeq_date_mapping(
            "actual_termination", "delisting_date",
            source_is_confirmed_actual=True,
        )
        self.assertFalse(denied["valid"])
        self.assertTrue(allowed["valid"])

    def test_transfer_exit_warns_against_automatic_actual_date_mapping(self):
        result = validate_neeq_termination_procedure(self._procedure(
            procedure_type="transfer_exit",
            date_semantics="transfer_exit",
            event_date="2019-10-01",
        ))
        self.assertTrue(result["valid"])
        self.assertIn(
            "registration_exit_not_actual",
            {item["code"] for item in result["warnings"]},
        )


if __name__ == "__main__":
    unittest.main()
