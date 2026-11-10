# 本文件记录短线机会雷达市场数据库V2的字段设计、索引/约束、更新规则与远程SQLite同步流程。
# TrendRadar 短线机会雷达 — 市场数据库详细设计 V2

## 1. 文件与版本

- SQLite 文件：output/market/market.db
- Schema文件：trendradar/storage/market_schema.sql
- 存储封装：trendradar/market_data/storage.py
- Schema版本：2（写入 schema_meta）
- 数据库与新闻存储分离，避免新闻历史清理、归档或写入锁影响市场雷达。

## 2. 数据表设计

| 表 | 主键/唯一约束 | 用途 |
|---|---|---|
| schema_meta | key PK | 数据库版本与迁移元数据 |
| data_sources | source_id PK | 数据源登记、优先级、健康状态、许可/条款链接 |
| security_master | code PK | A股证券主数据、交易所、板块、上市状态、风险标记、首见/末见时间 |
| sector_master | sector_code PK | 行业/概念/主题/指数等板块字典 |
| sector_membership | UNIQUE(security_code, sector_code, valid_from) | 证券与板块的生效期关系 |
| cross_market_map | UNIQUE(us_entity, us_ticker, cn_security_code, mapping_type, source_url) | 美股实体、宏观传导关系与A股证券映射，审核状态和证据 |
| market_events | event_id PK；source/source_event_id非空时唯一 | 新闻、公司事件、美国宏观事件标准化记录 |
| event_candidates | UNIQUE(event_id, target_code, mapping_type) | 事件到证券/板块的关联依据、可信度和方向 |
| market_snapshots | UNIQUE(code, timestamp, source) | 多行情源同时间点行情及延迟标记 |
| radar_runs | run_id PK | 每次扫描的状态、耗时、候选量、行情源与推送数 |
| radar_candidates | PK(run_id, code) | 扫描得分、层级、因果状态、理由、证据与是否推送 |
| ingestion_state | PK(source_id, feed_key) | RSS/行情/证券主数据增量游标 |
| remote_sync_state | object_key PK | 远程版本、ETag、SHA256与同步状态 |

### 关键设计

- 股票代码作为六位字符串保存，避免前导零丢失。
- 交易所单独保存（SH/SZ/BJ），不在数据库中混用带后缀/不带后缀代码。
- listing_status区分active、suspended、delisted、pending、unknown；risk_flag单独表示风险警示状态。
- 板块成员带有效期，避免把当前行业关系错误套用到历史事件。
- 跨市场映射 status=pending 默认不进入可信映射结果。仅verified关系可参与强关联；拒绝、过期关系留档不删除。
- 方向采用positive/negative/mixed/neutral/unknown；行业/概念命中并不自动代表利好。
- 行情的业务时间 timestamp 与采集时间 collected_at 分离，delayed明确标记非实时/延迟行情。
- 事件RSS未必具有provider event ID，不能对空source_event_id做全局唯一；对非空source_event_id建立部分唯一索引。
- 同证券、同时间、不同source的行情记录可共存；唯一约束不依赖单一行情源。

## 3. 索引策略

- 证券：上市状态/风险标记/交易所、名称、板块/状态组合索引。
- 板块关系：板块+有效期和证券+有效期两个方向索引。
- 跨市场映射：实体、ticker、证券分别与审核状态/可信度组合。
- 事件：发布时间、来源+发布时间、事件类型+发布时间、内容hash。
- 候选证据：目标代码+可信度、事件+审核状态、来源+URL。
- 行情：时间倒序、证券+时间倒序、交易日+证券。
- 雷达结果：扫描时间/状态、证券+run、run+score。
- 采集与远程同步：最近成功时间、source/feed和object key主键。

## 4. 更新策略

### 证券主数据
- 每日盘前或雷达运行前执行全量/增量同步。
- 新证券插入；名称、交易所、板块、风险状态变更采用UPSERT并更新source_updated_at/updated_at。
- 本轮源数据缺失不立即标记退市。只有权威数据源明确给出退市/停牌状态，或多轮完整同步确认后，才调整listing_status。
- 维护first_seen_at/last_seen_at。源数据异常、数量骤减时拒绝覆盖数据库，触发partial/failed状态。
- 风险警示过滤、停牌过滤在候选资格层进行，不从主数据表删除记录。

### 板块与关系库
- 板块成员采用有效期；关系发生变化时关闭旧valid_to，再插入新valid_from。
- AI发现关系只进入pending；证据包括来源、URL、发现时间、映射类型、方向、置信度。
- 审核通过设置verified/reviewer/verified_at；拒绝设置rejected，失效关系设置stale。
- 不把同一企业的所有概念股批量视作直接受益股。

### 事件与行情
- 事件用来源+provider ID去重；无provider ID则以稳定event_id/content hash策略生成。
- 同一事件更新标题、摘要或发布时间时更新记录，不重复插入候选。
- 行情每次扫描append snapshot；重复同证券、时间、源采用UPSERT。保留交易日、源、延迟标记。
- 保留期限建议：事件和扫描结果先保留至少180天；行情快照首版保留90天，后续可按数据量调整。删除前先统计容量，不做自动大规模清理。

## 5. SQLite安全与并发

- SQLite连接启用foreign_keys、busy_timeout、WAL、synchronous=FULL。
- 所有批量UPSERT在单事务中执行；任何批次失败整批回滚。
- GitHub Actions同一市场DB同步工作流需配置concurrency group，避免两个runner同时更新对象。
- 远程对象存储不提供SQLite事务。禁止在S3上直接打开数据库文件进行查询/写入。
- 远程同步只在本地runner下载完整文件后使用SQLite；写入结束关闭连接，执行完整性校验与checkpoint后再上传。

## 6. 远程SQLite对象同步流程（待接入当前RemoteStorageBackend）

1. 获取市场DB专用远程对象的锁/版本信息，记录ETag或VersionId。
2. 下载到本地临时文件，不直接覆盖工作数据库。
3. 验证SQLite header、schema版本和 PRAGMA quick_check；空库则按schema初始化。
4. 将已验证临时数据库原子替换为本地工作文件。
5. 完成证券、事件、映射、行情批量事务写入。
6. 关闭所有连接，执行 PRAGMA wal_checkpoint(TRUNCATE) 和 PRAGMA quick_check，确保无待上传WAL。
7. 生成本地SHA256，上传到同目录临时对象key。
8. 重新确认远端ETag/VersionId未变化；若变化，停止上传并触发冲突重试/重新合并，不覆盖他人更新。
9. 上传校验成功后通过对象存储支持的copy/replace机制替换正式key；保存VersionId、ETag、SHA256及同步时间。
10. 失败时保留远端旧对象与本地工作副本，写入last_error；不影响新闻流程。

### 对象存储注意事项
- 推荐独立key：market/market.db，不要复用新闻数据库key。
- S3的ETag不总是文件MD5（尤其multipart），完整性使用SHA256；ETag只用于条件版本比较。
- 若当前存储后端不能保证条件写/对象锁，则市场DB写入必须串行；仅靠先下载再上传不足以防止lost update。
- 初期不做多runner同时合并SQLite；采用单writer工作流最稳妥。

## 7. 迁移与兼容性

- 每次schema调整增加版本号，迁移必须幂等、可在已有market.db执行。
- 不删除已有事件、证券或扫描数据；重建行情表时先复制数据并在事务/备份保护下完成。
- 本版存储封装包含旧schema补列与market_snapshots复合唯一键迁移逻辑；上线前仍需在复制的旧数据库上做migration test。
- 若迁移失败，恢复备份并停止市场雷达写入；新闻采集与推送不依赖该数据库。

## 8. 本阶段验收

- 空库初始化和schema版本检查。
- 证券主数据UPSERT，风险/停牌信息不删除记录。
- 同一事件重复写入幂等。
- 同一证券同时间多个行情源可共存，重复相同源可UPSERT。
- 外键、唯一约束、索引和SQLite quick_check验证。
- 旧版market.db迁移前备份，迁移后行数/关键字段核对。
- 远程存储串行下载-更新-校验-上传模拟；失败保留旧远端对象。
