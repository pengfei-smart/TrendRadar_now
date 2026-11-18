import unittest

from trendradar.market_data.sector_keyword_mapper import (
    discover_industry_keyword_evidence,
    discover_industry_keyword_evidence_from_storage,
    build_industry_keyword_coverage_report,
)


class SectorKeywordMapperTests(unittest.TestCase):
    def setUp(self):
        self.sectors = [
            {"sector_code": "SW1:801010", "sector_name": "农林牧渔"},
            {"sector_code": "SW2:801016", "sector_name": "种植业"},
            {"sector_code": "SW2:801017", "sector_name": "养殖业"},
            {"sector_code": "SW2:801014", "sector_name": "饲料"},
            {"sector_code": "SW2:801018", "sector_name": "动物保健Ⅱ"},
            {"sector_code": "SW2:801033", "sector_name": "化学原料"},
            {"sector_code": "SW2:801034", "sector_name": "化学制品"},
        ]

    def test_matches_multiple_level_two_industries_by_stable_code(self):
        evidence = discover_industry_keyword_evidence(
            "生猪养殖景气回升，豆粕价格上涨，同时磷化工产品涨价。",
            self.sectors,
            source="test-news",
            source_url="https://example.invalid/news",
        )
        by_code = {item.target_code: item for item in evidence}
        self.assertEqual(
            set(by_code),
            {"SW2:801017", "SW2:801014", "SW2:801033"},
        )
        self.assertEqual(by_code["SW2:801017"].mapping_type, "industry_keyword")
        self.assertIn("生猪养殖", by_code["SW2:801017"].evidence)
        self.assertEqual(by_code["SW2:801017"].source, "test-news")
        self.assertEqual(
            by_code["SW2:801017"].source_url,
            "https://example.invalid/news",
        )

    def test_official_level_two_name_provides_baseline_coverage(self):
        sectors = self.sectors + [
            {"sector_code": "SW2:801076", "sector_name": "轨交设备Ⅱ"},
        ]
        evidence = discover_industry_keyword_evidence(
            "轨交设备Ⅱ行业景气变化", sectors,
        )
        self.assertEqual([item.target_code for item in evidence], ["SW2:801076"])
        self.assertEqual(evidence[0].confidence, 0.56)
        self.assertIn("轨交设备Ⅱ", evidence[0].evidence)

    def test_first_technology_batch_matches_eight_industries_by_code(self):
        sectors = [
            {"sector_code": "SW2:801081", "sector_name": "半导体"},
            {"sector_code": "SW2:801083", "sector_name": "元件"},
            {"sector_code": "SW2:801084", "sector_name": "光学光电子"},
            {"sector_code": "SW2:801085", "sector_name": "消费电子"},
            {"sector_code": "SW2:801102", "sector_name": "通信设备"},
            {"sector_code": "SW2:801737", "sector_name": "电池"},
            {"sector_code": "SW2:801735", "sector_name": "光伏设备"},
            {"sector_code": "SW2:801738", "sector_name": "电网设备"},
        ]
        text = (
            "晶圆代工和HBM需求增长，PCB与MLCC订单回暖；"
            "OLED面板、折叠屏手机出货增加，CPO光模块扩产；"
            "固态电池、TOPCon光伏组件及特高压变压器项目推进。"
        )
        evidence = discover_industry_keyword_evidence(text, sectors)
        by_code = {item.target_code: item for item in evidence}
        self.assertEqual(set(by_code), {item["sector_code"] for item in sectors})
        for item in evidence:
            self.assertGreaterEqual(item.confidence, 0.68)
            self.assertEqual(item.direction, "neutral")
            self.assertFalse(item.verified)

    def test_ambiguous_generic_words_do_not_expand_to_adjacent_industries(self):
        sectors = [
            {"sector_code": "SW2:801083", "sector_name": "元件"},
            {"sector_code": "SW2:801084", "sector_name": "光学光电子"},
            {"sector_code": "SW2:801085", "sector_name": "消费电子"},
            {"sector_code": "SW2:801102", "sector_name": "通信设备"},
        ]
        self.assertEqual(
            discover_industry_keyword_evidence("苹果价格上涨，普通服务器需求增加", sectors),
            [],
        )
        evidence = discover_industry_keyword_evidence(
            "CPO光模块订单增长，PCB厂商扩产", sectors,
        )
        self.assertEqual(
            {item.target_code for item in evidence},
            {"SW2:801083", "SW2:801102"},
        )

    def test_reviewed_semantic_terms_keep_higher_confidence(self):
        evidence = discover_industry_keyword_evidence(
            "猪价上涨，能繁母猪存栏变化", self.sectors,
        )
        self.assertEqual([item.target_code for item in evidence], ["SW2:801017"])
        self.assertEqual(evidence[0].confidence, 0.70)
        self.assertIn("能繁母猪", evidence[0].evidence)

    def test_coverage_report_separates_reviewed_terms_from_official_names(self):
        sectors = self.sectors + [
            {"sector_code": "SW2:801012", "sector_name": "农产品加工"},
            {"sector_code": "SW2:801076", "sector_name": "轨交设备Ⅱ"},
            {"sector_code": "SW1:801080", "sector_name": "电子"},
        ]
        report = build_industry_keyword_coverage_report(sectors)
        self.assertEqual(report["total_level_two_sectors"], 8)
        self.assertEqual(report["reviewed_terms_covered"], 7)
        self.assertEqual(report["official_name_only"], 1)
        baseline_only = next(
            row for row in report["sectors"]
            if row["sector_code"] == "SW2:801076"
        )
        self.assertEqual(baseline_only["coverage"], "official_name_only")
        self.assertEqual(baseline_only["reviewed_term_count"], 0)

    def test_storage_adapter_uses_database_industry_codes_and_provenance(self):
        import tempfile
        from pathlib import Path

        from trendradar.market_data.storage import MarketStorage

        with tempfile.TemporaryDirectory() as tmp:
            storage = MarketStorage(str(Path(tmp) / "market.db"))
            with storage.connect() as connection:
                connection.executemany(
                    """INSERT INTO sector_master
                    (sector_code,sector_name,sector_type,parent_sector_code,provider)
                    VALUES(?,?,?,?,?)""",
                    [
                        ("SW1:801010", "农林牧渔", "industry", "", "akshare_sw"),
                        ("SW2:801017", "养殖业", "industry", "SW1:801010", "akshare_sw"),
                        ("SW2:801016", "种植业", "industry", "SW1:801010", "akshare_sw"),
                        ("SW2:801017X", "养殖业", "industry", "", "other_provider"),
                    ],
                )

            evidence = discover_industry_keyword_evidence_from_storage(
                "生猪养殖价格上涨，猪周期受到关注",
                storage,
                source="test_news",
                source_url="https://example.org/news/1",
            )
            self.assertEqual([item.target_code for item in evidence], ["SW2:801017"])
            self.assertEqual(evidence[0].source, "test_news")
            self.assertEqual(evidence[0].source_url, "https://example.org/news/1")
            self.assertEqual(evidence[0].direction, "neutral")
            self.assertFalse(evidence[0].verified)

    def test_does_not_emit_level_one_sector(self):
        evidence = discover_industry_keyword_evidence("种植业政策支持", self.sectors)
        self.assertEqual([item.target_code for item in evidence], ["SW2:801016"])

    def test_keyword_match_does_not_assume_impact_direction(self):
        evidence = discover_industry_keyword_evidence("猪价上涨", self.sectors)
        self.assertEqual(evidence[0].direction, "neutral")
        self.assertFalse(evidence[0].verified)

    def test_empty_or_unmatched_text_returns_no_evidence(self):
        self.assertEqual(discover_industry_keyword_evidence("", self.sectors), [])
        self.assertEqual(
            discover_industry_keyword_evidence("与行业无关的普通新闻", self.sectors),
            [],
        )

    def test_duplicate_level_two_name_is_rejected(self):
        sectors = self.sectors + [
            {"sector_code": "SW2:999999", "sector_name": "养殖业"}
        ]
        with self.assertRaisesRegex(ValueError, "名称不唯一"):
            discover_industry_keyword_evidence("生猪养殖", sectors)


if __name__ == "__main__":
    unittest.main()
