# CachePilot

面向多轮 Agent 推理的 KV 缓存卸载策略探索。

当前状态（2026-10-11 晚间）：**阶段 3C 的受控单卡保护已实现；阶段 4 已完成容量、六点固定参数调优、原生/立即卸载/固定策略和压缩到达对照。保护尚未证明优于调优固定策略；新文本零覆盖已独立观测，当前不宣称通用加速。** 最新结论、实现边界和下一步见 [当前状态](docs/STATUS.md)；持续执行步骤见 [晚间记录](docs/experiments/2026-10-11/EVENING_PROGRESS.md)。

第一次阅读建议先看 [当前实现与代码导航](docs/IMPLEMENTATION_MAP.md)，再用 [离线复核说明](docs/experiments/2026-10-11/REPRODUCE_EVENING.md) 查看已归档结果；复核这些结果不需要 GPU。

2026-10-12 更新：长 prefill 的代际缺口已通过独立 opt-in 账本接线和 GPU 资源审计；原失败保持失败。关闭诊断的新四配置 pilot 4/4、formal 12/12 通过，正式816请求；长 prefill 下立即卸载/default/selected 的 TTFT P95 相对 native 为 -26.84%/-19.07%/-7.78%，只代表整套零保护配置。见 [长 prefill 正式报告](docs/experiments/2026-10-12/GENERATION_LONG_PREFILL.md)。原decode192 pilot在立即卸载session残留处停止；只读复现定位END后末批STORE重建session。独立END-after-original-receipt候选GPU诊断通过，新四配置pilot 4/4通过，272请求/52,224输出tokens；服务器、本地和冻结源复算一致，同签名formal十二格完成，816请求/156,672输出tokens，native TTFT P95最低，缓存配置相对为+2.29%至+5.85%，旧失败保留。见 [decode正式报告](docs/experiments/2026-10-12/DECODE_RECEIPT_FORMAL.md)。见 [generation 证据](docs/experiments/2026-10-12/GENERATION_BRIDGE.md) 和 [今日推进记录](docs/experiments/2026-10-12/PROGRESS.md)。新连接器暂仅支持零保护，不能据此扩大非零保护验收范围。 六session真实匿名结构的观测准入和无采样pilot各4/4完成，实际高占用/抢占及资源证据见 [结构GPU报告](docs/experiments/2026-10-12/REAL_STRUCTURE_GPU.md)；正式三重复待下一窗口。

## 历史检查点

以下按各次实验保留原始结论，其中“尚未完成/下一步”描述的是当时状态；当前待办以 [TASKS](docs/TASKS.md) 为准。失败轮和不支持的边界不会被后来局部成功覆盖。

2026-10-11 最新：**保护预算首轮性能消融完成。** 在 4090 24GB、Qwen3-4B、2 GiB GPU KV 的固定两阶段压力 trace 上，zero/one/two chunk 各三次且九个 cell 都通过完整响应、H2D、资源闭合和服务日志门槛。one chunk 的 TTFT P95 均值相对 zero 低 6.22%，two chunk 低 3.45%；两者不是统计显著性或通用加速结论，输出哈希也仍有跨组差异。原始证据、完整指标和限制见 [正式消融报告](docs/experiments/2026-10-11/PROTECTION_ABLATION.md)。

2026-10-11 最新：**长 pending STORE 的前缀拆分通过。** 对 `[0,1024)` 操作，CachePilot 先保护 `[0,512)`，成功 receipt 后交由默认 manager 提交 `[512,1024)`；前缀失败时 suffix 被丢弃，以维持 prefix closure 和 policy ledger。4090 上的成功路径完成 348 请求、1800/1800 层回载 KV bitwise 一致；真实 CUDA copy enqueue 后的受控 server 失败路径也恢复 909 free block 且 1656/1656 层 KV 一致。该结果扩大了正确性范围，尚不构成性能收益结论。见 [报告](docs/experiments/2026-10-11/PROTECTION_LONG_PREFIX.md)。

2026-10-11 补充：**3C 的在途预算耗尽与物理容量不足两种 GPU 回退均通过。** 前者验证另一个合格候选在原保护回执未交付时零 pin 回退、随后默认保存；后者在 1.25 GiB KV 池中观测 free=439、小于 reserve+required=452，预算仍足够且无在途 pin，同一候选随后由默认 manager 保存。物理容量轮 347 请求、936 层 KV 全等，14 chunk/504 层严格保护来源，完整 CUDA 回归 585 passed、115 subtests。仍未进入性能消融。见 [在途预算](docs/experiments/2026-10-11/INFLIGHT_BUDGET.md) 和 [物理容量](docs/experiments/2026-10-11/CAPACITY_FALLBACK.md)。

2026-10-11 最新：**真实 HTTP raw request ID 复用后的跨代回执隔离通过。** 在关闭 vLLM 默认 request-ID 随机化的隔离诊断中，同一个 API ID 的两代真实请求拥有不同 generation wire identity；旧代回执不会破坏新代 pending prefix，四种旧 envelope 均被拒绝。正常旧回执与“实际 STORE 成功、但报告失败”的回执各通过一轮，第二代两个 chunk 均实际回载且 72 层 KV bitwise 全等。该专项不代表真实 STORE/DMA 故障，也没有性能收益结论。见 [raw ID 复用报告](docs/experiments/2026-10-11/RAW_ID_REUSE.md)。

2026-10-11 最新：**保护 STORE 的 server 执行失败路径通过。** 一个受保护 STORE 在真实 CUDA copy enqueue 后触发受控 server host exception；LMCache 内层向未重写的 worker future 交付原始 `false`，native terminal、32 个 pin 释放及 worker/protection/scheduler 三层失败回执均闭合。348 请求中 864 层 KV 回载 bitwise 全等、26 个 terminal 和 909 free block 均闭合。该专项不是 CUDA driver fault、DMA/PCIe 中断或自然硬件故障，仍无性能收益结论。见 [server STORE 故障报告](docs/experiments/2026-10-11/PROTECTION_SERVER_STORE_FAULT.md)。

2026-10-11 最新：**阶段 3C 的保护窗口自然抢占及 GPU 回执身份专项通过。** 共享前缀请求被真实容量压力抢占时，32 个保护 block 引用 2→1→0，39 次分配未复用，504 层回载 KV 全等且资源闭合；错误代际与重复 envelope 在真实 scheduler 被拒绝。实际 raw ID 复用和真实执行故障仍开放，尚无新策略性能收益结论。见 [共享抢占](docs/experiments/2026-10-11/SHARED_PREEMPTION.md)、[无共享抢占](docs/experiments/2026-10-11/PROTECTED_PREEMPTION.md)和[回执身份](docs/experiments/2026-10-11/RECEIPT_IDENTITY.md)。

同日，一个 chunk 的真实 GPU 保护对照也通过：单 chunk 组七个保护 STORE、峰值 16 block，六批/216 层有严格来源，输入输出与零预算对照一致，资源闭合。见 [单 chunk 报告](docs/experiments/2026-10-11/PROTECTION_ONE_CHUNK.md)。

2026-10-10 最新：**共享受保护 GPU 前缀的新请求取消专项通过。** 同一组 32 个 block 引用按 2→1→0 变化，原回执恰好释放保护引用；347 个完整响应和 1 个取消请求，936 层回载 KV 全等，原批次两个 chunk/72 层有严格来源证明，资源清零、909 free 恢复。完整 CUDA 回归 525 passed、115 subtests。两轮实验流程失败保留；下一步为自然抢占和重复/跨代回执，仍无性能收益结论。见 [共享取消报告](docs/experiments/2026-10-10/PROTECTION_SHARED_CANCEL.md)。

2026-10-10 后续：**整项超预算回退 GPU 对照通过。** 零预算/一个 chunk 各 348 请求，输入/输出及 14 个默认 STORE 范围一致，各 720 层 KV 全等，资源归零。完整 CUDA 回归 506 passed、115 subtests。仍未进入性能消融，下一步为共享前缀新请求取消/抢占和回执身份专项。见 [预算回退报告](docs/experiments/2026-10-10/PROTECTION_BUDGET.md)。

2026-10-10：**新增保护 STORE 的回执专项已完成首轮。** 真实 STORE 成功后延迟 5 秒再交付回执，继续产生固定恢复流量时 508 请求、720 层 KV 全等并闭合；空闲等待仍留下 pending owner，失败轮保留。失败回执注入验证 worker/scheduler 状态一致、pin 正确释放。两种注入都不模拟真实 DMA 中断或 STORE 执行失败，下一步为新增保护路径的取消、自然抢占、重复/跨代回执和预算回退 GPU 验证。见 [保护回执专项](docs/experiments/2026-10-10/PROTECTED_RECEIPTS.md)。

2026-10-09 后续：**阶段 3C 两 chunk 实际保护正常路径通过。** 已接入真实 scheduler 分配前 pin、零步回滚、默认 manager 防重复 STORE 及原回执释放。零预算/两 chunk 各 348 请求输出一致，保护组 9 个保护 STORE、峰值 32 block、864 层回载 KV 全等，运行期资源清零。新增零步回滚刷新 LRU 的问题已修复，失败轮保留。当前仅支持已结束的短前缀；下一步为新增路径的 GPU 取消/失败/延迟专项，再做消融，尚无性能收益结论。见 [两 chunk 报告](docs/experiments/2026-10-09/PROTECTION_TWO.md)。

2026-10-09：**阶段 3C 零预算真实 GPU 交接通过。** `ProtectionConnector` 默认对照、保护关闭、零预算三组各 96 请求，14 个 STORE 批次的范围/次数与输出一致；每组 216 层回载 KV 全等、909 free 恢复，完整 CUDA 回归 460 passed、115 subtests。未新增保护 pin/STORE，非零预算当前拒绝；下一步实现最多两个 chunk 的实际保护。本轮使用固定短尾流量，不代表长延迟空闲收尾已修复，也没有性能收益结论。见 [零预算报告](docs/experiments/2026-10-09/PROTECTION_ZERO.md)。

2026-10-01：**可以开始阶段 3C 的受控单卡 GPU 接入。** 原 token/pin、实际 L2 controller、带原身份的 native terminal 与心跳失联回收已接入 opt-in MP 服务；八个受控场景按 r2/r3 通过，自然抢占两轮各 432 层回载 KV bitwise 一致，最终 CUDA 回归 438 passed、115 subtests。准入限定 eager、TP=1、单 full-attention、隔离实例和逐轮资源审计；30 秒回执延迟的空闲 owner 收尾与生产故障恢复仍未完成。GPU 保护策略尚未实现，下一步从零保护预算的 ProtectionConnector 交接开始。见 [当前验收矩阵](docs/experiments/2026-10-01/VALIDATION_MATRIX.md)、[实际服务报告](docs/experiments/2026-10-01/OWNED_SERVICE.md) 和 [3C 开工顺序](docs/3C_GPU_ENTRY.md)。

2026-09-30：阶段3B已补真实FS L2取消/短读、L2部分写入、自然容量抢占、迟到STORE回执及session TTL；无END客户端死亡仍残留prefetch job/result，整体资源门槛未通过。阶段3C已完成保护状态机、真实BlockPool/hash与STORE metadata的CPU契约，尚未接入GPU策略。见 [验收矩阵与下一步](docs/experiments/2026-09-30/VALIDATION_MATRIX.md)。

2026-09-27检查点：当时处于阶段3B生命周期验证。已完成契约测试、客户端断流、诊断等待期取消、受控显式抢占恢复，以及真实回载/STORE 完成后的模拟失败回执诊断。显式抢占的同步调度对照两次通过，默认异步调度下 reset API 返回 500，但生成和 block 清理通过。真实远端回载中止、实际 worker STORE 失败与在途抢占仍需补齐，当时最终保护策略尚未开始；9月30日CPU契约进展见上文。见 [抢占实验与边界](docs/experiments/2026-09-27/EXPLICIT_PREEMPTION.md) 和 [STORE 失败回执诊断](docs/experiments/2026-09-27/STORE_FAILURE.md)。

2026-09-26：分离策略与诊断日志后完成 12 组重复对比，默认/自适应压力 P95 均值为 3.686/3.694 秒，仍无收益证据。独立分配诊断确认异步回载的块分配与策略压力信号存在时间错位；下一步验证实际分配信号及提前需求预算。详见 [策略诊断报告](docs/experiments/2026-09-26/STRATEGY_DIAGNOSIS.md)。

后续已实现真实分配计数修正，完成三策略 18 组重复对比：压力 P95 默认/修正/立即卸载为 3.624/3.522/2.950 秒。修正相对默认改善 2.82%、实际 prefill 减少 6.21%，但增加搬运且仍慢于立即卸载；这是单一合成压力点的局部结果。见 [分配信号消融报告](docs/experiments/2026-09-26/ALLOCATION_SIGNAL.md) 与 [前移决策设计](docs/experiments/2026-09-26/PREALLOCATION_DESIGN.md)。

2026-09-24：RTX 4090 24GB、Qwen3-4B、vLLM 0.30.0 和固定 LMCache 研究提交已通过编译模式下的四种最小功能验收。后续合成实验完成 768 个请求：2 GiB GPU KV 压力下，原生/立即卸载/默认延迟卸载的复用轮 TTFT P95 均值分别约 5.53/2.85/3.47 秒；能留在 GPU 的小工作集则原生更快。跨配置存在输出文本差异，不能据此宣称正确性通过或新策略已有收益。详见 [实验报告与限制](docs/experiments/2026-09-24/REPORT.md) 和 [环境验收](docs/validation/2026-09-24/README.md)。

第一版研究：在 vLLM＋LMCache 的现有延迟卸载路径上，观察显存消耗与传输反馈，评估是否需要自适应卸载窗口和搬运预算。先验证瓶颈，再决定实现。

## 工作入口

- [当前状态、证据与开放边界](docs/STATUS.md)
- [晚间持续推进步骤记录](docs/experiments/2026-10-11/EVENING_PROGRESS.md)
- [固定参数调优与新文本验证预注册](docs/experiments/2026-10-11/FIXED_BASELINE_PREREGISTRATION.md)
- [3C 物理容量回退](docs/experiments/2026-10-11/CAPACITY_FALLBACK.md)
- [3C 保护预算正式性能消融](docs/experiments/2026-10-11/PROTECTION_ABLATION.md)
- [3C 在途预算耗尽](docs/experiments/2026-10-11/INFLIGHT_BUDGET.md)
- [3C 实际 raw ID 复用与跨代回执隔离](docs/experiments/2026-10-11/RAW_ID_REUSE.md)
- [3C 保护 STORE server 执行故障](docs/experiments/2026-10-11/PROTECTION_SERVER_STORE_FAULT.md)
- [总体设计与实施方案](docs/PROJECT_PLAN.md)
- [待办与阶段验收](docs/TASKS.md)
- [3C 共享前缀自然抢占验收](docs/experiments/2026-10-11/SHARED_PREEMPTION.md)
- [3C 共享受保护前缀取消验收与失败轮](docs/experiments/2026-10-10/PROTECTION_SHARED_CANCEL.md)
- [3C 两 chunk 实际保护验收与失败轮](docs/experiments/2026-10-09/PROTECTION_TWO.md)
- [3C 保护 STORE 回执专项](docs/experiments/2026-10-10/PROTECTED_RECEIPTS.md)
- [3C 零预算 GPU 交接验收](docs/experiments/2026-10-09/PROTECTION_ZERO.md)
- [当前3B/3C验收矩阵](docs/experiments/2026-10-01/VALIDATION_MATRIX.md)
- [3C GPU 接入顺序与限定范围](docs/3C_GPU_ENTRY.md)
- [环境要求与租机清单](docs/ENVIRONMENT.md)
- [系统接入与基线启动说明](docs/INTEGRATION.md)
- [回放语义、指标与运行入口](docs/REPLAY.md)
- [第一轮基线实验报告](docs/experiments/2026-09-24/REPORT.md)
- [重启复核与多轮 KV 回载诊断](docs/experiments/2026-09-26/README.md)
- [无日志策略对比与块分配诊断](docs/experiments/2026-09-26/STRATEGY_DIAGNOSIS.md)
- [实际分配信号消融与下一步决定](docs/experiments/2026-09-26/ALLOCATION_SIGNAL.md)
- [实验环境记录模板](configs/environment-record.example.json)

完整调研及候选改进点的源码证据目前保存在本地工作区的同级 `survey/` 目录，未包含在本仓库中。

## 第一版范围

- 单机单 GPU，Qwen3-4B，BF16 权重/BF16 KV。
- vLLM 原生缓存、LMCache 默认卸载、调优固定卸载策略作为基线。
- CachePilot 策略运行于 Connector 的调度器侧；不新增 HTTP 推理服务。
- 复用 LMCache manager 的校验、pin/unpin、提交与完成处理；固定版研究所有权适配器作为独立前置支线，不计为策略收益。
- 不同时开发跨节点路由、量化、PD 分离、复杂预测模型或完整 Agent 平台。

现有策略已经包括压力感知，项目不把“实现延迟卸载”作为新贡献，也不预先承诺性能提升。

## 目录

```text
CachePilot/
  README.md
  docs/
    TASKS.md
    ENVIRONMENT.md
    INTEGRATION.md
    REPLAY.md
    experiments/             基线摘要、图表与结果限制
    validation/              功能验收和环境包快照
    sources/                 本轮兼容性文档快照
  scripts/                   构建、启动、回放、分析与回归
  tests/                     不依赖 GPU 的回放测试
  configs/
    baseline-kv-transfer.json  固定源码上已实测的基线配置
    baseline-experiment.json   初始实验清单
    environment-record.example.json
```

`scripts/` 已提供源码构建、服务启动、自动功能验收和可复现回放入口。原始日志/trace 归档留在 `artifacts/`，不提交模型或大体积输出。自适应 horizon 目前是外部 Connector 实验原型，尚未证明收益；当前没有 `pip install cachepilot` 或 `CACHEPILOT` 配置开关。
