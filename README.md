# CachePilot

面向多轮 Agent 推理的 KV 缓存卸载策略探索。

当前状态：**已完成环境验收、固定轨迹回放、第一轮 24 组重复基线和自适应卸载窗口原型；原型已跑通但尚未证明收益。**

2026-10-01：**可以开始阶段 3C 的受控单卡 GPU 接入。** 原 token/pin、实际 L2 controller、带原身份的 native terminal 与心跳失联回收已接入 opt-in MP 服务；八个受控场景按 r2/r3 通过，自然抢占两轮各 432 层回载 KV bitwise 一致，最终 CUDA 回归 438 passed、115 subtests。准入限定 eager、TP=1、单 full-attention、隔离实例和逐轮资源审计；30 秒回执延迟的空闲 owner 收尾与生产故障恢复仍未完成。GPU 保护策略尚未实现，下一步从零保护预算的 ProtectionConnector 交接开始。见 [当前验收矩阵](docs/experiments/2026-10-01/VALIDATION_MATRIX.md)、[实际服务报告](docs/experiments/2026-10-01/OWNED_SERVICE.md) 和 [3C 开工顺序](docs/3C_GPU_ENTRY.md)。

2026-09-30：阶段3B已补真实FS L2取消/短读、L2部分写入、自然容量抢占、迟到STORE回执及session TTL；无END客户端死亡仍残留prefetch job/result，整体资源门槛未通过。阶段3C已完成保护状态机、真实BlockPool/hash与STORE metadata的CPU契约，尚未接入GPU策略。见 [验收矩阵与下一步](docs/experiments/2026-09-30/VALIDATION_MATRIX.md)。

2026-09-27检查点：当时处于阶段3B生命周期验证。已完成契约测试、客户端断流、诊断等待期取消、受控显式抢占恢复，以及真实回载/STORE 完成后的模拟失败回执诊断。显式抢占的同步调度对照两次通过，默认异步调度下 reset API 返回 500，但生成和 block 清理通过。真实远端回载中止、实际 worker STORE 失败与在途抢占仍需补齐，当时最终保护策略尚未开始；9月30日CPU契约进展见上文。见 [抢占实验与边界](docs/experiments/2026-09-27/EXPLICIT_PREEMPTION.md) 和 [STORE 失败回执诊断](docs/experiments/2026-09-27/STORE_FAILURE.md)。

2026-09-26：分离策略与诊断日志后完成 12 组重复对比，默认/自适应压力 P95 均值为 3.686/3.694 秒，仍无收益证据。独立分配诊断确认异步回载的块分配与策略压力信号存在时间错位；下一步验证实际分配信号及提前需求预算。详见 [策略诊断报告](docs/experiments/2026-09-26/STRATEGY_DIAGNOSIS.md)。

后续已实现真实分配计数修正，完成三策略 18 组重复对比：压力 P95 默认/修正/立即卸载为 3.624/3.522/2.950 秒。修正相对默认改善 2.82%、实际 prefill 减少 6.21%，但增加搬运且仍慢于立即卸载；这是单一合成压力点的局部结果。见 [分配信号消融报告](docs/experiments/2026-09-26/ALLOCATION_SIGNAL.md) 与 [前移决策设计](docs/experiments/2026-09-26/PREALLOCATION_DESIGN.md)。

2026-09-24：RTX 4090 24GB、Qwen3-4B、vLLM 0.30.0 和固定 LMCache 研究提交已通过编译模式下的四种最小功能验收。后续合成实验完成 768 个请求：2 GiB GPU KV 压力下，原生/立即卸载/默认延迟卸载的复用轮 TTFT P95 均值分别约 5.53/2.85/3.47 秒；能留在 GPU 的小工作集则原生更快。跨配置存在输出文本差异，不能据此宣称正确性通过或新策略已有收益。详见 [实验报告与限制](docs/experiments/2026-09-24/REPORT.md) 和 [环境验收](docs/validation/2026-09-24/README.md)。

第一版研究：在 vLLM＋LMCache 的现有延迟卸载路径上，观察显存消耗与传输反馈，评估是否需要自适应卸载窗口和搬运预算。先验证瓶颈，再决定实现。

## 工作入口

- [总体设计与实施方案](docs/PROJECT_PLAN.md)
- [待办与阶段验收](docs/TASKS.md)
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
