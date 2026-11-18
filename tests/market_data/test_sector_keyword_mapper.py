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
        self.assertEqual(by_code["SW2:801017"].evidence_type, "direct")
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
        self.assertEqual(evidence[0].evidence_type, "official_name")
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

    def test_second_commodity_batch_matches_eleven_industries_by_code(self):
        sectors = [
            {"sector_code": "SW2:801055", "sector_name": "工业金属"},
            {"sector_code": "SW2:801056", "sector_name": "能源金属"},
            {"sector_code": "SW2:801053", "sector_name": "贵金属"},
            {"sector_code": "SW2:801054", "sector_name": "小金属"},
            {"sector_code": "SW2:801043", "sector_name": "冶钢原料"},
            {"sector_code": "SW2:801044", "sector_name": "普钢"},
            {"sector_code": "SW2:801045", "sector_name": "特钢Ⅱ"},
            {"sector_code": "SW2:801951", "sector_name": "煤炭开采"},
            {"sector_code": "SW2:801961", "sector_name": "油气开采Ⅱ"},
            {"sector_code": "SW2:801962", "sector_name": "油服工程"},
            {"sector_code": "SW2:801963", "sector_name": "炼化及贸易"},
        ]
        text = (
            "电解铝和铜价上涨，碳酸锂及硫酸镍价格走强；"
            "国际金价创新高，氧化镨钕报价上涨；"
            "铁矿石价格回升，螺纹钢价格上涨，轴承钢需求改善；"
            "动力煤价格上涨，布伦特原油走强，海上钻井平台增加，炼化一体化项目投产。"
        )
        evidence = discover_industry_keyword_evidence(text, sectors)
        by_code = {item.target_code: item for item in evidence}
        self.assertEqual(set(by_code), {item["sector_code"] for item in sectors})
        for item in evidence:
            self.assertEqual(item.direction, "neutral")
            self.assertFalse(item.verified)

    def test_commodity_terms_keep_resource_and_service_chains_distinct(self):
        sectors = [
            {"sector_code": "SW2:801056", "sector_name": "能源金属"},
            {"sector_code": "SW2:801737", "sector_name": "电池"},
            {"sector_code": "SW2:801961", "sector_name": "油气开采Ⅱ"},
            {"sector_code": "SW2:801962", "sector_name": "油服工程"},
            {"sector_code": "SW2:801963", "sector_name": "炼化及贸易"},
        ]
        lithium = discover_industry_keyword_evidence("碳酸锂现货价格上涨", sectors)
        self.assertEqual({item.target_code for item in lithium}, {"SW2:801056"})
        oil = discover_industry_keyword_evidence(
            "海上钻井平台增加，炼化一体化项目投产", sectors,
        )
        self.assertEqual(
            {item.target_code for item in oil},
            {"SW2:801962", "SW2:801963"},
        )

    def test_third_batch_matches_fourteen_industries_by_code(self):
        sectors = [
            {"sector_code": "SW2:801093", "sector_name": "汽车零部件"},
            {"sector_code": "SW2:801095", "sector_name": "乘用车"},
            {"sector_code": "SW2:801096", "sector_name": "商用车"},
            {"sector_code": "SW2:801101", "sector_name": "计算机设备"},
            {"sector_code": "SW2:801104", "sector_name": "软件开发"},
            {"sector_code": "SW2:801072", "sector_name": "通用设备"},
            {"sector_code": "SW2:801074", "sector_name": "专用设备"},
            {"sector_code": "SW2:801077", "sector_name": "工程机械"},
            {"sector_code": "SW2:801078", "sector_name": "自动化设备"},
            {"sector_code": "SW2:801741", "sector_name": "航天装备Ⅱ"},
            {"sector_code": "SW2:801742", "sector_name": "航空装备Ⅱ"},
            {"sector_code": "SW2:801743", "sector_name": "地面兵装Ⅱ"},
            {"sector_code": "SW2:801744", "sector_name": "航海装备Ⅱ"},
            {"sector_code": "SW2:801745", "sector_name": "军工电子Ⅱ"},
        ]
        text = (
            "汽车线束和空气悬架订单增长，纯电乘用车交付提升，重卡销量回暖；"
            "GPU服务器与工业软件需求增加；数控机床、锂电制造设备和挖掘机出口增长；"
            "工业机器人订单增加；运载火箭、航空发动机、装甲车辆、潜艇建造、"
            "相控阵雷达项目取得进展。"
        )
        evidence = discover_industry_keyword_evidence(text, sectors)
        by_code = {item.target_code: item for item in evidence}
        self.assertEqual(set(by_code), {item["sector_code"] for item in sectors})
        for item in evidence:
            self.assertEqual(item.direction, "neutral")
            self.assertFalse(item.verified)
            self.assertGreaterEqual(item.confidence, 0.68)

    def test_third_batch_avoids_broad_terms_and_keeps_adjacent_chains_distinct(self):
        sectors = [
            {"sector_code": "SW2:801093", "sector_name": "汽车零部件"},
            {"sector_code": "SW2:801095", "sector_name": "乘用车"},
            {"sector_code": "SW2:801096", "sector_name": "商用车"},
            {"sector_code": "SW2:801101", "sector_name": "计算机设备"},
            {"sector_code": "SW2:801102", "sector_name": "通信设备"},
            {"sector_code": "SW2:801072", "sector_name": "通用设备"},
            {"sector_code": "SW2:801074", "sector_name": "专用设备"},
            {"sector_code": "SW2:801077", "sector_name": "工程机械"},
            {"sector_code": "SW2:801078", "sector_name": "自动化设备"},
            {"sector_code": "SW2:801741", "sector_name": "航天装备Ⅱ"},
            {"sector_code": "SW2:801742", "sector_name": "航空装备Ⅱ"},
            {"sector_code": "SW2:801743", "sector_name": "地面兵装Ⅱ"},
            {"sector_code": "SW2:801744", "sector_name": "航海装备Ⅱ"},
            {"sector_code": "SW2:801745", "sector_name": "军工电子Ⅱ"},
        ]
        self.assertEqual(
            discover_industry_keyword_evidence(
                "AI热度升温，汽车市场关注度提高，设备行业景气改善，军工订单增长",
                sectors,
            ),
            [],
        )
        evidence = discover_industry_keyword_evidence(
            "GPU服务器扩产，交换机需求增长；工业机器人交付，数控机床订单增加；"
            "运载火箭发射，航空发动机交付，舰用发动机项目推进，军用雷达升级",
            sectors,
        )
        self.assertEqual(
            {item.target_code for item in evidence},
            {
                "SW2:801101", "SW2:801102", "SW2:801072",
                "SW2:801078", "SW2:801741", "SW2:801742",
                "SW2:801744", "SW2:801745",
            },
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
