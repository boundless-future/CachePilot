# CachePilot 工程结构与模块边界

2026-10-13 工程收敛后，稳定主线的唯一实现位于 `src/cachepilot/`。
`scripts/` 中同名文件只保留导入/启动兼容入口；历史实验、故障注入及审计脚本继续留在原处，以维护已归档证据的原始解释。
逐文件迁移映射见 [module-migration.json](module-migration.json)。

运行时决策进一步拆分：`runtime/candidates.py`只过滤并排序候选，
`runtime/admission.py`只计算保守reserve、完整chunk预算与容量判定。
保护生命周期调用它们后才做证明、pin及交接；两个决策模块不导入上游栈。
当前共享保护仍继承LMCache generation桥，完整依赖反转尚未完成。
具体边界及验证见 [准入收敛报告](experiments/2026-10-13/RUNTIME_ADMISSION_REVIEW.md)。

```text
CachePilot/
├── pyproject.toml                 安装与 CLI 元数据，不安装或升级上游栈
├── src/cachepilot/
│   ├── cli.py                     config / doctor / manifest / server
│   ├── config.py                  显式实验接线参数，默认零保护
│   ├── provenance.py              新实验的统一源码快照
│   ├── native.py                  单独编译的 reservation 扩展路径
│   ├── runtime/                   pin、前缀证明、generation、idle 状态机
│   ├── integration/lmcache/       MP 协议、服务所有权、manager、RPC 与保护桥
│   ├── integration/vllm/          Connector 继承链与 scheduler/worker 接入
│   └── telemetry/                 服务端 generation 观察器
├── scripts/                       兼容入口、研究探针、实验与审计工具
├── native/                        原生 reservation 源码
├── configs/                       历史及基线配置
├── tests/
│   └── architecture/              包边界、配置、入口同一性、冻结源契约
├── examples/shared-prefix/        手工接线示例
└── docs/                        设计、待办、实验及原始证据导航
```

运行时请求从应用进入 vLLM HTTP/API → scheduler 的
`SharedPrefixConnector` → generation/protection/idle 接口 → LMCache MP
的 client、manager 与独立服务 → 原始 STORE/RETRIEVE 回执 → scheduler
完成资源释放。`runtime` 中的前缀证明、预算及 generation ledger 维护状态；
`integration` 负责在固定上游版本的实际边界调用它们；`telemetry` 只观察服务状态。
CLI 的 `doctor` 与 `config` 不加载 GPU 栈。接线命令见
[示例](../examples/shared-prefix/README.md)。

当前 Connector 继承链仍为
`OwnedMP → OwnedService → Protection → IdleFinalize → GenerationLedger → GenerationProtection → SharedPrefix`。
这次只统一源码位置，没有改保护预算、reserve、pin、回执、generation 或失败处理语义。
`runtime` 与 `integration/lmcache` 中已有相互引用，说明它们还不是严格的依赖倒置架构；
后续应按接口与数据所有权逐个拆分，不把目录名字当成解耦已经完成。

推荐按下列顺序逐模块推进，每项都先补该模块可观察契约，再改实现、跑相关测试，最后决定是否需要GPU准入：

1. `provenance`、配置和 CLI：保证历史与新实验可区分、运行入口可复现。
2. `runtime/prefix_proof`、`runtime/protection`、`runtime/shared_prefix`：候选识别、容量预算、共享引用与回滚。
3. `integration/lmcache/service`、`guard`、`protocol`：所有权、原生终结、source hash与服务资源审计。
4. `runtime/generation` 与 `integration/lmcache/{manager,client,protection}`：回执、END、RPC和跨代身份。
5. `integration/vllm`：保持显式opt-in、TP=1、同步调度限定，逐个Connector验证。
6. `telemetry` 与实验harness：只读观测和冻结源审计；GPU性能必须在正确性准入通过后评估。

每次改动以 `src/cachepilot` 为准；旧同名脚本只能维持导入兼容，不允许在其中添加新的主线逻辑。
实验脚本的 `source_sha` 现覆盖 `scripts/`、`configs/`、`src/cachepilot/`、
`native/` 与 `pyproject.toml`；历史manifest按旧字节继续归档，新的formal不能复用旧签名。
原始失败、证据SHA和历史报告不回写。

本阶段剩余工作：将218个混排的历史脚本按“实验入口/审计/故障探针/构建工具”分批分类或迁入研究目录；
在保持旧路径可用的前提下逐项执行，不一次性搬迁全部脚本。策略层的独立接口、真实多worker/异步支持、联合异常与完整抢占恢复仍是开放任务，工程重构不代表这些已完成。
