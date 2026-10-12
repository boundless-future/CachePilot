# 新组合新文本 pilot：无保护覆盖，按门槛停止

2026-10-12。已执行三格、204测量请求/9,792输出tokens；默认与调优零保护通过，selected-one 因保护提交为零失败，selected-two未运行，formal未启动。服务器、本地当前代码和冻结源审计一致：2/4通过、完整矩阵false、聚合为空。没有重跑或调整参数。

## 输入及设计

见 [预注册](GENERATION_STRATEGY_VALIDATION_PREREGISTRATION.md)。从历史 fixed-baseline-validation-20261011a-partial.tar.gz 提取同一份顶层trace，保持四配置、全部策略参数、资源和计时/关闭门槛。源码224项不变，仅增加runner/auditor两项，共226项；源包和逐字节核对归档。

68请求，开发/验证prompt哈希交集0；event/session/turn/arrival/think/max_tokens/temperature/seed逐项相同。输入tokens由74,652变为75,808，实际Qwen3-4B tokenizer与冻结长度完全一致。这是已使用过的历史合成新文本复核，不是新盲测或真实Agent泛化。旧栈失败记录继续保留。

每条输入多17 tokens，56条多1个16-token prompt block，12条多2个；每条完整256-token chunk数都不变。见 [静态边界分析](generation-strategy-validation-evidence/input-length-boundaries.json)。这是逐请求静态算术，没有同时驻留集合和真实free/reserve时间线；不能把这些block求和当作运行期额外占用，也不能认定17 tokens就是全部根因。

## 实际结果

以下仅逐格原始观测，不是完整矩阵统计；失败候选的耗时不能作为保护收益。

| 配置 | 请求 | TTFT P95 ms | 保护提交/回执 | 容量回退 | 实际prefill tokens | 状态 |
|---|---:|---:|---:|---:|---:|---|
| default-zero h2.5/d64 | 68 | 8234.04 | 0/0 | 0 | 24240 | 通过 |
| selected-zero h5/d1 | 68 | 8354.64 | 0/0 | 0 | 24240 | 通过 |
| selected-one h5/d1 | 68 | 8291.64 | 0/0 | 35 | 24240 | 无覆盖，失败 |
| selected-two h5/d1 | 未运行 | — | — | — | — | 按预注册停止 |

三个已执行格H2D均1,811,939,328 bytes、D2H均2,868,903,936 bytes。selected-one与selected-zero的68条output hash完全一致；默认组也一致。失败候选比较资格仍为false，没有因为输出或资源正常而提升为通过。

selected-one 的prepared/committed/receipts均0，峰值pin/inflight均0，35条capacity回退。普通worker批次提交返回和原回执各38，generation注册/END各69；请求/token/SSE时间戳、资源、原回执、配置、源签名、generation及运行期日志检查均通过，失败检查仅result_passed和coverage。三个已执行格都没有匹配到服务错误或已知停机traceback；服务器session/job/transfer为空，退出GPU注册清零。性能轮没有KV bitwise或物理BlockPool探针，因此不以这次结果声明全面KV正确性。

## 已知原因与未知部分

本次确认的是**容量准入分支拒绝了保护，正常推理继续完成**。`BoundedProtection.prepare()` 在free不足以覆盖保守reserve和一个完整chunk时拒绝；reserve为全部live请求完整footprint的两倍，加watermark及16 blocks。该约束是安全保留，不是已证明最优的性能需求估计。

capacity事件按wire身份去重，35不是35个调度步，也不是所有候选尝试次数。关闭逐步日志后，摘要只保留reason计数，没有实际free/reserve、候选范围、live请求组成及候选存活窗口。因此不能区分是窗口太短、保留估计过大、实际空闲不足或几者叠加；也不能用开发与验证输入的长度差直接完成因果归因。新栈与旧栈回退计数不同，不据此认为保护效果改善。

下一步是独立容量门槛诊断，见 [诊断设计](GENERATION_CAPACITY_DIAGNOSTIC_PLAN.md)。不继续未通过准入的formal、不放松安全reserve、不增显存或改负载绕过反例。开发三重复的−4.74%/−3.34%只能保留为开发负载结果，当前没有新文本收益证据。

## 证据及收尾

- [原始证据](generation-strategy-validation-evidence/generation-strategy-validation-pilot-20261012a/)、[本地审计](generation-strategy-validation-evidence/local-audit.json)、[冻结源审计](generation-strategy-validation-evidence/frozen-audit.json)。三处报告完全一致，审计退出码1是预期的未完成矩阵结果。
- [原始字节核对](generation-strategy-validation-evidence/raw-byte-audit.json)：50文件、3,511,420 bytes，逐一与服务器SHA256一致；清单不包含自身。所有原始日志显式纳入Git。
- [源包](generation-strategy-validation-evidence/generation-strategy-validation-pilot-20261012a-source.tar.gz)：928,221 bytes，SHA256 `85b935f3c2946ae25b98f719c2c4ac5323fe92b6e91b630e41ad35dc996923b3`，226项清单源逐字节一致。
- [输入来源](generation-strategy-validation-evidence/input-source.json)、[实际tokenizer核对](generation-strategy-validation-evidence/generation-strategy-validation-pilot-20261012a/tokenizer-audit.json)。
- 启动前55项回归通过，加入真实失败归档篡改passed标志仍不能提升覆盖的测试后，[56项通过](generation-strategy-validation-evidence/local-tests-final.txt)。这是入口/审计/测量回归，不是native/CUDA全套重跑。
- [收尾](generation-strategy-validation-evidence/closeout.json)：服务停止、226源未变、8000/8080/5555空闲、GPU1MiB/0%，容器保持开机。没有遗留实验。
