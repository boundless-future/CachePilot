# CachePilot 当前状态

更新：2026-10-11 晚间。逐步执行记录见 [EVENING_PROGRESS](experiments/2026-10-11/EVENING_PROGRESS.md)，完整阶段与未完成项见 [TASKS](TASKS.md)。本页区分实现、正确性范围和性能证据，历史日期的“下一步”不能代替当前计划。

## 项目定位与实现

CachePilot 是 vLLM＋LMCache 的外部实验 Connector 和小预算 KV 卸载策略：在真实 scheduler 的分配前，复核即将面临驱逐的已结束请求前缀，最多保护一个或两个完整 chunk；由原 manager 提交 STORE，并在原身份回执后释放保护 pin。单个物理 block 为 16 tokens，一个 LMCache chunk 为 256 tokens。它不是新推理引擎，也没有重新实现 GPU copy kernel。

原 token/reservation、请求 generation、native terminal、取消和失联回收构成单独的所有权适配支线。性能对照共用该数据通路，不能把它的修复算作保护策略收益。空闲 pending STORE 的 15 秒宽限收尾是另一默认关闭的候选机制；立即丢弃导致 H2D=0 的反例保留。

## 当前阶段

| 阶段 | 已完成 | 仍开放 |
|---|---|---|
| P0/P1 环境与接入 | 4090 24GB、Qwen3-4B/BF16、vLLM 0.30.0、锁定 LMCache 研究版、CUDA 13.0，实际 D2H/H2D 验收 | 第二模型/硬件控制实验 |
| P2/3A 观测与候选 | 固定 trace、基线 profiling、实际分配计数与 lookup 时序诊断；自适应 horizon 未证明收益 | 固定参数系统调优、更多到达分布、耗时分解 |
| 3B 所有权支线 | 受控单卡服务链路、原身份 native terminal、L2/取消/失联和资源审计；满足限定 3C 准入 | 真实 driver/DMA 故障、永久 I/O 自动恢复、多 worker/异步范围 |
| 3C 保护策略 | 零/一/两 chunk、预算与物理容量回退、共享取消/抢占、raw ID 跨代、长前缀拆分和受控 host exception；首轮正式消融 | 生产级全面正确性、可泛化收益结论 |
| P4 复核 | 新文本验证、1.5 GiB 容量重复、2.5 GiB 残留诊断、空闲收尾候选与组合专项 | 新栈 2.5 GiB formal 在运行；随后固定参数调优与新文本验证、立即卸载与新到达模式 |
| P5 展示/上游 | 原始证据持续归档 | 最终复现入口、整体图表、最小可维护上游补丁评估 |

## 能支持的结论

| 对照 | 当前观察 | 解释边界 |
|---|---|---|
| 2 GiB 开发 trace，zero/one/two 各三次 | one/two 的 TTFT P95 均值相对 zero 为 -6.22%/-3.45% | [首轮消融](experiments/2026-10-11/PROTECTION_ABLATION.md)；n=3，未证明稳定通用收益 |
| 相同到达结构、新文本验证，各三次 | -0.57%/+0.31%，未稳定复现改善 | [独立输入](experiments/2026-10-11/PROTECTION_ABLATION_VALIDATION.md)；不是新分布或真实 Agent 泛化 |
| 1.5 GiB 容量，各三次 | -5.09%/-3.99%；H2D 增加 8.92%/6.49% | [容量扫描](experiments/2026-10-11/CAPACITY_SCAN.md)；搬运代价与波动一并报告 |
| 原 2.5 GiB 试点 | zero 因 owned job/session 残留失败，后续格未运行 | 原失败永久保留；独立诊断定位空闲未提交 STORE |
| 新栈 2.5 GiB 试点 | zero/one/two 三格均通过，formal 正在运行 | [预注册](experiments/2026-10-11/IDLE_CAPACITY_PREREGISTRATION.md)；pilot 不进入正式均值 |

各性能组仍存在输出 hash 差异；固定输入、48 decode token、HTTP 200 或资源闭合不等于输出完全确定。独立诊断曾验证多组回载 KV bitwise 全等，但不能代替所有性能请求的逐层正确性证明。编译相关输出差异根因仍开放。

## 晚间新增正确性证据

- 空闲收尾后相同 raw ID 的 68 对 generation：旧 reclaim 均早于新 lookup；包括实际 idle-dropped 请求重入。只覆盖旧代完整结束后的复用。
- 失败回执与共享取消组合分别通过 864/936 层 KV 比较和资源审计，但 idle drop=0，只算兼容性。
- 延迟回执组合有实际 idle drop 且资源回收，但恢复命中仍在 GPU、H2D 未覆盖，整体失败。详见 [组合矩阵](experiments/2026-10-11/IDLE_RECEIPT_MATRIX.md)。
- 最近完整显式 CUDA 回归为 703 passed、115 subtests；随后新增 runner 的 CPU 专项单独记录，不伪称重新执行了完整 GPU 回归。

## 接下来按顺序推进

1. 完成新栈 2.5 GiB formal 的九格和独立审计、归档；任一失败即停止该矩阵。
2. 按 [固定基线预注册](experiments/2026-10-11/FIXED_BASELINE_PREREGISTRATION.md) 在开发 trace 上扫描六个固定参数点和两个同期候选，再锁定最佳固定参数做预先生成的新文本验证。
3. 补同所有权栈立即卸载/原生对照，以及长 prefill、稳定 decode、集中恢复等新到达模式；不因单一 trace 的最优结果设置生产默认值。
4. 之后再做模型/硬件扩展、性能归因与展示；上游问题只在复现和维护边界明确后评估 issue/PR。

今天北京时间 21:50 后停止启动新实验格，22:00 安全收尾，所有部分矩阵明确标记未完成。授权持续推进不等于放宽正确性或统计门槛。
