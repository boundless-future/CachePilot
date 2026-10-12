# generation 与有界保护：三重复开发对照

2026-10-12。十二格全部通过，816 测量请求。服务器、本地当前代码、本地冻结源三份独立复算报告完全一致。one/two 的 TTFT P95 均值相对调优零保护低 4.74%/3.34%，但改善幅度波动且双 chunk 在 r2 退化；没有重算或 H2D 降低证据，不宣称稳定泛化收益。

## 冻结设计

依据 [预注册](GENERATION_STRATEGY_FORMAL_PREREGISTRATION.md)，保留 default-zero、selected-zero、selected-one、selected-two 四组，每组三次独立冷启动，按重复左旋 0/1/2 位。default-zero 使用 h2.5/d64，其余三组统一 h5/d1；one/two 预算为一/两个 chunk，即 16/32 个物理 blocks。所有组使用同一个 GenerationProtectionConnector 与 generation/ownership 栈。

Qwen3-4B BF16、4090 24GB、同步 eager、TP=1，GPU KV 2 GiB、CPU KV 16 GiB、idle grace 15 秒。每格 68 测量请求、48 输出 tokens，加一条独立预热；完整矩阵实际为 816 测量请求、39,168 输出 tokens、828 个 generation。关闭逐步 JSONL、KV 内容和物理 BlockPool 探针，仅保留计数和收尾摘要。

原 pilot 的 221 项源逐字节不变，只显式加入 formal runner、formal auditor、pilot auditor，共 224 项冻结源。pilot 不计入正式聚合；旧 h2.5/d64 保护候选也不混入。任一格失败即停止，不追加重跑筛选数据。

## 解释边界

主指标是每格 TTFT P95 的三次均值及样本标准差，不是把 204 个请求池化后的 P95。n=3 且位置轮转不完全平衡，标准差不是置信区间，不能作显著性或泛化声明。

排队、prefill、decode、staging、保护 helper 回调计时描述不同范围，可能重叠，不能相加成为独立关键路径分解；staging 不是独立 DMA 耗时。零预算没有 helper 计时不代表零 Connector 开销。SSE fragment 不等于逐个 GPU token。

本次性能轮不重新证明 bitwise 或物理 BlockPool 909 free；那些证据来自单独的正确性准入。固定输出 token 数也不保证跨配置文本逐字一致。

## 正式结果

| 配置 | r0 / r1 / r2 TTFT P95 ms | 均值 ± 样本SD ms | 相对 selected-zero | 保护提交 / 峰值blocks |
|---|---|---:|---:|---|
| default-zero | 8461.67 / 8401.77 / 8210.54 | 8357.99 ± 131.17 | -1.52% | 0/0/0 / 0 |
| selected-zero | 8716.33 / 8308.32 / 8435.61 | 8486.75 ± 208.76 | +0.00% | 0/0/0 / 0 |
| selected-one | 7797.71 / 8080.88 / 8374.15 | 8084.25 ± 288.23 | -4.74% | 4/4/4 / 16 |
| selected-two | 8044.57 / 7909.06 / 8657.10 | 8203.57 ± 398.56 | -3.34% | 4/4/3 / 32 |

one 同 repeat 相对 selected-zero 的变化为 -10.54% / -2.74% / -0.73%；two 为 -7.71% / -4.81% / +2.63%。one 三次较低，但 r2 改善很小；two 两次较低、一次较高，不能把均值写成每轮都加速。

所有格 prefill 24,156 tokens、external hit 12,288 tokens、H2D 1,811,939,328 bytes，抢占计数均为 0。因此 TTFT 改善不支持减少重算或回载的机制解释。D2H 的均值和三次值如下，搬运减少同样不等于关键路径减少：

| 配置 | D2H均值 bytes | 三次 bytes |
|---|---:|---|
| default-zero | 2919235584 | 2868903936 / 3019898880 / 2868903936 |
| selected-zero | 2856321024 | 2868903936 / 2868903936 / 2831155200 |
| selected-one | 2743074816 | 2453667840 / 2906652672 / 2868903936 |
| selected-two | 2692743168 | 2566914048 / 2642411520 / 2868903936 |

## 成本和端到端口径

下表为三次单格统计的均值；完整样本SD、单次值及SSE跨度见 [离线成本报告](generation-strategy-formal-evidence/formal-costs.json)。全部计时单位 ms；回调列是整个 cell 的累计 wall ms，而非每请求耗时。

| 配置 | 服务端queue均值 | prefill均值 | decode均值 | ready-to-end P95 | 会话完成P95 | helper累计wall |
|---|---:|---:|---:|---:|---:|---:|
| default-zero | 3562.58 | 48.74 | 1243.35 | 9558.19 | 31927.26 | 0.00 |
| selected-zero | 3602.66 | 48.57 | 1241.85 | 9694.38 | 31683.61 | 0.00 |
| selected-one | 3447.52 | 47.66 | 1214.52 | 9277.34 | 31642.96 | 105.34 |
| selected-two | 3486.70 | 48.02 | 1217.82 | 9379.96 | 31653.46 | 111.03 |

保护 helper 累计 CPU ms 为 one 105.03、two 110.67，累计 wall ms 为 105.34/111.03。H2D staging 秒均值 default/zero/one/two 为 0.08672/0.08636/0.07607/0.07842；D2H 为 0.14052/0.13453/0.12997/0.12602。这些是聚合计数，不能用来扣除独立 DMA 成本。保护组 queue 均值略低，但当前实验没有证明保护→排队变化的因果链。会话完成 P95 均值接近，不能把首token改善写成会话整体同幅加速。

## 覆盖、错误与资源

| 配置/重复 | 容量回退 | 在途预算回退 | 输出差异/68（对同期selected-zero） | 停机traceback行 |
|---|---:|---:|---:|---:|
| default-zero-r0 | 0 | 0 | 4 | 0 |
| selected-zero-r0 | 0 | 0 | 0 | 0 |
| selected-one-r0 | 31 | 3 | 2 | 8 |
| selected-two-r0 | 31 | 2 | 2 | 0 |
| default-zero-r1 | 0 | 0 | 0 | 0 |
| selected-zero-r1 | 0 | 0 | 0 | 0 |
| selected-one-r1 | 32 | 3 | 0 | 0 |
| selected-two-r1 | 31 | 4 | 2 | 0 |
| default-zero-r2 | 0 | 0 | 2 | 0 |
| selected-zero-r2 | 0 | 0 | 0 | 0 |
| selected-one-r2 | 32 | 1 | 0 | 0 |
| selected-two-r2 | 33 | 2 | 2 | 0 |

回退是摘要事件计数，不是候选命中率。one 各4批，two 为4/4/3批；提交与原回执一致、失败回执0，峰值保护16/32 blocks未越界，退出前prepared/inflight/pending handoff清零。每格69唯一generation精确注册/END，ledger闭合；server会话、job、transfer与退出GPU注册清零。

selected-one-r0 保留8行已知 output-handler 停机 traceback，精确上下文分类通过，其余格没有该类记录；运行期 fatal 门槛通过，不称所有日志无错误。跨配置 output hash 差异保留，本轮不解决数值差异根因。

## 归档与复现

- [本地复算](generation-strategy-formal-evidence/formal-local-audit.json)、[冻结源复算](generation-strategy-formal-evidence/formal-frozen-audit.json)、[服务端报告](generation-strategy-formal-evidence/generation-strategy-formal-20261012a/independent-summary.json) 完全相同。
- [原始字节核对](generation-strategy-formal-evidence/raw-byte-audit.json)：183文件、11,129,441 bytes，与服务器逐一SHA256相同；manifest本身不计入自身清单。日志显式纳入Git。
- [224项源字节核对](generation-strategy-formal-evidence/source-byte-audit.json)。[源码包](generation-strategy-formal-evidence/generation-strategy-formal-20261012a-source.tar.gz) 917,115 bytes，SHA256 `3ff9f8cf7f5e76e0499acfbf5aef0e5d49c23d7087b4dc5ab58445ee56e86eb3`；包包含辅助目录项，224是清单内运行源数。
- [本地47项测试](generation-strategy-formal-evidence/local-tests.txt)：formal 9、pilot 6、原始归档篡改 5、测量回归 27；不是全套native/CUDA重跑。
- 入口：`python scripts/audit_generation_strategy_formal.py <raw-directory> --output <audit.json>`；成本：`python scripts/analyze_generation_strategy_costs.py <raw-directory> --output <costs.json>`。冻结源复现须同时解包scripts/configs，不能用后续源码代替。
- [最终收尾](generation-strategy-formal-evidence/final-closeout.json)：224源未变，8000/8080/5555空闲，GPU 1MiB/0%，容器保持开机。系统未安装ss，端口检查使用现有socket绑定门槛，不影响实验结果。

## 阶段结论与下一步

P3C 新generation＋保护组合的开发消融已经完成，不能再写成等待formal。当前最可靠的交付是小预算保护的正确性准入、可复现消融与失败证据链；性能证据限于这个开发负载的均值改善，未决定生产默认预算。

接下来固定当前四组和预算，在此前已经冻结的新文本validation trace上做独立覆盖准入，先核对来源、输入交集、token边界和新栈契约，再pilot；不重新调horizon、drain、reserve、到达或文本来追求覆盖。历史固定策略validation零保护提交失败保留，不能由本开发formal改写。若新栈仍无保护覆盖，停止泛化矩阵并报告触发局限，转向离线候选/容量原因分析；全部pilot通过才预注册三重复。随后才考虑真实匿名结构的非零保护复核，以及8B/5090扩展。

多worker、异步/图模式、真实driver/DMA故障、服务poison自动恢复、抢占受害请求完整续跑和退出traceback仍是独立开放工作。后续可冻结机制分析实验区分D2H与调度影响，不能用追加正确性smoke代替收益验证。
