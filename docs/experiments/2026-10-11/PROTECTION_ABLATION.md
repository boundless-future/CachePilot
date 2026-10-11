# 3C 保护预算性能消融

日期：2026-10-11。此报告完成 CachePilot 保护预算 `zero`、`one`、`two` chunk 的第一轮正式性能消融。它检验的是一个固定合成多轮 Agent trace 上的端到端服务延迟，不将功能成功、KV 一致性或 D2H 下降单独解释为性能收益。

后续复核：同日完成 prompt 不重合、结构相同的 [独立输入验证](PROTECTION_ABLATION_VALIDATION.md)，one/two 的整体 TTFT P95 相对 zero 为 -0.57%/+0.31%，本报告的改善没有稳定复现。以下数据保留为开发集观察，不作为一般加速结论。

## 环境与负载

- 单卡 RTX 4090 24GB，Qwen3-4B BF16，vLLM 0.30.0，固定 LMCache 研究提交 `1a4b40b1d79b0e76244f127f96ee0982f8bd270f`。
- eager、TP=1、同步调度、2 GiB GPU KV、16 GiB CPU KV；服务每个 cell 独立启动，缓存不跨 cell 复用。
- 每个预算三次，轮转顺序为 `zero, one, two`、`one, two, zero`、`two, zero, one`。客户端以 SSE 开放环记录计划到达、实际发送、首 token 与结束。
- 固定 trace 共 68 个请求：先让 12 个可复用 session 各运行 4 轮以建立 STORE，再注入 8 个具有独立文本前缀的 pressure session，最后回访前 12 个 session 的第 5 轮。这一分段避免早期校准中的两种无效负载：只有 D2H 没有 H2D，或非零预算全部因容量回退而没有保护提交。

## 有效性门槛

九个 cell 全部通过。每个 cell 都完成 68/68 个 SSE 响应和固定 token 合约，存在 `1,811,939,328` B H2D，运行期和 vLLM 退出后的 owned-service/LMCache 资源均闭合，GPU registration 清空，日志无 `ERROR` 或 traceback。非零预算 cell 均有保护 commit 和 receipt。因而下表没有排除样本。

原始结果摘要及逐请求输出哈希保存在 [analysis JSON](ablation-evidence/protection-ablation-staged-20261011a-analysis.json)，完整日志、响应和状态快照在 [artifact archive](ablation-evidence/protection-ablation-staged-20261011a.tar.gz)。旧的无效校准轮保留在同目录，但不参与本报告。

## 结果

数值为三次重复的均值 +/- 样本标准差。TTFT 和队列单位为毫秒，D2H 为 GiB；所有组的实际 prefill token（24,156）、外部 hit token（12,288）、GPU hit token（38,208）和 H2D 都相同。

| 预算 | TTFT P95 | 回访 TTFT P95 | 会话完成 P95 | 服务端平均排队 | D2H | 保护 peak pin | 策略 wall 时间 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| zero | 7442.47 +/- 148.82 | 2340.67 +/- 40.03 | 31447.37 +/- 45.10 | 3180.18 +/- 65.53 | 2.671 +/- 0.000 | 0 | 0 |
| one | 6979.65 +/- 195.72 | 2239.29 +/- 47.77 | 31321.57 +/- 47.34 | 2996.47 +/- 86.38 | 2.590 +/- 0.101 | 16 | 90.82 +/- 8.51 |
| two | 7185.36 +/- 129.22 | 2305.99 +/- 72.65 | 31397.99 +/- 116.95 | 3100.37 +/- 52.73 | 2.297 +/- 0.107 | 32 | 85.82 +/- 3.02 |

相对于 zero，one chunk 的整体 TTFT P95 低 6.22%，回访 TTFT P95 低 4.33%，服务端平均排队低 5.78%，D2H 低 3.07%。two chunk 的相应变化为 -3.45%、-1.48%、-2.51% 和 -14.03%。one chunk 还把全 trace 的 reuse TTFT P95 从 7805.57 ms 降到 7336.90 ms（-6.00%）；two chunk 为 7520.63 ms（-3.65%）。会话完成 P95 的变化很小，分别为 -0.40% 和 -0.16%。

因此，首个可复现实验点支持一个范围很窄的结论：在该固定 2 GiB GPU-KV 压力 trace 上，单 chunk 保护有可测的尾延迟改善；扩大到两个 chunk 没有带来更好的 TTFT。D2H 的下降与收益同时出现，但不是因果证明。三次重复不足以给出统计显著性或推广到其他模型、KV 容量、到达模式和硬件。

## 输出与限制

每个 cell 的输入、到达计划与 token 合约相同，但不能假定 decode 完全确定。相对同 repeat 的 zero，one chunk 的输出哈希失配分别为 2、6、0 / 68，two chunk 为 4、4、4 / 68；所有请求仍完整返回固定 token 数。这延续了项目中已记录的跨配置 decode 差异，因此本轮只将其报告为观察，不以输出文本等价作为性能通过条件。

本轮没有覆盖 CUDA driver/DMA/PCIe 实际故障、30 秒延迟 receipt 的空闲 owner 收尾、异步调度、多 GPU、真实 Agent 闭环或自然工具调用。诊断日志和 KV 逐层校验没有纳入计时路径。下一阶段应先固定独立的开发/验证 session 集合，再在不同 GPU KV 容量、长 prefill/稳定 decode/集中恢复以及 Qwen3-8B + RTX 5090 上复核；所有失败 cell 继续从聚合中排除。
