# 实际分配信号消融

## 问题与实现

此前 allocator 诊断发现：异步 CPU KV 回载可以先分配 GPU 块，原来的 `new_blocks_allocated` 却从执行请求列表提取，因此分配当步可能低估，恢复执行时又可能高估。见 [上一轮诊断](STRATEGY_DIAGNOSIS.md)。

本轮新增独立 `allocation` 配置。`AllocationConnector` 在真实 `get_new_blocks` 成功返回后累加 `len(result)`，在上游已有的 policy drain 中替换 `DrainSignals.new_blocks_allocated`，其余信号及固定 horizon=2.5 不变。它不修改 allocator 的返回值、块队列、哈希检查、pin/unpin、批次合并、完成回执，也不提前调用 drain。

计数口径是成功分配的物理块总数，不是 free queue 长度净变化，不是 token 数，也不保证等于从已有缓存驱逐的块数。请求 block table 恢复后不会再次记账。失败的分配不计数；同一 pool/policy 重复绑定不叠加包装器，绑定不同 pool 直接报错。

**零 token 步的处理**：manager 原本跳过 drain，本实验保持这个时机。因此这些步的分配保留到下一次真实 drain 消费一次；信号是“自上次策略观察以来的分配总量”，不能把它误写为任何情况下都只含当前 scheduler step。EMA 仍按原来的 drain 次数更新，没有同时修改时间尺度。诊断同时记录 `actual_step_blocks` 与 `consumed_blocks`，二者可以不同。

模块边界：

```text
vLLM block_pool.get_new_blocks
  → AllocationPressure.record（仅计数）
vLLM build_connector_meta
  → begin_step（保存本步实际数量）
  → LMCache manager.on_scheduler_step（原有时机）
  → replace(new_blocks_allocated=未消费的实际数量)
  → 上游 EVICTION_AWARE → 上游校验/pin/合并/STORE/回执
```

`allocation` 不导入 DecisionConnector，不逐步写 JSON；`allocation-decision` 才启用额外观测，不能用于性能结论。

## 独立诊断验收

先执行 12 会话压力诊断，再启动性能对比。48 条测量请求无错误，账本含额外 1 条 warmup。

- 独立 allocator 账本记录 **6,494** 个成功分配块；策略信号消费 **6,494** 个，结束时未消费数为 0。
- 648 次真实 drain，26 次信号值与旧口径不同。
- 第 175 步：旧值 0、实际/消费 384；第 182 步：旧值 132、实际/消费 4。既修复当步低估，也移除恢复执行时的重复归入。
- 第 335 步无模型 tokens 但分配 128 块；第 336 步新增 6 块，消费累计 134；第 337 步消费 0。空闲步压力保留且只消费一次。
- 11 个丢弃操作仍存在，全部有真实复用证据，10 个关联到后续相同前缀缺失。**计数通过不等于性能通过，也不代表能挽救本步已覆盖的 KV。**

`analyze_allocation_signal.py` 根据独立 allocation 事件逐个检查：每步数量、累计数量、消费数量、策略真正接收的值；空数据不会通过。详细结果见 [allocation-signal-diagnostic.json](allocation-signal-diagnostic.json)。

## 性能实验口径

同一张 RTX 4090 24GB、Qwen3-4B、BF16、2 GiB GPU KV、16 GiB CPU cache，沿用本日固定软件版本。默认 EVICTION_AWARE、allocation、immediate 三组；4/12 会话、各 4 轮、每轮 48 生成 tokens，每组重复三次，轮换模式顺序。共 18 个单元、576 条测量请求。每个单元重新启动引擎和 CPU cache，warmup 后重置 GPU cache。

以同轮默认与立即卸载为对照；与旧实验的绝对延迟不混排。报告每次运行的复用轮 TTFT P95 及均值/范围、实际 prefill tokens、排队、D2H/H2D。三次重复的范围不是置信区间。预设判断：只有丢弃变少而重算与延迟不改善，不将其称为有效优化。

## 完整结果与第三步决定

18 个性能单元、576 条请求均通过 HTTP/SSE/usage 长度检查。下表为三次运行各自复用轮 TTFT P95 的均值及最小–最大值，不是合并请求的 P95。

| 负载 | 默认 EVICTION_AWARE | 实际分配信号 | 立即卸载 |
|---|---:|---:|---:|
| 4 会话 | 85.8 ms（84.6–88.0） | 84.2 ms（80.8–88.4） | 84.8 ms（75.5–92.4） |
| 12 会话 | 3624.1 ms（3577.7–3707.8） | 3521.8 ms（3439.9–3569.7） | 2950.3 ms（2910.7–2993.6） |

压力下逐次默认→修正信号为 3707.8→3569.7、3577.7→3439.9、3586.8→3555.8 ms，分别改善约 3.73%、3.85%、0.87%。均值改善 **2.82%**。三次方向一致但幅度不稳定，只有一个合成压力点，尚不能声称通用或统计显著收益。

| 压力负载指标，每次运行的均值 | 默认 | 修正信号 | 立即卸载 |
|---|---:|---:|---:|
| 实际 prefill 计算 tokens | 54,978.7 | 51,565.3 | 35,864 |
| 外部命中 tokens | 46,421.3 | 49,834.7 | 65,536 |
| 请求平均排队 ms | 2175.5 | 2137.4 | 1858.7 |
| D2H GiB | 3.176 | 3.352 | 3.375 |
| H2D GiB | 6.375 | 6.844 | 9.000 |

修正信号减少 **6.21%** 实际 prefill，增加 **5.54%** D2H 与 **7.35%** H2D。其 D2H 已接近立即卸载，延迟仍比立即卸载高约 **19.37%**，说明只改计数尚未解决保存时机的问题。低压力下默认/修正都不搬运；立即卸载平均写出 1.125 GiB、没有 CPU 回载，三者低压力延迟差异不能作为稳定优势。

默认压力丢弃数为 14/13/13，修正为 8/11/12。candidate admission 数随缓存状态改变，因此不把丢弃减少直接写成固定比例的重算节约。性能结论使用实际 prefill、搬运及客户端延迟。完整数据见 [allocation-signal-analysis.json](allocation-signal-analysis.json)。

**第三步决定：保留计数修正为消融配置，继续研究提前需求感知，暂不替换默认策略。** 事后信号有小幅局部改善，但无法消除同一步覆盖，且尚未达到立即卸载的性能。下一实现应先观察准入前的真实可用需求，验证提前量与误报，再决定加入 manager 的提前保护。对应源码核查、已观测提前机会、拟议状态流程和验收条件已经整理到 [前移决策设计](PREALLOCATION_DESIGN.md)。本轮没有实现 scheduler 准入控制或零 token STORE 路径，不把设计稿视作完成的 GPU 优化。

## 验收与限制

本地 Windows 与远程 Linux 均通过 24 项测试，覆盖独立信号审计、空数据不得通过、异步分配/恢复、零 token 累计、异常传播、重复绑定及既有回放测试。诊断 48 条请求另计，不纳入 576 条性能请求。

两个性能单元（`exceeds-gpu-allocation-r0`、`fits-gpu-immediate-r2`）在请求处理完毕、服务开始 SIGTERM teardown 后出现 `AsyncLLM output_handler` / `EngineDeadError`，附近有 CUDA IPC 释放警告；日志保留，不将本轮表述为全部生命周期正确。该异常也出现在未使用修正信号的 immediate 组，但其根因仍未单独解决。其余单元没有 ERROR 行。既有 KV bitwise 验证不覆盖本轮全部并发路径，本轮也未新增逐 token 输出一致性承诺。

## 复现入口

```bash
python scripts/run_baselines.py --modes allocation-decision \
  --workloads exceeds-gpu --repeats 1 --output artifacts/allocation-diagnostic-new
python scripts/analyze_allocation_signal.py \
  artifacts/allocation-diagnostic-new/exceeds-gpu-allocation-decision-r0/decisions/scheduler-<pid>.jsonl \
  --output artifacts/allocation-diagnostic-new/analysis.json
python scripts/run_baselines.py --modes eviction allocation immediate --repeats 3 \
  --output artifacts/allocation-comparison-new
python scripts/summarize_baselines.py artifacts/allocation-comparison-new \
  --output artifacts/allocation-comparison-new/analysis.json
python -m unittest discover -s tests -v
```

所有 GPU 实验串行执行并使用新目录。前移决策的源码核查与方案见 [PREALLOCATION_DESIGN.md](PREALLOCATION_DESIGN.md)。

## 归档

原始请求、指标、输入 trace、每单元版本清单、诊断账本、分析结果、运行脚本/配置/测试和所用上游源码保存在本地及服务器的 `artifacts/allocation-signal-evidence-2026-09-26.tar.gz`。SHA256 为 `9f240f442fa87757d4e15611174ed65e61dcad85bbdd2bb8165bda33fc536bb4`，两端一致；详见 [归档清单](allocation-signal-evidence-manifest.json)。性能运行实际加载的源码和配置逐个匹配运行时清单。

实验结束后 8000/5556/8081 端口均关闭，GPU 显存 1 MiB、利用率 0%；服务器保持开机。
