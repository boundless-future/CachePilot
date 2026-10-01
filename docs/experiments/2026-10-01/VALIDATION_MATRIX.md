# 3B 受控准入与 3C 下一步

更新：2026-10-01。**可以开始受控单卡 3C；GPU 保护策略尚未实现，完整生产恢复门槛尚未通过。** 这次准入限定 RTX 4090 24GB、Qwen3-4B BF16、eager、TP=1、单 full-attention group、固定源码版本和隔离实例。实现、失败历史及复现命令见 [实际服务报告](OWNED_SERVICE.md)，实施顺序见 [3C GPU 接入](../../3C_GPU_ENTRY.md)。

## 实际服务场景

每项使用真实 MP 服务、L1/FS L2、controller、RPC 和必要的 CUDA copy；人工控制等待/异常窗口。八项覆盖来自 r2/r3，不是一次完整矩阵全部通过；早期失败原结果不修改。

| 场景 | 结果与证据 | 验证边界 |
| --- | --- | --- |
| L2 在途取消 | [r2](owned-service-evidence/owned-service-matrix-r2/l2-cancel/result.json) 通过 | 实际文件 load 完成后回收原所有权，非永久 I/O 中断 |
| 文件短读 | [r2](owned-service-evidence/owned-service-matrix-r2/l2-short-read/result.json) 通过 | 裁剪与原 token 逐项释放，不能按保留前缀推断获取数量 |
| 无 END 客户端死亡 | [r2](owned-service-evidence/owned-service-matrix-r2/client-death/result.json) 通过 | SIGKILL 后 heartbeat 过期清理；17 个原 token；恢复命中 4352 tokens，输出一致；使用专门死亡审计 |
| 持有 L2 load 时关闭 | [r2](owned-service-evidence/owned-service-matrix-r2/held-shutdown/result.json) 通过 | 预算内拒绝关闭，消费者停止后关闭；不是永久内核阻塞自动恢复 |
| STORE 提交后 host 异常 | [r2](owned-service-evidence/owned-service-matrix-r2/store-enqueue-error/result.json) 通过 | 保留原 writer 至 native terminal，非 CUDA driver fault |
| RETRIEVE 提交后 host 异常 | [r2](owned-service-evidence/owned-service-matrix-r2/retrieve-enqueue-error/result.json) 通过 | 保留原 reader/pin 至 native terminal，非硬件传输中断 |
| STORE 终结等待期取消 | [r3](owned-service-evidence/owned-service-matrix-r3/cancel-store-stream/result.json) 通过 | r2 未覆盖原 transfer 窗口，失败保留；r3 按原身份验证 |
| RETRIEVE 终结等待期取消 | [r3](owned-service-evidence/owned-service-matrix-r3/cancel-retrieve-stream/result.json) 通过 | copy 提交后 stream hold，取消早于原 terminal；不证明 DMA 执行重叠 |

## 最终快照复查

| 验收 | 证据 | 结果 |
| --- | --- | --- |
| CUDA 完整回归 | [日志](owned-service-evidence/owned-service-regression-r4.txt) | 438 passed、115 subtests、133 warnings |
| 正常 eager 回载 | [结果](owned-service-evidence/owned-service-normal-r3/owned-service/result.json)、[原 token 审计](owned-service-evidence/owned-service-normal-r3/ownership-audit.json) | 1536 tokens 外部命中，文本一致；18 token/4 terminal；两个引擎各 909 free |
| 自然抢占第一次 | [r8 结果](owned-service-evidence/owned-service-preemption-r8/result.json)、[审计](owned-service-evidence/owned-service-preemption-r8/ownership-audit.json) | 4 次抢占，其中 2 次处于选定原 STORE 等待窗口；512 pin 引用释放；24 token/13 terminal；454 free |
| 自然抢占独立重复 | [r9 结果](owned-service-evidence/owned-service-preemption-r9/result.json)、[审计](owned-service-evidence/owned-service-preemption-r9/ownership-audit.json) | 同样覆盖；每轮 12 chunk/432 层 KV bitwise 相同，6 个 orphan 源 chunk 无中间 STORE |
| STORE 丢 terminal | [负例结果](owned-service-evidence/owned-service-no-terminal-store-r1/result.json) | 预期 unresolved；8 个 writer 和 context 保留，直接 close/整实例 shutdown 均拒绝 |
| RETRIEVE 丢 terminal | [负例结果](owned-service-evidence/owned-service-no-terminal-retrieve-r1/result.json) | 预期 unresolved；17 个 reader/pin 和 context 保留，直接 close/整实例 shutdown 均拒绝 |

自然抢占采用 50ms EVICTION_AWARE 卸载期限、5 秒回执观测延迟、前三批 copy 提交后的 stream hold；这是正确性诊断，不能用于性能结论。四条生成完成不等于并发 decode 文本等价。最终矩阵早于最后两项兼容/分析器修补，最终快照没有重新执行整个八场景矩阵，已重新执行上述回归、正常回载及两轮自然抢占。

[实际执行源码 SHA256](owned-service-evidence/owned-service-final-source-sha256.txt) 覆盖仓库的 173 个源码、测试与配置文件，与本地内容按 LF 归一后全部一致；[native build manifest](owned-service-evidence/reservation-native/build.json) 保留编译参数和二进制 hash，不提交二进制。归档的 normal-r3/r8/r9 已在本地重新执行完整日志审计，均通过。旧轮的文件和失败结果保留，`.log` 仅改名为 `.txt`。

## 开放问题与停止条件

- [r6](owned-service-evidence/owned-service-preemption-r6/result.json)：30 秒回执延迟，两个空 LOOKUP owner 到关闭才回收，运行期 drain 失败。与 pending suffix / 空闲 END 不执行相容，根因未唯一证明，尚未修复。5 秒通过不能代替该验收。
- [r7](owned-service-evidence/owned-service-preemption-r7/result.json)：KV 解析器误读 layout，原结果保留为失败；修复后 r8/r9 重跑通过，不把 r7 改成成功。
- 永久 I/O 自动恢复、真实 driver fault、硬件 DMA 执行期中断、实际 L2 不确定提交异常、多 GPU、长期乱序及上游最小补丁继续开放。缺 terminal 时只验证保留资源和拒绝关闭，恢复依靠完整实例重建。
- 异步 reset API、compile-only 文本差异根因继续独立记录；eager 的局部等价与 KV 对比不能扩大为所有配置的正确性。

3C 每轮必须审计原 token、job/controller/transfer/read/write/pin/batch、退出后 GPU 注册和最终 BlockPool，核查新增路径 KV。未知终结、资源残留或关闭拒绝即停止该轮；不能强行释放、仅看 free block 或靠关闭清理宣布运行期通过。

## 进入 3C

1. 实现零保护预算的外部 `ProtectionConnector`，接真实 scheduler/worker metadata 与 receipt；与同一所有权栈的默认 EVICTION_AWARE 比较，无新增保护 pin 或 STORE。
2. 只保护最多两个完整连续前缀 chunk，串行 pin 前复核物理 block 与双 hash；验证零步回滚、原回执、取消/失败、ID 重用和预算回退。
3. 新路径正确性通过后关闭诊断日志，在同轨迹/预算/版本下做至少三次重复消融，报告性能与保护代价，保留退化结果。

所有权适配器是阶段 3B 的独立支线，不计作 CachePilot 策略的性能贡献。3C 开工不意味着扩大生产范围，也不需要现在升级 GPU。
