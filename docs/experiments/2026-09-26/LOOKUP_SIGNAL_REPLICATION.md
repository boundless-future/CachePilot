# 异步 KV 回载需求信号：第二类轨迹复核

## 实验设计

本轮使用与 `LOOKUP_SIGNAL_VALIDATION.md` 不同的独立压力轨迹：20 个会话、4 轮、初始上下文约 1280 tokens、轮间到达间隔 2300 ms、同一时间点批量到达（session gap 为 0 ms），每次 80 条请求。环境仍为 RTX 4090 24GB、Qwen3-4B、2 GiB GPU KV 预算。运行时只开启 `preallocation-diagnostic` 观测，不做 pin、准入阻塞或额外 STORE。

由于 scheduler 和异步回载存在运行时调度差异，两次重复使用相同 trace，但不假设物理分配步完全一致。四种预测规则、128 blocks 突发阈值和 allocator 风险关联口径与上一轮相同。

## 结果

| 重复 | 口径 | 报警步 | 突发步 | 同步命中 | 无效报警 | 提前风险操作 | 至少 50 ms 提前 | 未见覆盖风险 |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| r0 | `compute_slots` | 11 | 12 | 4 | 7 | 2 | 2 | 0 |
| r0 | `lookup_ready` | 303 | 12 | 7 | 296 | 4 | 4 | 0 |
| r0 | `lookup_inflight` | 451 | 12 | 7 | 444 | 4 | 4 | 3 |
| r0 | `async_admission` | 945 | 12 | 12 | 933 | 6 | 6 | 20 |
| r1 | `compute_slots` | 13 | 17 | 7 | 6 | 3 | 3 | 0 |
| r1 | `lookup_ready` | 475 | 17 | 11 | 464 | 4 | 4 | 1 |
| r1 | `lookup_inflight` | 524 | 17 | 11 | 513 | 4 | 4 | 1 |
| r1 | `async_admission` | 948 | 17 | 17 | 931 | 9 | 9 | 16 |

`compute_slots` 只在 4/12 和 7/17 个突发步同一步报警，继续证明普通计算槽不是物理分配上界。宽泛 `async_admission` 覆盖全部突发，但两次重复分别有 933 和 931 个无效报警，不能直接驱动保护。

`lookup_ready` 与 `lookup_inflight` 在两次重复中的同步命中数完全相同，提前风险关联数也相同；`lookup_inflight` 反而额外标记了 3 个和 1 个未见覆盖的风险操作。这说明把所有在途 lookup 按完整剩余长度计入，会增加风险候选，却没有在本轨迹上带来更多已观测提前覆盖。`lookup_ready` 仍有 296/464 个同步无效报警，区分度不足以直接实现 pin 或准入保护。

每次 80 条请求均无错误，两个账本的分配审计均通过：r0 为 6,073/6,073 blocks，r1 为 6,574/6,574 blocks，均无未消费块。STORE 提交与 worker 回执均完整配对：r0 为 21/21，worker 回执 P50/P95 为 160.5/244.1 ms；r1 为 20/20，P50/P95 为 164.0/281.5 ms。该时间包含 worker 排队和回执处理，不等于 DMA 时间。

本轮请求级 P95 TTFT 分别为 5,172 ms 和 5,617 ms，P95 端到端延迟为 5,718 ms 和 6,195 ms。由于本模式包含诊断账本写入，且没有无观测对照，这些数字只作为运行健康记录，不能解释为策略收益或退化。

## 阶段结论

第二类轨迹没有提供足够证据进入提前 pin/准入保护。当前最可信的结论是：

1. 物理分配压力需要观察异步回载路径，普通计算槽不足以作上界。
2. 已完成 lookup 状态比宽泛 waiting 上界更收敛，但误报仍多。
3. `lookup_inflight` 在本轮重复中没有增加有效提前覆盖，不能因为“在途”就保护资源。
4. worker 回执通常在约 0.16--0.28 秒内完成，但仍需要在取消、抢占、保存失败和不同到达节奏下验证生命周期。

下一步应补一类高重叠持续到达或显式抢占轨迹，并至少再做一次重复；同时记录 lookup 状态到实际分配的逐请求配对。只有误报下降、提前量稳定且保护预算可量化时，才实现可回滚的 pin/unpin 原型。

## 复现与证据

```bash
python scripts/generate_trace.py --model /root/CachePilot/models/Qwen3-4B \
  --output artifacts/burst-trace.json --sessions 20 --turns 4 \
  --context-tokens 1280 --period-ms 2300 --session-gap-ms 0
python scripts/run_baselines.py --modes preallocation-diagnostic \
  --trace artifacts/burst-trace.json --repeats 2 --output artifacts/burst-runs
```

原始 trace、两次账本、日志、请求结果、四种分析和分配审计已打包为 `artifacts/preallocation-burst-validation-raw-2026-09-26.tar.gz`，SHA256 为 `a48dc9290807af99ec1357fcfc9eaed8bdf0d46d7ad1d1711856d3335a1330e0`。
