# 六会话匿名结构：正式十二格完成

2026-10-12 18:46完成，四配置各三次，共720请求、34,560输出tokens；请求/token/来源/服务/资源及generation门槛12/12通过。服务器、本地和210项冻结源码的独立报告完全一致。所有配置零保护，本轮没有额外metrics轮询。

## 方法与范围

沿用 [预注册](REAL_STRUCTURE_PERFORMANCE_PREREGISTRATION.md) 和完整 [两套pilot](REAL_STRUCTURE_GPU.md)。Qwen3-4B BF16、4090 24GB、同步eager TP1、2GiB GPU KV、16GiB CPU L1。每格60请求、209,661 prompt tokens、每请求48输出，四配置三轮按固定顺序轮换；未调参、改trace或混入pilot。运行前后210项源码字节一致，无额外文件。

六条原session全部保留，源编号21/22/26/57/58/86。输入/8、会话内部时间/10、首请求对齐0、固定输出、open arrival仍为显式变换；源hash重建不是原文本或实际tool执行，不含生成反馈，也不代表原生产跨会话并发分布。

## 正式结果

秒，三个单轮P95的均值±样本标准差；不是合并请求后的P95。

| 配置 | TTFT P95 | 相对native | ready-to-end P95 | session completion P95 | 实际prefill均值 |
|---|---:|---:|---:|---:|---:|
| native | 1.893 ± 0.026 | — | 3.092 ± 0.033 | 78.573 ± 0.015 | 42,893 |
| immediate receipt | 2.002 ± 0.071 | +5.76% | 3.248 ± 0.071 | 78.603 ± 0.004 | 27,608 |
| ledger default h2.5/d64 | 2.107 ± 0.048 | +11.33% | 3.463 ± 0.169 | 78.663 ± 0.052 | 35,581 |
| ledger selected h5/d1 | 2.076 ± 0.050 | +9.69% | 3.296 ± 0.037 | 78.666 ± 0.031 | 33,677 |

| 配置 | TTFT P95 r0 / r1 / r2 |
|---|---|
| native | 1.908 / 1.907 / 1.863 |
| immediate receipt | 2.082 / 1.976 / 1.948 |
| default | 2.161 / 2.089 / 2.071 |
| selected | 2.132 / 2.061 / 2.036 |

native在每轮整体TTFT和ready-to-end P95均最低。立即卸载prefill降幅约35.64%，仍未改善整体尾延迟；不是所有CPU缓存场景都有净收益。会话完成时间含原计划到达跨度，因此接近78.6秒不能解释为真实Agent端到端任务耗时。

## 计算、搬运和抢占

| 配置 | queue均值ms | prefill均值ms | decode均值ms | GPU hit均值 | external hit均值 | 抢占r0/r1/r2 |
|---|---:|---:|---:|---:|---:|---|
| native | 374.83 | 73.71 | 1,228.30 | 166,768 | 0 | 0 / 0 / 0 |
| immediate receipt | 411.68 | 54.41 | 1,223.75 | 166,352 | 15,701 | 0 / 0 / 0 |
| default | 445.31 | 65.45 | 1,254.61 | 160,373 | 13,707 | 0 / 1 / 2 |
| selected | 458.23 | 62.02 | 1,267.49 | 161,680 | 14,304 | 1 / 1 / 1 |

三套缓存配置均有实际H2D，资源终结闭合。default每轮idle drop=25，selected=19，保护commit/pin均0。本轮没有占用采样，不能把前置pilot的94.94–99.67%峰值当本轮测得值。抢占是本轮原counter观测，未通过人为注入制造。

逐方向staging字节与耗时、各请求阶段、标准差和原单轮值见 `structure-formal-sessions-costs.json` 及独立报告。请求阶段并非独占kernel时间，staging与请求可能重叠，不相加归因DMA成本。当前结果不足以区分Python接入、调度顺序、预留容量和传输各自的因果贡献。

## 全六会话事后描述

下表为每会话单轮TTFT P95的三轮均值（ms）；不是新的调参准则，六条全部列出。请求少的会话P95接近最大值，不作显著性结论。

| 源编号（每轮请求数） | native | immediate | default | selected |
|---|---:|---:|---:|---:|
| 21（14） | 1817.42 | 1699.88 | 1956.57 | 1926.18 |
| 22（7） | 1758.05 | 1781.64 | 1995.09 | 1931.45 |
| 26（3） | 570.13 | 623.38 | 591.15 | 569.25 |
| 57（8） | 1717.57 | 1741.93 | 1833.29 | 1842.29 |
| 58（4） | 2025.47 | 2138.61 | 2135.97 | 2155.21 |
| 86（24） | 1133.29 | 1007.31 | 1251.37 | 1266.96 |

immediate在21/86的会话尾延迟均值更低，在其余四条更高；不能挑出这两条宣称整体加速。selected在26的差距不足1ms，不能据此选择该参数。完整会话ready-to-end、入口等待、完成跨度与单轮值由 `analyze_structure_sessions.py` 复算。

## 正确性与证据

运行期及退出后exact session/job/transfer为空，lazy generation操作闭合；原日志没有未分类运行期错误。`owned-immediate-r1`有一段严格匹配既有SIGTERM后output-handler退出竞态的8行traceback，单列保留，未修改门槛。CUDA IPC等warning保留原文，不宣称无任何警告。

对同轮native的输出hash差异（每轮60）：immediate 8/8/8、default 10/7/3、selected 6/8/6。固定greedy不保证跨配置输出逐字一致；没有真实任务质量评分，不声称语义等价。此性能矩阵不新增KV bitwise探针；前置诊断的正确性边界不能扩展为多worker/异步或driver fault支持。

- 原始目录 `real-structure-evidence/real-structure-formal-20261012a/`：186文件、47,173,551 bytes，与服务器inventory逐字节一致。
- `structure-formal-server-audit.json`、`structure-formal-local-audit.json`、`structure-formal-frozen-audit.json`完全相同，12/12。
- 210源包继续使用 `real-structure-unobserved-source-20261012.tar.gz`，SHA `56c6cce2cdf12992c692dd3b508d09930be1021a5ae3b2932fbec09d97f064c3`。
- `structure-formal.png`已视觉检查；`structure-formal-sessions-costs.json`保留完整描述。
- `structure-formal-closeout.json`：GPU 0%/1MiB，无计算进程，8000/8080/5555无监听，容器未关闭。

## 下一步判断

当前已经补齐长prefill、decode192、匿名结构三类零保护新栈对照：收益依赖负载，选中固定参数不稳定支配，非零保护尚无泛化收益证明。暂不通过更大卡或改trace追求正结果。若继续策略主线，先实现 [generation与保护的显式交接契约](../../GENERATION_PROTECTION_INTEGRATION.md)，验证完整prefix/suffix、原回执与END安全；通过真实manager和GPU bitwise准入后才做新栈zero/one/two。不能直接删除零预算guard继续跑性能。
