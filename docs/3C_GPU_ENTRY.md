# 3C 首个 GPU adapter 的实施顺序

这是 [总体计划](PROJECT_PLAN.md) 的具体开工顺序；准入证据与限制见 [实际服务所有权报告](experiments/2026-10-01/OWNED_SERVICE.md)。此文不表示 GPU 策略已经实现或已有性能收益。

## 固定范围

沿用 RTX 4090 24GB、Qwen3-4B、BF16、TP=1、单 full-attention group、16-token block / 256-token chunk 和固定 LMCache/vLLM 版本。第一轮使用 eager、固定请求轨迹和隔离服务实例；缺 terminal 或消费者不终结时保留资源并停止该实例的实验，按完整实例重建处理。

2026-10-01 的受控准入已通过，见 [验收矩阵](experiments/2026-10-01/VALIDATION_MATRIX.md)。这次明确把隔离研究实验与完整生产恢复分开：后者仍未通过。30 秒回执延迟 r6 的空闲 owner 收尾没有修复，5 秒 r8/r9 的成功不能覆盖它；第一步不用人工 stream hold/回执延迟，且每轮检查 job/controller/transfer/pin、GPU registration 以及 BlockPool。任一不闭合即停止该轮，不以关闭时回收掩盖运行期残留。

策略配置独立可关闭。保护预算为零必须回到同一所有权适配栈上的默认 EVICTION_AWARE 路径；比较新旧策略时两边使用相同的数据通路，避免把所有权修补的开销或效果算到策略贡献里。原生栈作为另一个基线保留。

## 第一步：零保护预算与真实交接（2026-10-09 已通过）

`ProtectionConnector` 已接入真实 metadata/receipt envelope。默认、关闭、零预算三组各 96 请求通过，14 个 STORE 批次的范围与次数一致；各 216 层回载 KV bitwise 一致、909 free 恢复，运行期 owner/job 归零。完整 CUDA 回归 460 passed、115 subtests。见 [验收报告](experiments/2026-10-09/PROTECTION_ZERO.md)。这一步没有保护 pin，当时非零预算拒绝；非零保护后续进展见第二步。

实现外部 `ProtectionConnector` 的 scheduler / worker 交接框架，先不开启保护。复用 `protection_state_machine.py`、`chunk_protection_model.py` 的语义，以及 `token_prefix_proof.py` 和 `cpu_store_metadata_bridge.py` 已验证的字段约定。

`vllm_metadata_pool.py` 只是 CPU harness，不能直接安装到 allocator callback；真实 adapter 必须在 scheduler owner 串行维护候选快照、hash 复核、pin 与随后的分配。正式模块应清楚区分 PREPARED、已交给 worker、实际终结三种状态。

交付：独立配置开关、真实 metadata/receipt 账本、零保护预算的默认行为对照。此时保护 pin 和额外 STORE 数都应为零；默认 STORE 范围、失败语义和资源终态保持可解释。

## 第二步：小预算实际保护

2026-10-11 已完成保护窗口内的无共享及共享前缀 victim 自然抢占，以及构造错误代际和原批次重复回执的 GPU 拒绝；见 [无共享抢占](experiments/2026-10-11/PROTECTED_PREEMPTION.md)、[共享抢占](experiments/2026-10-11/SHARED_PREEMPTION.md)和[回执身份](experiments/2026-10-11/RECEIPT_IDENTITY.md)。同日还完成了真实 HTTP raw ID 复用后的两代隔离：默认 ID 随机化关闭仅用于诊断，旧回执不能破坏新代 pending prefix，两个回执语义均通过，见 [raw ID 复用](experiments/2026-10-11/RAW_ID_REUSE.md)。真实执行故障、空闲延迟与较长前缀仍开放，第三步尚未开始。

同日一个 chunk 实际保护对照通过：七个新增保护 STORE、峰值 16 block，六批/216 层严格来源，运行期资源闭合；见 [单 chunk 报告](experiments/2026-10-11/PROTECTION_ONE_CHUNK.md)。

在途预算耗尽时另一个候选零 pin 回退，之后由默认 manager 保存；原成功回执释放预算，864 层 KV 全等、资源闭合，见 [在途预算报告](experiments/2026-10-11/INFLIGHT_BUDGET.md)。物理容量不足专项在 1.25 GiB KV 池中也通过：预算足够、无在途 pin，但 free=439 小于 reserve+required=452；候选随后默认保存，936 层 KV 全等且资源闭合，见 [容量报告](experiments/2026-10-11/CAPACITY_FALLBACK.md)。

2026-10-10 新增保护回执诊断、整项超预算回退及共享受保护前缀的新请求取消验收。取消 r3 同组 32 block 引用 2→1→0，原批次两个 chunk/72 层严格来源、全轮 936 层 KV 全等，资源闭合。r1/r2 实验流程失败已保留，见 [共享取消报告](experiments/2026-10-10/PROTECTION_SHARED_CANCEL.md)。

2026-10-09 正常路径首轮通过：两组各 348 请求，保护组 9 个保护 STORE、峰值 32 block、19 次零步回滚，864 层 KV 全等；其中 7 批/14 chunk/504 层证明保护来源且无中间 STORE。运行期资源清零、909 free 恢复。首版仅支持已结束且无外部引用、start=0/end=256 或 512 的整项，同步模型调度。新增零步回滚刷新 LRU 的 r1 问题已修，r2 因目标仍驻 GPU 未覆盖 H2D 而失败，均保留于 [报告](experiments/2026-10-09/PROTECTION_TWO.md)。本步的 GPU 取消/失败/延迟专项和较大前缀仍待验证，第三步尚未开始。

先只选完整、连续前缀 chunk；物理 block 身份、vLLM hash、LMCache token/chunk hash 都要在 pin 前复核。已保存前缀、运行请求外部引用和在途 STORE 不能混用。首轮从最多两个 chunk 的额外保护预算开始，使用明确的 free-block 余量和预算回退。

准入前需求观测先作为账本输入：此前 `compute_slots` 与 lookup 状态信号均有反例，不能把它们当正确需求 oracle。若无法证明本步可执行，先不保护；若 pin 后出现零 token 步，则仅回滚未交接的 PREPARED 批次。不能在 allocator 已弹出候选后再 pin。

metadata 只能提交一次；默认 manager drain 必须排除同批 action。同 raw request-id 跨代最多一批 STORE 在途，旧回执仅终结旧批次。取消/抢占不提前 unpin 已交给 worker 的批次；失败不推进已保存前缀。

交付：一次真实 STORE、原批次回执、GPU pin/unpin 和最终 BlockPool 闭合，随后完成零步、取消、自然抢占、保存失败、身份复用和预算不足的专项。先验证实际 KV 内容与恢复输出，再谈性能。

## 第三步：可回滚消融

同版本、同轨迹、同 GPU/CPU KV 预算下对比零保护预算、少量固定预算、默认卸载、调优固定策略及立即卸载。先跑一个可解释的压力点；正确性通过后关闭逐步诊断日志，每个关键配置至少三次重复。

报告实际 prefill、TTFT P95、排队、H2D/D2H、保护峰值、在途峰值、回退频率和策略 CPU 开销。若保护增加搬运、降低可分配容量或没有收益，保留退化结果，缩小策略或停止该分支；不能仅凭 STORE 变多或丢弃变少宣布优化成功。

## 仍单列的工作

真实驱动故障、永久 I/O 阻塞后的自动恢复、多 GPU、动态存储管理、异步 reset API 及 compile-only 输出差异根因继续作为独立限制/支线。它们不能被计为已通过，也不能代替上述 3C 自身新增路径的验收。
