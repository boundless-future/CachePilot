# 共享前缀保护：历史输入正式比较

2026-10-13。四组pilot通过后，同240项源码完成四配置×三次轮转正式实验，**12/12通过**，816请求/39,168输出tokens。候选解决了原历史输入的零保护覆盖问题；本次TTFT均值改善约1.5%，但没有减少prefill或H2D，尚不足以证明稳定、可泛化的策略收益。

## 设计

沿用 [预注册](SHARED_STRATEGY_PREREGISTRATION.md) 与 [pilot](SHARED_STRATEGY_PILOT.md)：Qwen3-4B BF16、RTX4090 24GB、GPU KV2GiB/CPU16GiB、同步eager/TP1。历史合成trace每格68请求/75,808输入tokens、每请求48输出，固定输入/arrival/seed/temperature。没有诊断尾段，没有同步KV或逐步状态探针。四组共用generation与SharedPrefixConnector，预算0/16/32 blocks；default h2.5/d64，其余h5/d1。没有更换reserve、挑选重复或调整输入。

轮转顺序：r0 default-zero→selected-zero→shared-one→shared-two；r1从selected-zero开始；r2从shared-one开始。pilot不混入正式均值。旧失败矩阵仍保持失败。

## 主结果

单位ms；±是三次P95的**样本标准差**，不是请求级波动或置信区间。均值不是合并请求后的P95。

| 配置 | r0 P95 | r1 P95 | r2 P95 | 均值±SD | 相对selected-zero | 提交/回执 |
|---|---:|---:|---:|---:|---:|---|
| default-zero | 9514.86 | 9432.23 | 9389.45 | 9445.51±63.75 | +2.05% | 0/0每格 |
| selected-zero | 9294.28 | 9191.66 | 9280.68 | 9255.54±55.74 | 基线 | 0/0每格 |
| shared-one | 9016.15 | 9096.20 | 9232.71 | 9115.02±109.50 | −1.52% | 4/4每格 |
| shared-two | 9025.00 | 9214.13 | 9089.37 | 9109.50±96.16 | −1.58% | 4/4每格 |

two在r1比selected-zero慢约0.24%；不能只报告均值而隐藏退化。所有12格prefill **24,240 tokens**、H2D **1,811,939,328 bytes**完全相同，实际抢占次数为0。D2H三次均值分别2,868,903,936 / 2,793,406,464 / 2,705,326,080 / 2,805,989,376 bytes；one的D2H略低但two略高，尚没有一致的减少搬运/重算解释。

## 成本与端到端指标

以下均为三格描述性均值，完整单次值和SD见 [成本数据](evidence/shared-strategy-costs.json)，[服务器/本地成本复算一致](evidence/shared-strategy-analysis-audit.json)。首次上传分析脚本时远端日期文档目录尚未创建，未执行分析；建目录后复算成功，没有重跑GPU或更改冻结源。

| 配置 | 服务端queue均值ms | prefill均值ms | decode均值ms | ready-to-end P95 ms | session完成P95 ms | 保护helper CPU总计ms/格 |
|---|---:|---:|---:|---:|---:|---:|
| default-zero | 4036.78 | 51.54 | 1338.05 | 10752.41 | 32092.69 | 0 |
| selected-zero | 3945.62 | 52.19 | 1318.91 | 10550.98 | 32041.71 | 0 |
| shared-one | 3877.64 | 51.90 | 1306.89 | 10396.95 | 31985.33 | 100.85 |
| shared-two | 3875.92 | 50.61 | 1303.81 | 10374.83 | 31962.92 | 100.26 |

各格duration counter count均为68，原SSE时间戳、输出hash、H2D/D2H独立重算。staging总计H2D约0.072秒/格，D2H约0.104–0.111秒/格；这些不是独立GPU DMA关键路径。callback wall约105ms/非零格；CPU/wall计时仅覆盖已插桩helper方法，可能嵌套，zero的0不是整个Connector零开销。queue、prefill、decode、staging和callback不能相加作为延迟因果分解。

## 覆盖、输出与失败边界

- 非零六格全部保护4批，原回执全部闭合、failed receipt为0、峰值16/32块。one capacity fallback32/32/32、budget3/5/3；two capacity31/32/31、budget3/1/5。每格69代END，资源摘要pending/inflight/pin归零，服务session/job/transfer清空。
- 总体H2D>0不证明每个保护批次随后被使用。先共享再保护的逐批严格STORE→H2D证据仍来自独立正确性诊断，不把此性能轮当作新bitwise证明。
- 同repeat对selected-zero的输出差异只有shared-two-r1的event50/51；其余比较一致。保留原文/hash；不能据固定输入/seed断言decode逐条相同，也没有任务质量评价。
- default-zero-r2在已排空且进入关闭流程后有`AsyncLLM output_handler`/`EngineDeadError` traceback，按原预注册窄规则单列；12格的CUDA IPC退出警告完整保留。运行fatal检查通过，不写成“日志无错误”。
- 本轮没有真实DMA fault、完整抢占恢复、先共享再保护联合取消/旧代复用、多worker/异步准入，也不是生产默认启用。

## 可复现证据

- [正式原始包](evidence/shared-strategy-formal-evidence.tar.gz)：188文件、11,589,331原始bytes；压缩1,494,514 bytes，SHA256 `3c1eda1e1c82e411c097ef2275374c8c1a000eecb7bb53e68b5a6be61b487c96`。
- [完整审计](evidence/shared-strategy-formal-audit.json)、[字节/三处复算](evidence/shared-strategy-formal-integrity.json)：服务器、本地与240项冻结源码复算逐字段相等。与pilot源签名相同，最终远端源未漂移。
- 两包共258原始文件；入口第一次缺测试文件的失败、远端27项和本地37项回归分开保留。上一阶段961项CUDA/native结果是历史正确性基线，本轮没有重跑或冒称新的全套结果。
- 离线复核：`python docs/experiments/2026-10-13/verify_shared_strategy_evidence.py <archive> <new-output-directory>`；成本复算使用同目录`analyze_shared_strategy_costs.py <extracted-matrix> --output <json>`。
- [收尾](evidence/shared-strategy-closeout.json)：240源未变，8000/8080/5555空闲，无在途实验，GPU1MiB/0%。容器保持开机。

## 阶段判断与下一步

3C已推进到“共享候选在历史反例输入上的正式对比完成”。它是实测的小幅均值改善，不能升级成稳定收益或真实Agent泛化成功。下一主线应冻结**现有六session匿名结构输入**，先共享候选覆盖pilot，再决定是否formal；该输入以前跑过零保护组，必须标注为历史负载扩展，不能叫未见测试集。

若新负载无保护覆盖或保护没有影响后续复用，应保留负结果，先离线分析候选前缀的后续复用机会，再独立设计机制诊断；不反复改原输入凑收益。共享owner离开/完整抢占恢复等联合正确性继续作为独立支线，不能与性能工作相互替代。暂不扩大模型/卡，也不根据1.5%的三次结果选生产默认值。
