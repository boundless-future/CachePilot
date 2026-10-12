# decode192：同签名正式十二格完成

2026-10-12 16:28独立复核通过：四配置各三次，共816请求、156,672输出tokens，全部请求/token/服务运行期/资源门槛通过。原decode立即卸载残留失败继续保留；此轮使用新ImmediateReceiptConnector，不能与旧immediate混合。非零保护没有参加。

原 [预注册](DECODE_RECEIPT_PREREGISTRATION.md) 和 [pilot](DECODE_RECEIPT_PILOT.md) 的205项冻结源、68请求、75,808 prompt tokens、每请求192输出、Qwen3-4B BF16、4090 24GB、2GiB KV、同步eager TP1均不变。用户续授17:00窗口后运行三次轮换，不调参、不重跑择优。热路径逐事件日志关闭。

## 正式结果

延迟单位秒，为三个单轮P95的均值±样本标准差，不是合并请求后的P95。

| 配置 | TTFT P95 | 相对native | ready-to-end P95 | session completion P95 | 实际prefill均值 |
|---|---:|---:|---:|---:|---:|
| native | 50.783 ± 0.793 | — | 55.767 ± 0.863 | 84.708 ± 0.897 | 50,432 |
| immediate receipt | 52.934 ± 0.920 | +4.24% | 57.961 ± 0.982 | 86.354 ± 1.476 | 28,651 |
| ledger default h2.5/d64 | 53.751 ± 1.062 | +5.85% | 58.878 ± 1.095 | 87.661 ± 0.706 | 29,781 |
| ledger selected h5/d1 | 51.948 ± 0.701 | +2.29% | 56.951 ± 0.690 | 85.933 ± 0.616 | 32,352 |

| 配置 | TTFT P95 r0 / r1 / r2 |
|---|---|
| native | 50.180 / 50.487 / 51.681 |
| immediate receipt | 53.596 / 51.883 / 53.323 |
| ledger default | 52.783 / 54.886 / 53.584 |
| ledger selected | 52.740 / 51.406 / 51.697 |

native在每轮整体TTFT P95均最低，selected最后一轮差距只有约16ms，不能渲染为稳定显著差距。缓存组三种配置减少实际prefill约35.85–43.19%，但没有转化为整体尾延迟改善。它补充了长prefill正式结果的负载边界；长prefill与decode192的prompt长度和immediate版本不同，不是单变量跨负载因果消融。这里比较整套零保护系统，不把差异归因于保护策略或单个END修复。

## 阶段成本和范围

| 配置 | 请求queue均值ms | prefill均值ms | decode均值ms | H2D / D2H staging GiB均值 | 抢占r0/r1/r2 |
|---|---:|---:|---:|---:|---|
| native | 30,918 | 55.78 | 4,932.88 | N/A | 0 / 0 / 0 |
| immediate receipt | 31,981 | 44.30 | 5,030.49 | 2.414 / 4.430 | 0 / 0 / 0 |
| ledger default | 32,493 | 49.01 | 5,105.16 | 2.344 / 3.410 | 0 / 0 / 0 |
| ledger selected | 31,838 | 50.47 | 5,002.14 | 2.344 / 3.188 | 0 / 0 / 0 |

请求decode时间远大于prefill，排队占端到端延迟很大部分；数据支持“此负载减少prefill仍不足以改善整体延迟”的观察。上述counter不是独占kernel耗时，搬运staging可能重叠，不能相加或据此计算DMA的因果贡献。没有新增占用采样，不能仅因输出192就声称存在稳定decode并发平台。

固定48 early / 8 pressure / 12 revisit的事后拆分及SSE fragment首末时间已归档 `decode-formal-phases.json`，不作为参数选择标准。各配置revisit TTFT P95均值分别51.897/53.349/54.547/52.989秒；与整体方向一致。SSE fragment不是单个token。

三套缓存的运行期及退出后exact session/job/transfer为空；lazy各generation和操作终结闭合，无非零保护commit。`h2p5-d64-r2` 有一段已按原严格规则识别的正常SIGTERM后output-handler EngineDeadError，原文保留并单列；不是服务运行期错误，也不宣称没有任何traceback。没有改动分类规则。

相对同轮native的输出hash不同请求数（每轮68）：immediate 9/11/9、default 6/6/6、selected 9/6/6。greedy/seed不能保证跨缓存和调度路径逐字一致，未证明语义等价；性能试验没有新KV bitwise探针，正确性范围仍引用前置独立诊断，不扩大到取消、抢占或driver fault。

## 证据

- 原始目录 `generation-matrix-evidence/generation-decode192-receipt-formal-20261012a/`，154文件、14,798,960 bytes，与服务器SHA/长度逐项一致。
- 服务器 `independent-summary.json`、本地 `decode-formal-local-independent.json`、冻结源 `decode-formal-frozen-independent.json` 完全相同，12/12通过。
- `decode-formal-source-after.json` 复核原205源；源包继续为 `generation-decode-receipt-source-20261012.tar.gz`，SHA `45fc064635651cbf1be435e66cea14c97beed6f9a8fa33e856e11b3967848e94`。
- `decode-formal-costs.json`、`decode-formal-phases.json` 和 `decode-formal.png`（已视觉检查）保留完整单轮数值、波动和分解。

本轮闭合decode formal待办。下一步按独立预注册运行全部六session匿名结构的GPU准入和物理压力观测；单次试点不提供正式收益结论，不按结果增加压力或选参数。
