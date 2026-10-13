# 共享候选历史新文本比较预注册

2026-10-13。用户授权推进下一阶段。在启动GPU前固定以下设计；不合并旧策略/诊断/pilot与正式结果。

## 固定实验

| 配置 | horizon | drain | 完整chunk预算 |
|---|---:|---:|---:|
| default-zero | 2.5 | 64 | 0 |
| selected-zero | 5.0 | 1 | 0 |
| shared-one | 5.0 | 1 | 1（16 blocks） |
| shared-two | 5.0 | 1 | 2（32 blocks） |

所有格均使用显式SharedPrefixConnector及generation账本，zero实际绕过保护。Qwen3-4B BF16、RTX4090 24GB、GPU KV2GiB、CPU KV16GiB、idle grace15秒，同步eager/TP1。保护引用/安全reserve/原回执实现不改。

输入使用原68请求历史trace，摘要015c2148973855fe2fa1e0cb7cef4ecb136f69ecf41fde2212b61bbb9e5acd6b；75,808输入tokens、每请求48输出、固定arrival/seed/temperature。此次不是未见真实Agent数据，不加入上一轮323条诊断尾段。

pilot按表顺序每格一次。若全部通过，则同一源码签名运行formal三重复：每重复从表序列依次左移0/1/2位。每格独立启动/停止服务，无重跑挑选；pilot不并入formal。

## 准入与失败

先独立复算上一阶段正常三格、取消/抢占/失败回执/raw ID复用，以及先共享再保护严格回载诊断。与诊断源相比只允许已记录的runner/离线auditor变化和本轮两项入口新增，禁止策略runtime漂移。

测量轮关闭同步KV、逐步物理快照、handoff JSONL；仅保留一次性generation/保护/资源摘要。非零格必须实际保护提交且原回执一一闭合，H2D>0；所有68请求/3,264输出完整、SSE原时间戳独立复算、运行及关闭后session/job/transfer为空、generation精确69代END、pending/inflight/pin归零、无未知运行fatal。保留窄范围已知关闭traceback及CUDA IPC警告，记录输出hash差异。

任一格零覆盖/资源/计时/来源/日志门槛失败，立即停止后续格及formal，保存失败与未运行清单，聚合为空；不得改trace、reserve、预算或显存绕过。不将总体H2D解释为每个保护批次都回载——这项逐批证明来自独立正确性诊断。

## 分析

主指标：三次正式TTFT P95的单次值、均值及样本SD，相对selected-zero的变化；同时列default-zero。补充ready-to-end/session完成、prefill、external hits、H2D/D2H、保护次数、capacity fallback和输出差异。三次重复不足以宣称显著性/普遍收益，SD不是置信区间。

保留全部失败；每轮逐文件SHA、精确冻结源码、服务器/本地/冻结源三处复算。当前不改变多worker/异步、实际DMA fault、完整抢占恢复、联合异常组合和生产默认启用的未准入状态。
