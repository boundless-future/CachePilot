# 异步 KV 回载需求信号验证

## 目的与边界

本实验验证准入前观测能否提前发现异步 KV 回载造成的物理 block 分配压力，并检查 STORE worker 回执的可观测时间。它是诊断实验，不是性能对比；观测账本会增加调度路径开销，因此本结果不用于声称 TTFT 或吞吐收益。

实验运行在 RTX 4090 24GB、Qwen3-4B、2 GiB GPU KV 预算上。独立合成轨迹包含 16 个会话、每个 5 轮、初始 1536 tokens、轮间 1400 ms、会话间 25 ms，共 80 条测量请求。阈值定义为单个 scheduler step 实际分配至少 128 blocks。原始 trace、请求、日志、决策账本和运行清单已归档。

## 预测口径

四种口径均只读取每个 `schedule()` 前可见的快照：

* `compute_slots`：只按普通计算槽估计 waiting 请求需求。
* `async_admission`：把前四个 waiting/preempted 请求的剩余长度都当作潜在异步回载需求。这是宽泛上界，不是外部 lookup 的真实结果。
* `lookup_ready`：只有 lookup 状态已解析为 `resolved` 或 `result_available` 时，按已知命中 token 估计。
* `lookup_inflight`：在 lookup 仍处于 `awaiting_ack` 或 `status_pending` 时，暂按剩余长度计入，测试“在途即预警”的上界。

预测是在实验后按预先写入的规则离线重放；没有根据本账本调阈值或修改运行时策略。

## 结果

| 口径 | 报警步 | 同步突发步 | 同步无效报警 | 有提前风险关联的操作 | 至少 50 ms 提前 | 风险操作中未见覆盖 |
|---|---:|---:|---:|---:|---:|---:|
| `compute_slots` | 9 | 5 | 4 | 1 | 0 | 0 |
| `lookup_ready` | 486 | 12 | 474 | 5 | 4 | 1 |
| `lookup_inflight` | 583 | 13 | 570 | 9 | 9 | 1 |
| `async_admission` | 907 | 17 | 890 | 13 | 13 | 15 |

本轨迹共有 17 个物理突发步、8,354 个分配块。`compute_slots` 明显低估异步回载：只在 5 个突发步同一步报警。`lookup_ready` 和 `lookup_inflight` 能抓到更多突发，但报警密度很高；其中 `lookup_inflight` 的 9 个提前风险关联不能单独说明保护来得及，因为其中仍有 3 个只在覆盖当步报警，而且“未见覆盖”可能是保存完成、取消或退出 pending，不能直接称作误报。`async_admission` 覆盖全部 17 个突发，但 907 个报警步中 890 个当步没有突发，不能直接用于保护。

风险关联使用 allocator 的 `affected` 事件核对，表示观察到已有前缀被覆盖的操作，不等于性能损失，也不等于所有 `dropped_evicted`。分配审计通过：8,354 个分配块与 8,354 个消费块一致，残留为 0。

## STORE 回执时间

账本中的 `store_submit` 与 `store_worker_receipt` 可以逐请求配对：14 个提交批次对应 14 个 worker 回执，无未匹配回执，也无未完成批次。提交到 worker 回执的端到端观测延迟 P50 为 179.6 ms，P95 为 258.3 ms，最大值为 258.3 ms。这个区间包含 worker 排队和回执路径，不等于 GPU DMA 时间，也不能单独证明回载或保护收益。

## 阶段决定

本次独立轨迹支持两个结论：普通计算槽不是物理分配上界；异步 lookup 状态比宽泛“所有 waiting 都可能回载”更有区分度，但当前仍有大量无效报警。暂不实现提前 pin、准入阻塞或保护容量分配。下一步应在至少两类独立压力轨迹和多次重复中继续观测，并把 lookup 状态、请求取消/抢占、STORE 提交和完成生命周期配对；只有在有效提前量稳定、误报可接受且能测量保护代价后，才进入可回滚的保护原型。

## 复现

```bash
python scripts/analyze_preallocation.py <ledger> --forecast lookup_ready --output lookup_ready.json
python scripts/analyze_preallocation.py <ledger> --forecast lookup_inflight --output lookup_inflight.json
python scripts/analyze_preallocation.py <ledger> --forecast compute_slots --output compute_slots.json
python scripts/analyze_preallocation.py <ledger> --forecast async_admission --output async_admission.json
python scripts/analyze_allocation_signal.py <ledger> --output allocation-audit.json
```

证据归档：`artifacts/preallocation-lookup-validation-raw-2026-09-26.tar.gz`，SHA256 为 `9691f627d88dc38ef44368ca9ab66a8c3b778489c1dbbca5996f057749bc9641`。四个小型分析 JSON 和分配审计 JSON 位于 `artifacts/`，便于无需解压原始账本即可复核汇总数值。
