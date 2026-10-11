# 新文本零保护覆盖：一次独立观测

## 结果

按 [预注册](COVERAGE_DIAGNOSTIC_PREREGISTRATION.md) 仅运行一次，保持原 one/2 GiB/h2.5/d64/15 秒宽限与同一新文本 trace，开启已有 handoff JSONL。68 请求、实际 token、H2D、资源和退出检查均通过；保护提交/回执仍是 **0/0**，原 performance gate 继续为 false。

本轮记录到 **9 个不同 wire generation 的首次 capacity fallback**，全部满足：剩余保护预算 16 block、在途保护 0 block、实际新增 pin 0、所需 16 block，而 free 小于 reserve+16。随后每个 generation 都有一次默认 manager STORE。没有修改策略、增加保护预算、追加压力或重跑筛选。

原未开诊断的失败轮是 8 个首次回退，本轮是 9 个；日志会改变时序，不能要求两轮事件序列相同。本轮延迟不进入性能聚合。

## 观察到的容量缺口

一块为 16 tokens；一 chunk 需要 16 block。以下为首次回退点，按原事件顺序记录，不是每次尝试。

| 点 | free blocks | conservative reserve | required | reserve+required-free | 剩余预算 | 在途 pin | 后续默认 STORE |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | 704 | 1502 | 16 | 814 | 16 | 0 | 1 |
| 2 | 582 | 2208 | 16 | 1642 | 16 | 0 | 1 |
| 3 | 703 | 972 | 16 | 285 | 16 | 0 | 1 |
| 4 | 497 | 1366 | 16 | 885 | 16 | 0 | 1 |
| 5 | 423 | 1516 | 16 | 1109 | 16 | 0 | 1 |
| 6 | 486 | 1816 | 16 | 1346 | 16 | 0 | 1 |
| 7 | 486 | 1816 | 16 | 1346 | 16 | 0 | 1 |
| 8 | 486 | 1816 | 16 | 1346 | 16 | 0 | 1 |
| 9 | 542 | 1682 | 16 | 1156 | 16 | 0 | 1 |

`conservative_reserve` 来自所有 live request 完整 footprint 的两倍，加 watermark 和 16 block；它故意不减去已驻留块/命中，也不把 lookup 状态当准入 oracle。这组观察解释了**当前安全约束为何拒绝保护**；它不证明这些全部是下一步真实需要分配的块，也不证明缩减 reserve 后必然安全。

## 对后续策略设计的影响

1. 当前失败是触发范围不足，没有请求失败、资源残留或已观察到的 KV 损坏。没有理由为了“通过”而把 coverage=0 改成通过。
2. 单纯增大 max_chunks 无法解除这类约束：本轮保护预算始终空闲，限制来自物理预留。
3. 若继续设计 reserve v2，先做只观察的逐 scheduler-step 分解：已有 resident 引用、真正新增分配、异步回载保留、抢占和队列候选。沿用实际分配证据与身份检查，不能退回 `compute_slots` 或单纯 lookup 状态估计。
4. 更小 reserve 要另立安全契约与 held-out workload，并重跑分配排除、取消/抢占/回执/失联资源回收测试；本轮不修改现有策略。此前数据继续属于 v1。
5. 先补长 prefill/不同 decode 比例，确认缓存收益有足够空间；仅改保护触发频率可能增加开销，不能预先承诺收益。

## 证据与验证

- [诊断原始包](coverage-evidence/protection-coverage-diagnostic-20261011a.tar.gz)，SHA256 `f1958bd72cabd44706faa9148e00cffd8eec75c5b2eab85ddba63e5f6ab32346`。
- [独立诊断结果](coverage-evidence/coverage-diagnostic.json)：`diagnostic_passed=true`、`original_performance_gate_passed=false`、`zero_coverage_reproduced=true`。本地从 handoff 原始行重算 9 个观察并核对时间戳/token，通过且与服务器一致。
- 真实 CUDA/native 全量回归：**734 passed、115 subtests passed、121 warnings，17.30 秒**，见 [服务器日志](coverage-evidence/evening-full-regression-20261011.txt)。显式启用 `CACHEPILOT_RUN_CUDA_CONTRACT=1` 和 `CACHEPILOT_REQUIRE_RESERVATION_NATIVE=1`，没有把跳过当通过。
- 本地 Windows 的全量 pytest 因缺少 `lmcache`/`msgspec` 在收集期报两个错误，保留 [本地日志](coverage-evidence/evening-local-regression-20261011.txt)。本地新增 runner/审计专项 26 项与源码校验 2 项通过，不能与服务器全量回归混为一项。
