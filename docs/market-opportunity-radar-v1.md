# 本文件记录短线机会雷达V1逐文件开发范围、数据约束及验收状态，便于后续分阶段继续操作。
# TrendRadar 短线机会雷达 V1 — 文件清单与实施说明

## 本分支目标

建立可独立演进的市场机会雷达模块，覆盖美股/美国宏观事件采集、事件到A股板块/证券的证据映射、动态候选池、免费优先的批量行情适配接口、透明规则评分和独立市场数据库。

## 已落入本分支的文件

### 核心模块
- `trendradar/market_data/__init__.py`：市场雷达模块公共导出。
- `trendradar/market_data/models.py`：事件、证券、行情快照、映射证据、候选模型。
- `trendradar/market_data/security_master.py`：证券代码规范化、主数据校验、风险/非活跃股票过滤。
- `trendradar/market_data/sector_mapper.py`：行业板块候选扩展及过滤。
- `trendradar/market_data/us_event_fetcher.py`：美股及官方RSS事件源抓取。
- `trendradar/market_data/macro_event_fetcher.py`：美国宏观与政策RSS采集。
- `trendradar/market_data/cn_event_mapper.py`：事件批量映射证据接口，供后续AI批量提取器接入。
- `trendradar/market_data/cross_market_mapper.py`：美股/宏观事件到行业的启发式发现及可信关系解析。
- `trendradar/market_data/quote_fetcher.py`：批量行情提供商接口、异常降级和行情时间字段约束。
- `trendradar/market_data/opportunity_ranker.py`：A/B/C候选分层、规则评分和排序。
- `trendradar/market_data/market_radar.py`：事件、证券主数据、可信映射、行情、评分的流程组装。
- `trendradar/market_data/storage.py`：独立SQLite数据库持久化。
- `trendradar/storage/market_schema.sql`：证券、板块成员、跨市场关系、事件、候选证据、行情快照、运行记录表。

### 测试
- `tests/market_data/test_market_radar.py`：代码规范化、过滤、事件映射和评分排序单元测试。
- `tests/market_data/test_market_storage.py`：SQLite事件幂等写入、证券主数据及行情快照测试。

## 设计边界/后续集成任务

- 当前行情模块仅定义 provider contract；必须在后续选定并验证具体免费数据源，确认频率、延迟、接口稳定性和服务条款后，才可以执行生产扫描。
- 可信关系库的数据录入/人工审核界面以及证券/板块主数据源更新任务尚需实现。
- AI批量事件抽取目前留有接口，尚未接入现有AI客户端；此模块避免逐只股票调用AI。
- 本次尚未把雷达接入 TrendRadar 原新闻分析与通知主流程，也未变更现有时间线、调度或推送行为，避免未经验证改变当前线上配置。
- 市场DB当前以独立本地SQLite文件 `output/market/market.db` 为目标。S3对象生命周期、并发锁、下载/上传原子替换与版本冲突保护需在接入现有RemoteStorageBackend前单独实现并测试。新闻存储的每日数据库模式不应直接复用为市场DB远程同步机制。
- 14:33定时snapshot不等于连续盘中监控；短周期行情扫描需要另行设计调度与资源预算。

## 初步验收标准

1. 单元测试覆盖证券过滤、事件关联和排名基础逻辑。
2. SQLite schema能够在空库初始化，唯一约束支持事件/行情重复采集幂等。
3. 每个候选可携带映射依据、来源、置信度、行情源和数据时间。
4. 弱关联扩展标记为未证实，不将行业概念直接描述为利好或交易建议。
5. 未配置或行情源失败时，新闻原有流程不应受到影响（需在主流程接入阶段验证）。
