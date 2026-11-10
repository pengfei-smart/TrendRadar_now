<!-- 本文档说明全A股行情批量压力测试的执行方式、风险控制和验收口径。 -->
# 全A股行情批量压力测试

## 目的与边界

使用东方财富证券列表接口构建当前证券池，再通过 `ulist.np/get` 按批次请求行情。该脚本用于只读基准测试，不写入 `output/market/market.db`，不触发新闻采集、推送或调度器。

数据源为非官方公开接口，无SLA。测试结果不能证明交易所逐笔实时性；行情模型继续保留 `delayed_or_unverified=true`。

## 执行前提

- Python >= 3.10。
- 安装项目依赖（至少包含 requests、pytz）。
- 在仓库根目录执行，Windows CMD 需要设置 `set PYTHONPATH=.`。

## 第一轮：小规模验证

先测试200只，确认证券列表分页、批次切分、报告结构正常：

```bat
set PYTHONPATH=.
python scripts\market_data\full_market_quote_benchmark.py --limit 200 --batch-size 100
```

若成功，再进行全量基准：

```bat
python scripts\market_data\full_market_quote_benchmark.py --batch-size 100 --pause-seconds 0.3
```

不要并行启动多个全量测试。首轮不自动重试，避免重试掩盖上游原始失败率；失败批次会被记录并继续处理后续批次。

## 第二轮：批次大小对照

每次只运行一轮，建议依次比较：

- `--batch-size 100 --pause-seconds 0.3`
- `--batch-size 200 --pause-seconds 0.5`
- `--batch-size 300 --pause-seconds 0.8`

较大批次会增加URL长度和单次响应负担。只有在全量覆盖率、新鲜度和请求成功率均稳定时，才考虑提高批次大小；不建议直接使用500作为生产默认值。

## 报告字段

- `security_master_total`：证券列表原始条数。
- `eligible_security_count`：过滤风险警示及非 active 状态后的测试数量。
- `requested_count / returned_count / coverage`：请求、返回和覆盖率。
- `successful_batches / failed_batches`：批次级成功/失败数量。
- `batch_elapsed_seconds`：批次最小、平均、最大耗时。
- `freshness_counts`：按180秒阈值统计报价新鲜度。
- `missing_count / missing_codes_sample`：缺失报价数量及最多50个代码样本。
- `database_written`：必须为false。

注意：证券列表端点当前未提供经过验证的停牌状态，缺失报价可能来自停牌、无有效价格、接口遗漏或其他异常，需进一步核实。

## 第一版验收建议

先把以下作为目标，而不是预先假设接口必然达到：

1. 证券主数据分页完整，分页期间上游总数不变。
2. 所有请求批次均完成，无未解释的批次失败。
3. 行情覆盖率目标 >= 99%；缺失股票需要抽样人工核查。
4. 交易时段新鲜报价占比目标 >= 95%（180秒阈值）；保留 stale/invalid 明细。
5. 请求平均耗时和最大耗时可接受，且无持续限流/封禁迹象。
6. 连续至少3个交易日重复测试，比较成功率和耗时分布。

全量首轮结果出来前，不要将该脚本接入日常调度，也不要将全市场轮询作为默认行情采集策略。生产雷达仍优先只请求事件映射产生的候选股票池。
