# 保护预算的独立输入复核

日期：2026-10-11。开发集消融后固定保护实现，使用 prompt 不重合的合成验证输入完成 zero/one/two chunk 各三次重复。九个 cell 均通过当前实验门槛，但开发集的整体 TTFT P95 改善没有稳定复现：one 相对 zero 为 -0.57%，two 为 +0.31%。当前不能把开发集的 -6.22% 写成一般加速结论，也不能据此选定生产默认预算。

## 固定条件与输入隔离

环境沿用 [开发集报告](PROTECTION_ABLATION.md)：RTX 4090 24GB、Qwen3-4B BF16、vLLM 0.30.0、固定 LMCache 研究提交、eager、TP=1、同步调度、2 GiB GPU KV 和 16 GiB CPU KV。每格独立服务和缓存；轮转顺序为 zero/one/two、one/two/zero、two/zero/one。关闭 KV 探针和逐步诊断账本，保留聚合策略计时与终态资源审计。

验证标签为 `validation-20261011a`，正式 artifact 目录为 `protection-ablation-validation-20261011b`。验证 trace 仍是 68 请求：12 个可复用会话各四轮、8 个独立压力会话、12 个原会话回访。它只改变生成文本的 namespace，结构仍来自同一生成器，不是独立真实 Agent 数据集或新到达分布。

[输入审计](ablation-evidence/protection-validation-input-audit.json) 直接读取两个归档中的 `zero-r0/trace.json`，复算每条 prompt SHA，并核对实际字段：

- 两边各 68 条不同 prompt，跨集合 SHA 交集为 0。
- `event_id/session_id/turn/arrival_ms/think_ms/max_tokens/temperature/seed` 全部逐条一致；session ID 是结构编号，文本通过 workload label 隔离。
- 输入 token 总数分别为 74,652 和 75,604。因此仅保持结构与到达一致，没有做到开发/验证之间 token 长度完全匹配。
- cell manifest 的 trace SHA 分别为 `49f2c9cb0b2faba83806a53727ef2280fa2bf38351154041f18847c3b0b056af` 和 `079c9507c9585a5e5397affc8750c42603ee7efeeca3127d1cafafc2b1c54eb5`。这些是 manifest 的摘要口径，不与顶层 JSON 的文件字节摘要混用。
- 两份 manifest 中共同记录的源码只有 runner 和 summarizer 改变（验证标签与关闭日志门槛）；保护策略和所有权实现的记录 hash 未变。门槛代码提交为 `c643a1b`。

## 实验验收与关闭竞态

正式九格完成 612/612 个请求，均满足固定输出 token 合约、运行期 drain、引擎退出后的 owned-service/LMCache 清理与 GPU registration 归零。每格 H2D 为 1,811,939,328 B；非零组均有真实保护 commit 和对应 receipt。没有 cell 被排除。

三格（`one-r2`、`two-r1`、`two-r2`）各出现一次八行 `AsyncLLM output_handler failed → EngineDeadError`。它们在请求完成、资源 drain、runner 主动 SIGTERM 之后出现，并满足同 PID、已记录 stop 日志边界、EngineCore teardown/MPClient stop 次序、application shutdown complete 和退出码 0。依照 [关闭日志门槛](SHUTDOWN_LOG_GATE.md)，分别记录为 `shutdown_teardown_errors`，九格 `service_errors` 均为空。这不表示日志没有 ERROR，也没有修复 vLLM 本身。

早期 `protection-ablation-validation-20261011a/zero-r0` 的历史 `passed=false` 保持原样，保存在关闭门槛证据中，不并入本次九格。新门槛的 27 项专项测试及服务器完整回归（633 passed、9 skipped、115 subtests passed）已在正式重跑前完成；本次归档和文档更新没有改动运行代码。

## 结果

下表为三次重复均值 +/- 样本标准差，时间为 ms。每格实际 prefill 为 24,436 token，GPU hit 为 39,072 token，external hit 为 12,096 token，H2D 相同。因此本次没有观测到保护降低重算量或回载量。

| 预算 | TTFT P95 | 相对 zero | 回访 TTFT P95 | 会话完成 P95 | 服务端平均排队 | D2H GiB | 策略 wall 总时间 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| zero | 7148.41 +/- 40.53 | — | 2323.71 +/- 63.90 | 31449.77 +/- 100.61 | 3083.37 +/- 23.68 | 2.766 +/- 0.081 | 0 |
| one | 7107.42 +/- 195.61 | -0.57% | 2303.37 +/- 20.88 | 31374.88 +/- 17.16 | 3065.71 +/- 70.61 | 2.742 +/- 0.061 | 90.29 +/- 3.96 |
| two | 7170.89 +/- 106.04 | +0.31% | 2252.20 +/- 67.41 | 31344.98 +/- 108.55 | 3101.22 +/- 37.47 | 2.672 +/- 0.000 | 88.00 +/- 3.93 |

策略 wall 是含计数 instrumentation 的方法耗时总和，不是每请求开销，也不是端到端关键路径或 DMA 时间。one/two 的 peak pin 和 peak inflight 分别为 16/32 个物理 block；one 每格 commit/receipt 都为 2/2，two 分别为 2/2、1/1、2/2。zero 为 0。

| 预算 | r0 TTFT P95 | r1 TTFT P95 | r2 TTFT P95 | 相对同 repeat zero 的输出 hash 失配数 |
| --- | ---: | ---: | ---: | --- |
| zero | 7102.17 | 7177.83 | 7165.23 | 0 / 0 / 0 |
| one | 6884.12 | 7189.64 | 7248.51 | 0 / 7 / 0（每格 68 请求） |
| two | 7289.00 | 7083.86 | 7139.82 | 2 / 2 / 0（每格 68 请求） |

one 只有 r0 的整体 P95 低于同 repeat zero，另两次略高。two 的回访 P95 均值低 3.08%，但整体 P95 略高；不能把局部指标下降替代整体目标。one/two 会话完成 P95 分别低 0.24%/0.33%，D2H 分别低 0.85%/3.39%，均不构成稳定加速或因果证明。客户端 ready-to-first P95 均值为 zero 7149.09、one 7108.12、two 7171.64 ms，与 TTFT 结论一致，未把客户端排队隐藏在指标之外。

固定输入、到达和 token 合约不保证 decode 完全相同。上述输出 hash 差异必须保留；本次计时没有逐层 KV 探针，不能把历史 KV 正确性诊断或当前 HTTP 成功当作本轮逐请求文本等价证明。每组 n=3，不作统计显著性结论；文本/token 长度变化、运行时波动和调度都可能影响开发/验证差异，尚未唯一归因。

## 下一轮设计

阶段 3C 的实现与首轮消融已交付，P4 的首个独立输入复核完成，收益复现门槛仍未通过。保留本次结果作为未复现案例，不根据它修改策略再继续称同一输入为未见验证集。

下一步先冻结现有实现，预先记录 1.5/2.5 GiB GPU KV 两个新容量点，沿用本验证 trace、到达计划和 zero/one/two 三次轮转，对照已有 2 GiB 点。每个新点先做可行性/资源审计，再决定正式计时；校准与正式结果分目录保存。若 H2D 为零或保护始终回退，应报告为无回载/无保护覆盖的边界点，不混入当前要求 H2D 和保护 commit 的聚合，也不为了通过门槛悄悄修改负载。容量审计失败时先保留证据并排查，不连续重跑挑选成功样本。

之后再预先固定长 prefill、稳定 decode、集中恢复三类到达/长度配置和新的验证 namespace，补默认 horizon/提交上限调优与立即卸载对照。若调整策略，现有验证输入转为开发依据，使用新的未见集合评估。Qwen3-8B/5090 扩展在上述结果明确后进行，当前无需升级卡。

30 秒回执延迟空闲 owner、真实 driver/DMA fault、永久 I/O 恢复、多 GPU、异步调度与真实工具调用闭环仍不在本次通过范围。

## 证据与复现

- [统计 JSON](ablation-evidence/protection-ablation-validation-20261011b-analysis.json)：九格指标、输出 hash、关闭分类与聚合。
- [完整原始归档](ablation-evidence/protection-ablation-validation-20261011b.tar.gz)：trace、manifest、日志、请求结果、资源快照及 shutdown context。
- 归档 SHA256：`80157889fdc56b40a8958f0c3af641d1e2a171f09e5836c09a3b42baf2954910`，本地和服务器一致。

```bash
python scripts/run_protection_ablation.py \
  --output artifacts/protection-ablation-validation-20261011b \
  --repeats 3 --modes zero one two --workload-label validation-20261011a
python scripts/summarize_protection_ablation.py \
  artifacts/protection-ablation-validation-20261011b \
  --output artifacts/protection-ablation-validation-20261011b-analysis.json
```

以上为本次实际命令；再次复现须换新输出目录，保留旧证据。结束时已确认 runner、vLLM 和 LMCache 服务进程退出。
