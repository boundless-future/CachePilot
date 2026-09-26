# 准入前需求观测：第一次压力诊断

## 结论

**暂不进入提前 pin/准入保护原型。** 在单次 12 会话合成压力轨迹中，只按计算槽估计需求会漏掉异步 KV 回载；把前四个 waiting 请求都视为可能回载虽然覆盖了分配突发，却产生大量无效报警。按已记录的候选、free queue 排名和实际 allocator 覆盖核对，只有一项受影响操作得到超过 50 ms 的风险提前量。尚无 STORE 完成时长、保护容量代价和收益证据。

这是一轮诊断，不是性能对比。诊断 JSON 写入会改变调度时序，不能用本轮 TTFT 与无日志策略比较。

## 接入与口径

独立 `preallocation-diagnostic` 模式在 vLLM 0.30.0 `Scheduler.schedule()` 开始时记录 waiting/running 状态、剩余 token、已有 GPU blocks、free queue、pending STORE 候选及 hash 状态。既有 allocator 包装在真实 `get_new_blocks()` 返回后记录物理分配，策略仍在原时机 drain。未 pin、未修改 free queue、未提交额外 STORE，也未调用外部 KV lookup。只支持当前单组 full-attention Qwen3-4B；该观测包装不是通用 vLLM hook。

设备及负载：RTX 4090 24GB、Qwen3-4B BF16、2 GiB GPU KV、16 GiB CPU cache，12 会话、4 轮、每轮 48 生成 tokens。引擎和 cache 单独启动，warmup 后重置 GPU prefix cache。48 条测量请求无 HTTP/SSE/长度错误；账本另含 warmup。666 个 scheduler 步，成功分配 6,756 blocks，修正信号消费 6,756，无残留；独立分配审计通过。

以单步实际分配至少 128 blocks 为“突发”，本轮共 33 步。`compute_slots` 只把可用计算槽内的 waiting 请求计入下一步：74 步报警，其中 29 步当步确有突发、45 步没有；历史 EMA 超过同一阈值仅 4 步，且均非这 33 个突发步。这个估计**不是全部物理分配的上界**。

根因是 vLLM 能在一个 `schedule()` 中连续准入多个异步回载：它们先占用 GPU blocks，但不作为该步模型计算请求消耗普通并发槽。例如第 174 步在只估计约 131 blocks 时连续分配 3 x 128；第 329 步估计约 136，却连续分配 4 x 128。不能把 waiting 队列第一个阻塞于 `WAITING_FOR_REMOTE_KVS` 的请求当成调度遍历终点。

离线再用**同一账本**评估 `async_admission` 上界：对快照中前四个 `WAITING/PREEMPTED` 请求按剩余长度估计可能回载块，未重复做外部 lookup。它覆盖 33/33 个突发步，但 666 步中有 590 步报警，557 步当步没有突发。该规则是在看到诊断后提出，结果只用于判断是否值得独立验证，不是独立测试集上的精度。

| 同一账本候选风险关联 | 计算槽估计 | 异步准入上界（事后提出） |
|---|---:|---:|
| allocator 确认发生覆盖的操作 | 8 | 8 |
| 覆盖前一个或更多调度步已报警 | 1 | 3 |
| 仅在覆盖当步才报警 | 0 | 5 |
| 至少 50 ms 提前量 | 1 | 1 |
| 被标为风险但账本未见覆盖的操作 | 4 | 32 |

8 个操作并非 8 个独立请求，也不等于策略的全部 `dropped_evicted`。同一步的 5 个异步上界报警距离覆盖只有约 1.0–3.4 ms，不能推定 STORE 来得及完成；另两个更早报警约 26.5–27.3 ms，一个约 221.9 ms。“未见覆盖”也不能直接称为误报：操作可能在覆盖前已经保存、取消或退出 pending。

## 阶段决定

本次观测否定了“计算槽即物理分配上界”，但也没有证明“宽泛异步上界”可用于保护。下一步先寻找无需重复外部 lookup 的、更有区分度的回载需求信号，例如请求级异步 lookup 已完成状态或可读的 connector 状态；在独立压力轨迹上核对覆盖率、候选风险提前量、无效报警和 STORE 完成时间。只有多次运行都出现稳定的几十到几百毫秒有效提前量，且保护预算允许 scheduler 前进，才开展 pin/unpin 状态机与 worker 生命周期实现。

本轮请求完成后，SIGTERM teardown 仍出现 `AsyncLLM output_handler` / `EngineDeadError`，与此前个别单元所见相似；不能据此宣布完整生命周期验收通过。GPU 与实验服务在回放后已释放。

## 复现

```bash
python scripts/run_baselines.py --modes preallocation-diagnostic \
  --workloads exceeds-gpu --repeats 1 \
  --output artifacts/preallocation-diagnostic-new
python scripts/analyze_allocation_signal.py \
  artifacts/preallocation-diagnostic-new/exceeds-gpu-preallocation-diagnostic-r0/decisions/scheduler-*.jsonl \
  --output artifacts/preallocation-diagnostic-new/allocation-audit.json
python scripts/analyze_preallocation.py \
  artifacts/preallocation-diagnostic-new/exceeds-gpu-preallocation-diagnostic-r0/decisions/scheduler-*.jsonl \
  --output artifacts/preallocation-diagnostic-new/preallocation-analysis.json
```

原始数据、日志、输入轨迹和运行清单保存在 `artifacts/preallocation-diagnostic-2026-09-26/`；汇总结果见 [需求分析](preallocation-analysis.json) 和 [分配审计](preallocation-allocation-audit.json)，归档位置与 SHA256 见 [证据清单](preallocation-evidence-manifest.json)。分析脚本在实验之后增加了事后异步上界与同一步风险区分，运行时脚本 hash 以 `run-manifest.json` 为准。
