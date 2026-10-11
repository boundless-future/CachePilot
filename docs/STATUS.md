# CachePilot 当前状态

更新：2026-10-11 22:54。逐步执行记录见 [EVENING_PROGRESS](experiments/2026-10-11/EVENING_PROGRESS.md)，完整阶段与未完成项见 [TASKS](TASKS.md)。本页区分实现、正确性范围和性能证据，历史日期的“下一步”不能代替当前计划。

## 项目定位与实现

CachePilot 是 vLLM＋LMCache 的外部实验 Connector 和小预算 KV 卸载策略：在真实 scheduler 的分配前，复核即将面临驱逐的已结束请求前缀，最多保护一个或两个完整 chunk；由原 manager 提交 STORE，并在原身份回执后释放保护 pin。单个物理 block 为 16 tokens，一个 LMCache chunk 为 256 tokens。它不是新推理引擎，也没有重新实现 GPU copy kernel。

原 token/reservation、请求 generation、native terminal、取消和失联回收构成单独的所有权适配支线。性能对照共用该数据通路，不能把它的修复算作保护策略收益。空闲 pending STORE 的 15 秒宽限收尾是另一默认关闭的候选机制；立即丢弃导致 H2D=0 的反例保留。

## 当前阶段

| 阶段 | 已完成 | 仍开放 |
|---|---|---|
| P0/P1 环境与接入 | 4090 24GB、Qwen3-4B/BF16、vLLM 0.30.0、锁定 LMCache 研究版、CUDA 13.0，实际 D2H/H2D 验收 | 第二模型/硬件控制实验 |
| P2/3A 观测与候选 | 固定 trace、profiling、分配与 lookup 诊断；六点固定参数开发调优 24 格完成，选中 h5.0/d1 | 固定选择的新文本验证未通过覆盖门槛；更多到达分布、耗时分解 |
| 3B 所有权支线 | 受控单卡服务链路、原身份 native terminal、L2/取消/失联和资源审计；满足限定 3C 准入 | 真实 driver/DMA 故障、永久 I/O 自动恢复、多 worker/异步范围 |
| 3C 保护策略 | 零/一/两 chunk、预算与物理容量回退、共享取消/抢占、raw ID 跨代、长前缀拆分和受控 host exception；首轮正式消融 | 生产级全面正确性、可泛化收益结论 |
| P4 复核 | 首轮新文本/容量重复、新栈 2.5 GiB 九格、固定参数 24 格、无保护 reference 12 格和边界专项 | 原新文本因 one 零保护覆盖停止；到达压缩 formal 已完成；长 prefill pilot 因空闲身份校验失败停止，需独立定位；稳定 decode/真实结构压力待补 |
| P5 展示/上游 | 原始证据持续归档 | 最终复现入口、整体图表、最小可维护上游补丁评估 |

## 能支持的结论

| 对照 | 当前观察 | 解释边界 |
|---|---|---|
| 2 GiB 开发 trace，zero/one/two 各三次 | one/two 的 TTFT P95 均值相对 zero 为 -6.22%/-3.45% | [首轮消融](experiments/2026-10-11/PROTECTION_ABLATION.md)；n=3，未证明稳定通用收益 |
| 相同到达结构、新文本验证，各三次 | -0.57%/+0.31%，未稳定复现改善 | [独立输入](experiments/2026-10-11/PROTECTION_ABLATION_VALIDATION.md)；不是新分布或真实 Agent 泛化 |
| 1.5 GiB 容量，各三次 | -5.09%/-3.99%；H2D 增加 8.92%/6.49% | [容量扫描](experiments/2026-10-11/CAPACITY_SCAN.md)；搬运代价与波动一并报告 |
| 原 2.5 GiB 试点 | zero 因 owned job/session 残留失败，后续格未运行 | 原失败永久保留；独立诊断定位空闲未提交 STORE |
| 新栈 2.5 GiB 正式重复 | 九格 612 请求通过，one/two TTFT P95 均值 -3.07%/-2.00% | [正式报告](experiments/2026-10-11/IDLE_CAPACITY.md)；第一组重复退化，输出差异保留，pilot 不进入正式均值 |
| 同栈六点固定参数开发调优 | 24 格、1,632 请求通过；h5.0/d1 均值最低，one/two 相对它 +0.88%/-0.19% | [调优报告](experiments/2026-10-11/FIXED_BASELINES.md)；差异小、波动大，未证明保护优于调优固定策略 |
| 固定选择后的新文本验证 | 前两格通过；one 的 8 次候选均容量回退，保护 0/0，矩阵按规则停止 | 请求和资源通过、H2D 存在；整体未完成，无性能聚合，不重跑筛选 |
| 无保护 reference 四组 | 12 格、816 请求全部通过；native TTFT P95 6732.95 ms，h5.0/d1 7187.49 ms | [独立报告](experiments/2026-10-11/UNPROTECTED_REFERENCES.md)；整套配置对照，不评价保护策略 |
| 到达时间减半 | 12 格、816 请求通过；native 9503.55 ms，其余三组高 3.47–5.81% | [到达报告](experiments/2026-10-11/ARRIVAL_COMPRESSION.md)；缓存组少算 prefill，但整体及回访标签组均未改善 |
| 零覆盖单次独立诊断 | 9 个首次容量回退，预算充足、在途 pin 为零，之后默认 STORE；保护仍 0/0 | [诊断](experiments/2026-10-11/COVERAGE_DIAGNOSTIC.md)；安全预留限制触发范围，未修改 reserve |
| 长 prefill pilot | 前两格通过；默认 fixed 68 请求后在空闲身份预检 fatal，剩 1 owned job/11 sessions | [失败报告](experiments/2026-10-11/LONG_PREFILL.md)；2/4 格、无均值，选中 fixed 与 formal 均未运行 |

各性能组仍存在输出 hash 差异；固定输入、48 decode token、HTTP 200 或资源闭合不等于输出完全确定。独立诊断曾验证多组回载 KV bitwise 全等，但不能代替所有性能请求的逐层正确性证明。编译相关输出差异根因仍开放。

## 晚间新增正确性证据

- 空闲收尾后相同 raw ID 的 68 对 generation：旧 reclaim 均早于新 lookup；包括实际 idle-dropped 请求重入。只覆盖旧代完整结束后的复用。
- 失败回执与共享取消组合分别通过 864/936 层 KV 比较和资源审计，但 idle drop=0，只算兼容性。
- 延迟回执组合有实际 idle drop 且资源回收，但恢复命中仍在 GPU、H2D 未覆盖，整体失败。详见 [组合矩阵](experiments/2026-10-11/IDLE_RECEIPT_MATRIX.md)。
- 最新完整 CUDA/native 回归 746 passed、115 subtests；之后新增离线身份审计 5 项 CPU 测试单列。新增默认关闭的 generation/session ledger 及显式 client 边界，服务器专项 39 passed（含 22 项新增账本/边界/真实库手动交接测试）；原失败路径仍可复现。真实 tracker/manager 交接验证 A/B 分代 END，但 terminal/seal 由测试交付，尚未接入生产 adapter，不能算长 prefill 修复。

## 接下来按顺序推进

1. 新栈 2.5 GiB formal 九格已经独立审计、归档；保留原失败和新栈结论之间的版本边界。
2. 六点开发调优完成，后续新文本验证因保护覆盖失败停止；保留原始反例与无均值审计，不将状态改为通过。
3. 无保护 reference 已按独立预注册完成：12/12 格通过，native 在该 trace 最低；到达时间压缩 formal 12/12 完成，native 仍有最低整体 TTFT；长 prefill pilot 在默认 fixed 的空闲身份检查处失败，按规则停止。先定位该新边界，再补稳定 decode、集中恢复；不因单一 trace 最优设置生产默认。
4. 两轮独立身份诊断已执行，定位抢占新 tracker 跳过 lookup 后沿用旧 mapping：实际出现新代 STORE、旧代 END，真实库最小复现通过；两轮资源仍失败。已实现 [generation ledger 候选](experiments/2026-10-11/GENERATION_LEDGER_CANDIDATE.md)，下一步是将其接到 adapter 的真实 tracker/manager 回调并做逐身份 server 审计，再准入新长 prefill pilot；尚未修复。

原 22:00 持续推进已收尾、heartbeat 已暂停；用户随后再次要求继续，本轮追加两次独立诊断。22:53 实验服务均停止，GPU 空闲，容器保持开机。所有旧失败保持不变。
