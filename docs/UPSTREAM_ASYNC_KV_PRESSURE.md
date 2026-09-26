# 异步 KV 回载与 lazy-offload 压力信号：上游候选问题

状态：**项目内已复现时间错位；截至本次核查，未发现上游对此特定压力信号缺口的直接修复。** 记录日期：2026-09-26。项目完成后根据复现、收益和接口稳定性决定是否向 LMCache 提交 issue/PR，不预设一定提交。

## 准确的问题边界

这里有三个不同的计数，不能混为一谈：

1. CachePilot 的 `compute_slots` 是按普通模型计算槽估计 waiting 请求的离线预测规则。它不是 vLLM 的实际 block admission，也不是分配安全性的上界。异步回载可在零模型 token 的情况下分配 GPU blocks，所以这个规则低估物理分配突发。
2. vLLM 0.30.0 的 scheduler 已为 `load_kv_async` 调用 `allocate_slots(..., delay_cache_blocks=True, reserved_blocks=...)`，并通过 `_inflight_prefill_reserved_blocks()` 考虑在途回载/预填充需求。异步请求随后进入 `WAITING_FOR_REMOTE_KVS`。因此**不能说 vLLM 漏掉异步回载的准入或物理 block 分配**。
3. 固定 LMCache 版本的 `LazyOffloadManager._new_blocks()` 仅从 `SchedulerOutput.scheduled_new_reqs` 和 `scheduled_cached_reqs.new_block_ids` 计算 `DrainSignals.new_blocks_allocated`。异步回载准入时已分配的 block 可能不在当步普通执行请求列表中；请求恢复执行时，先前的 block 又可能出现在输出里。这是 **allocator 的实际分配与 lazy-offload policy 的压力观测存在时间错位**，不代表物理 block 永久漏记，也不等于 vLLM 分配错误。

源码证据保存在 [allocation-source-evidence.json](experiments/2026-09-26/allocation-source-evidence.json)，对应运行环境是 vLLM 0.30.0、LMCache `1a4b40b1d79b0e76244f127f96ee0982f8bd270f`。2026-09-26 再查 LMCache `dev` 的 [`lazy_offload_manager.py` 固定快照](https://github.com/LMCache/LMCache/blob/dc68527c76fd08c5501e19097973938143f06348/lmcache/integration/vllm/lazy_offload_manager.py)：`_new_blocks()` 仍使用上述两个 scheduler 输出列表；未看到此特定压力信号缺口的修复。上游状态可能变化，提报前必须重新核对。

## 已有证据与影响

- [策略诊断](experiments/2026-09-26/STRATEGY_DIAGNOSIS.md)中，单步 256 个实际分配 block 对应策略信号 128，另一步 512 对应 0；恢复执行时也出现实际仅分配 6 个、策略却看到 134 的时间错位。11 个丢弃操作有 allocator 复用证据，但不能把丢弃数直接等同于重算或延迟损失。
- [实际分配信号消融](experiments/2026-09-26/ALLOCATION_SIGNAL.md)以 `get_new_blocks` 成功返回的数量替换策略信号，独立账本 6,494/6,494 blocks 闭合。压力负载三次重复的复用轮 TTFT P95 均值改善 2.82%，实际 prefill 减少 6.21%；同时 D2H 增加 5.54%、H2D 增加 7.35%，延迟仍比立即卸载高约 19.37%。这些只是一个受控压力点的局部收益，不证明通用修复有效。
- [需求信号复核](experiments/2026-09-26/LOOKUP_SIGNAL_REPLICATION.md)显示 `compute_slots` 只同步命中 4/12 和 7/17 个物理突发步；更宽泛的异步上界虽覆盖突发，却产生大量无效报警。准确的历史分配计数也发生在分配之后，救不回同一步已经被覆盖的 KV。

项目**不需要先修复上游才能继续**：现有 vLLM/LMCache 路径可运行，默认策略、立即卸载和项目原型可以在相同锁定版本下对照。这个缺口会影响依赖 LMCache 原始压力信号的预测质量和我们对策略退化的解释，因此后续报告必须同时给出真实 allocator 分配、策略收到的信号及分配/保存时序；不能把原始信号当成物理分配真值。当前 `allocation` 模式作为消融对照保留，默认策略不静默替换。

真正可能阻碍后续**提前保护**原型的是另一个接口约束：现有 manager drain 在分配之后，零 token 步不提交 STORE。要在复用之前完成保存，需要独立验证准入前决策、pin 预算、worker 提交和回执生命周期；单独修正 `_new_blocks()` 不能满足这一目标。详见[分配前设计](experiments/2026-09-26/PREALLOCATION_DESIGN.md)。

## 上游 PR 的进入条件

项目阶段收尾时再评估是否提报，至少完成以下核查：

- 在当时 LMCache `dev` 与兼容 vLLM 版本上复现，搜索现有 issue/PR，确认不是版本已修复或重复提报。
- 给出最小测试：异步回载准入但当步无普通模型 token、回载完成后恢复执行、多个回载同一步、零 token 步延迟消费，以及取消/抢占路径；逐步核对 allocator 真值与 policy 实际接收值，避免重复计数。
- 明确上游愿意接受的接口：vLLM 是否暴露可靠的分配事件/计数，或 LMCache 是否需要局部记录实际成功分配。不得假定当前 `SchedulerOutput` 两个执行列表完整代表物理分配。
- 把“计数正确”与“策略收益”分开验收。基准中比较默认、仅计数修正与立即卸载，控制资源、输入、版本和日志开销，并报告重算、TTFT、搬运及资源回收。

相关但范围不同的上游工作：[LMCache #3382](https://github.com/LMCache/LMCache/pull/3382)限制高并发回载占满 GPU blocks；[LMCache #4847](https://github.com/LMCache/LMCache/pull/4847)引入 eviction-aware lazy offload；[vLLM #42568](https://github.com/vllm-project/vllm/pull/42568)讨论异步回载完成后的饱和调度。这些不能直接视作本压力信号缺口已修复。
