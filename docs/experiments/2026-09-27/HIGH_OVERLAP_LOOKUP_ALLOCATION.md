# 高重叠到达轨迹：lookup 状态与物理分配复核

## 目的与边界

本轮补充一类高重叠持续到达轨迹，验证异步 lookup 状态是否能稳定提前预测实际 GPU block 分配。运行只启用 `preallocation-diagnostic` 观测：不 pin、不改变 free queue、不阻塞准入、不提交额外 STORE，也不修改 LMCache policy。诊断日志会改变时序，因此本轮数字不能作为性能对比。

## 实验条件

- RTX 4090 24GB，Qwen3-4B BF16，vLLM 0.30.0，固定 LMCache `0.5.5+g1a4b40b1d`。
- GPU KV 预算 2 GiB，CPU L1 16 GiB，`max_num_seqs=4`，单组 full-attention，block size 16 tokens。
- 20 个会话、4 轮、约 1280-token 初始上下文；同一轮 20 个会话同时到达，轮间隔 600 ms。每次输出 48 tokens。
- 相同 trace 重复两次；每次 80 条测量请求，均无 HTTP/SSE/长度错误。
- 原始 trace 和两个 scheduler ledger 位于 `artifacts/preallocation-high-overlap-2026-09-27-final/`；分析输出由 `scripts/analyze_preallocation.py` 生成。

## 逐请求配对结果

新加入的 `lookup_allocation_pairs` 按 request id 和单调时钟，把 allocator 的真实 `get_new_blocks()` 返回与此前最近的 lookup 事件、同一 upcoming step 的 `pre_step` lookup 状态配对。两次重复结果为：

| 重复 | 实际分配 blocks | allocator 事件 | 异步分配事件 | 异步 blocks | 异步事件有 lookup | 异步事件有 pre-step 状态 | STORE 提交/回执 |
|---|---:|---:|---:|---:|---:|---:|---:|
| r0 | 6,149 | 337 | 27 | 2,136 | 27 | 27 | 41/41 |
| r1 | 5,913 | 335 | 25 | 2,000 | 25 | 25 | 39/39 |

异步分配对应的 pre-step 状态几乎全部为 `result_available`，少数为 `resolved`；本轮没有出现缺少 lookup 事件的异步 allocator 分配。worker 回执延迟（提交到收到 worker receipt，包含排队和回执处理，不等于 DMA 时间）：r0 P50/P95 为 142.9/285.4 ms，r1 为 146.4/294.5 ms。

## 预测口径比较

突发阈值为单步至少 128 blocks。`prior` 表示在发生受影响操作之前已有更早报警；`lead50` 表示该报警距离首次 allocator 覆盖至少 50 ms。

| 重复 | 口径 | 报警步 | 突发步 | 同步命中 | 无效报警 | 受影响操作 | prior | lead50 |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| r0 | `compute_slots` | 14 | 14 | 9 | 5 | 10 | 0 | 0 |
| r0 | `lookup_ready` | 454 | 14 | 14 | 440 | 10 | 4 | 4 |
| r0 | `lookup_inflight` | 531 | 14 | 14 | 517 | 10 | 4 | 4 |
| r0 | `async_admission` | 937 | 14 | 14 | 923 | 10 | 6 | 6 |
| r1 | `compute_slots` | 16 | 15 | 10 | 6 | 12 | 0 | 0 |
| r1 | `lookup_ready` | 461 | 15 | 15 | 446 | 12 | 1 | 1 |
| r1 | `lookup_inflight` | 538 | 15 | 15 | 523 | 12 | 4 | 4 |
| r1 | `async_admission` | 943 | 15 | 15 | 928 | 12 | 8 | 8 |

## 判断

1. 高重叠负载再次证明普通 `compute_slots` 不是物理分配上界；异步回载分配可以在普通计算压力估计之外发生。
2. 新的逐请求配对没有发现“异步分配但没有 lookup 证据”的案例，说明记录链路在本负载上闭合。
3. `lookup_ready` 比宽泛 `async_admission` 收敛，但每次重复仍有 440/446 个无效报警；`lookup_inflight` 相比 `lookup_ready` 没有稳定增加有效提前覆盖，仍不能直接驱动 pin 或准入保护。
4. 两次重复均没有 prior risk（`compute_slots`）；lookup 口径虽在少数受影响操作上提前报警，但诊断轨迹尚不能证明保护动作有足够时间完成 STORE。当前不实现提前 pin/准入原型。

下一步应把重点转向取消、显式抢占、保存失败和 request-id 重用的生命周期状态机测试；同时保留本轮高重叠轨迹作为 lookup→allocation 的回归样本。只有在这些路径下仍能闭合资源和 generation 语义，并且提前量与误报可量化时，才考虑保护实现或上游接口提案。

## 复现

```bash
python scripts/generate_trace.py --model /root/CachePilot/models/Qwen3-4B \
  --output artifacts/preallocation-high-overlap-trace-2026-09-27.json \
  --sessions 20 --turns 4 --context-tokens 1280 \
  --period-ms 600 --session-gap-ms 0

python scripts/run_baselines.py --modes preallocation-diagnostic \
  --trace artifacts/preallocation-high-overlap-trace-2026-09-27.json \
  --repeats 2 --output artifacts/preallocation-high-overlap-2026-09-27-final
```
