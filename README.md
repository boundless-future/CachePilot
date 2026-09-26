# CachePilot

面向多轮 Agent 推理的 KV 缓存卸载策略探索。

当前状态：**已完成环境验收、固定轨迹回放、第一轮 24 组重复基线和自适应卸载窗口原型；原型已跑通但尚未证明收益。**

2026-09-26：分离策略与诊断日志后完成 12 组重复对比，默认/自适应压力 P95 均值为 3.686/3.694 秒，仍无收益证据。独立分配诊断确认异步回载的块分配与策略压力信号存在时间错位；下一步验证实际分配信号及提前需求预算。详见 [策略诊断报告](docs/experiments/2026-09-26/STRATEGY_DIAGNOSIS.md)。

2026-09-24：RTX 4090 24GB、Qwen3-4B、vLLM 0.30.0 和固定 LMCache 研究提交已通过编译模式下的四种最小功能验收。后续合成实验完成 768 个请求：2 GiB GPU KV 压力下，原生/立即卸载/默认延迟卸载的复用轮 TTFT P95 均值分别约 5.53/2.85/3.47 秒；能留在 GPU 的小工作集则原生更快。跨配置存在输出文本差异，不能据此宣称正确性通过或新策略已有收益。详见 [实验报告与限制](docs/experiments/2026-09-24/REPORT.md) 和 [环境验收](docs/validation/2026-09-24/README.md)。

第一版研究：在 vLLM＋LMCache 的现有延迟卸载路径上，观察显存消耗与传输反馈，评估是否需要自适应卸载窗口和搬运预算。先验证瓶颈，再决定实现。

## 工作入口

- [总体设计与实施方案](docs/PROJECT_PLAN.md)
- [待办与阶段验收](docs/TASKS.md)
- [环境要求与租机清单](docs/ENVIRONMENT.md)
- [系统接入与基线启动说明](docs/INTEGRATION.md)
- [回放语义、指标与运行入口](docs/REPLAY.md)
- [第一轮基线实验报告](docs/experiments/2026-09-24/REPORT.md)
- [重启复核与多轮 KV 回载诊断](docs/experiments/2026-09-26/README.md)
- [无日志策略对比与块分配诊断](docs/experiments/2026-09-26/STRATEGY_DIAGNOSIS.md)
- [实验环境记录模板](configs/environment-record.example.json)

完整调研及候选改进点的源码证据目前保存在本地工作区的同级 `survey/` 目录，未包含在本仓库中。

## 第一版范围

- 单机单 GPU，Qwen3-4B，BF16 权重/BF16 KV。
- vLLM 原生缓存、LMCache 默认卸载、调优固定卸载策略作为基线。
- CachePilot 策略运行于 Connector 的调度器侧；不新增 HTTP 推理服务。
- 复用 LMCache manager 的校验、pin/unpin、提交与完成处理；按需增加反馈信号。
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
