# CachePilot

面向多轮 Agent 推理的 KV 缓存卸载策略探索。

当前状态：**已完成总体设计、GPU 环境搭建和 vLLM＋LMCache CPU KV 保存/回载的最小功能验证；尚未实现自定义策略或运行性能实验。**

2026-09-24：RTX 4090 24GB、Qwen3-4B、vLLM 0.30.0 和固定 LMCache 研究提交已通过编译模式下的四种功能验收：原生 vLLM、普通保存、FIFO、EVICTION_AWARE。源码版本未复现原 PyPI 0.5.5 的请求完成回调崩溃，压力卸载后可从 CPU 回载 1536 tokens。尚有停机后会话记录待回收的问题；不是完整稳定性或性能认证。详见 [验收结果与限制](docs/validation/2026-09-24/README.md)。

第一版研究：在 vLLM＋LMCache 的现有延迟卸载路径上，观察显存消耗与传输反馈，评估是否需要自适应卸载窗口和搬运预算。先验证瓶颈，再决定实现。

## 工作入口

- [总体设计与实施方案](docs/PROJECT_PLAN.md)
- [待办与阶段验收](docs/TASKS.md)
- [环境要求与租机清单](docs/ENVIRONMENT.md)
- [系统接入与基线启动说明](docs/INTEGRATION.md)
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
    sources/                 本轮兼容性文档快照
  configs/
    baseline-kv-transfer.json  待环境核验的基线配置
    environment-record.example.json
```

`scripts/` 已提供源码构建、服务启动和自动功能验收入口；`docs/validation/` 保存实测证据和环境包快照。自定义策略与性能 benchmark 尚未实现，当前没有 `pip install cachepilot` 或 `CACHEPILOT` 配置开关。
