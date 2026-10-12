# 新 generation 栈：长 prefill 正式比较

2026-10-12 13:48 完成服务器与本地独立复核：pilot 4/4、formal 12/12，正式共 816 请求、39,168 输出 tokens。原失败矩阵仍保留为失败；本轮使用独立目录、已通过诊断的默认关闭 generation connector，且仅零保护。

## 方法与准入

固定 Qwen3-4B BF16 / RTX 4090 24GB / GPU KV 2GiB / eager 同步单 worker；68 请求、284,772 prompt tokens、每请求输出 48 tokens。原到达时间、15 秒 idle grace 和身份 guard 不变。先四配置 pilot，再旋转顺序运行三次正式重复，无新调参。逐 cell 的源文件、trace、配置、原始时间戳、token、H2D、服务错误和资源门槛全部通过。热路径逐事件日志关闭，保留一次性关闭摘要和精确服务端资源集合；END submitted 不当作 ACK。

四配置是整套系统比较：native 无外部缓存；owned-immediate 是原所有权 connector；default 和 selected 使用独立 generation ledger。不能把跨 connector 差值称为单个策略或身份修复的收益。selected h5.0/d1 沿用既有开发集选择。

## 正式结果

均值 ± 样本标准差；三个单轮 P95 的均值不是将请求合并后的 P95。延迟单位秒。

| 配置 | TTFT P95 | 相对 native | ready-to-end P95 | session completion P95 | computed prefill | GPU hit | external hit |
|---|---:|---:|---:|---:|---:|---:|---:|
| native | 21.266 ± 0.291 | +0.00% | 22.817 ± 0.337 | 42.424 ± 0.469 | 284772.000 ± 0.000 | 0.000 ± 0.000 | 0.000 ± 0.000 |
| owned-immediate | 15.559 ± 0.482 | -26.84% | 16.722 ± 0.494 | 34.111 ± 0.348 | 90212.000 ± 2442.084 | 0.000 ± 0.000 | 194560.000 ± 2442.084 |
| h2p5-d64 | 17.210 ± 0.692 | -19.07% | 18.807 ± 0.948 | 36.770 ± 1.014 | 126964.000 ± 10343.836 | 3440.000 ± 3074.541 | 154368.000 ± 13063.527 |
| h5p0-d1 | 19.611 ± 0.137 | -7.78% | 21.939 ± 0.147 | 40.601 ± 0.168 | 208996.000 ± 0.000 | 0.000 ± 0.000 | 75776.000 ± 0.000 |

| 配置 | TTFT P95 r0/r1/r2（秒） | ready-to-end P95 r0/r1/r2（秒） |
|---|---|---|
| native | 21.290 / 20.964 / 21.545 | 22.837 / 22.470 / 23.143 |
| owned-immediate | 15.086 / 16.051 / 15.541 | 16.224 / 17.212 / 16.731 |
| h2p5-d64 | 16.507 / 17.891 / 17.231 | 17.788 / 19.663 / 18.971 |
| h5p0-d1 | 19.756 / 19.591 / 19.485 | 22.093 / 21.923 / 21.800 |

本轮立即卸载的 TTFT 最低；三种外部缓存整套配置均低于 native。与此前短上下文 native 最低的结果形成负载边界：长 prompt 的 recompute 成本足以抵消本轮缓存接入/搬运成本。该解释与 computed prefill 和 external hit 同向，尚非独占 kernel 或因果归因。default 比 selected 好，说明开发集上的固定参数选择没有跨负载稳定支配；保留选择而不事后挑选重新包装。未证明非零保护策略收益。

## 请求计时与搬运

| 配置 | queue 均值 ms | prefill 均值 ms | decode 均值 ms | 抢占 r0/r1/r2 | H2D / D2H staging GiB 均值 |
|---|---:|---:|---:|---|---|
| native | 11543.15 ± 233.06 | 346.59 ± 0.55 | 1506.04 ± 20.08 | 0 / 0 / 0 | N/A |
| owned-immediate | 8142.89 ± 274.90 | 112.27 ± 0.53 | 1284.34 ± 14.60 | 0 / 0 / 0 | 26.719 / 11.250 |
| h2p5-d64 | 9077.93 ± 399.69 | 406.42 ± 73.80 | 1485.53 ± 117.93 | 10 / 12 / 12 | 21.199 / 9.410 |
| h5p0-d1 | 10723.51 ± 117.66 | 315.07 ± 1.02 | 1569.83 ± 7.18 | 3 / 3 / 3 | 10.406 / 5.871 |

vLLM 请求阶段 counters 不是独占 GPU kernel 测量；请求之间以及 transfer staging 可能重叠，不能相加为总耗时预算。搬运字节使用 direction/phase 标签的 staging counters；不将合并所有 LMCache 标签的 delta 当物理流量。完整单轮值和 staging elapsed 在 `long-prefill-costs.json`。

## 事后阶段分解

使用固定 48 early / 8 pressure / 12 revisit，不作为新的调参准则；下表 TTFT P95 均值 ± SD（秒）。

| 配置 | early | pressure | revisit |
|---|---:|---:|---:|
| native | 22.247 ± 0.321 | 17.903 ± 0.367 | 13.235 ± 0.468 |
| owned-immediate | 16.291 ± 0.486 | 11.686 ± 0.414 | 4.956 ± 0.364 |
| h2p5-d64 | 17.732 ± 0.526 | 13.707 ± 0.642 | 7.616 ± 1.035 |
| h5p0-d1 | 20.000 ± 0.134 | 16.775 ± 0.164 | 11.391 ± 0.114 |

SSE 首末非空 fragment 和结束 gap 已在 `long-prefill-phases.json` 逐轮保存；fragment 可能含多 token，不称为逐 token ITL。

## 输出差异和限制

| 配置 | 相对同轮 native 的输出 hash 不同请求数 r0/r1/r2（每轮68） |
|---|---|
| owned-immediate | 12 / 10 / 10 |
| h2p5-d64 | 11 / 6 / 5 |
| h5p0-d1 | 8 / 9 / 10 |

固定 greedy、seed 和输出预算不保证不同缓存/调度路径逐字一致。本轮性能门槛验证输入/输出数量、服务与资源闭合；bitwise KV 正确性证据来自前置独立 GPU 诊断，不将性能数据中的 hash 差异隐去或解释为已定位。三次重复、单合成 trace、单硬件/模型，不能外推显著性或真实 Agent 质量。

## 证据与下一步

- 预注册：`GENERATION_MATRIX_PREREGISTRATION.md`。
- 原始目录：`generation-matrix-evidence/generation-long-prefill-matrix-formal-20261012a/`，包含启动命令、manifest、请求/SSE、metrics、资源 snapshots、generation summaries 和原日志。
- 独立复算：`long-prefill-local-independent.json`；图：`long-prefill.png`（已视觉检查）；描述性分解：`long-prefill-phases.json`、`long-prefill-costs.json`。
- 原冻结源码包和194项字节复核继续适用，历史 formal 不受后续 decode runner 扩展影响。
- 下一阶段为已预注册的 decode192：保持原 held-out prompts/arrivals，将48改192输出；新冻结、新pilot，完整通过后再formal。

decode 同步后回归第一次因服务器缺少测试读取的历史 archive 失败（821 passed、9 skipped、1 failed），已保留原日志。补齐原始 archive 后全套822 passed、115 subtests、9 CUDA显式跳过；随后显式CUDA两文件9 passed。合计831个测试，分开执行，不写成一次全套CUDA回归。离线cost分析在Windows遇到默认GBK读取UTF-8错误，修正显式UTF-8后12格分析完成；不影响冻结GPU实验。
