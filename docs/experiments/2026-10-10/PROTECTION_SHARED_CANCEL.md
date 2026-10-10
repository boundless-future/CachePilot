# 3C：受保护 GPU 前缀被新请求复用后取消

RTX 4090 24GB / Qwen3-4B BF16 / vLLM 0.30.0 / 固定 LMCache 研究提交；2 GiB GPU KV、16 GiB L1，eager、同步调度、TP=1。r3 完整通过，r1/r2 未通过并保留。不是性能实验。

## 验证对象与流程

当前保护源请求必须已经结束，不能取消这个源请求。本轮验证新请求命中同一组受保护 GPU block 后，正常客户端断流会不会误释放原保护引用。

1. 单次预热源前缀，以固定容量压力触发两个 chunk 的真实保护 STORE。
2. worker 保留真实 STORE future，只用诊断文件门暂缓回执交付；120 秒超时直接失败，不伪造终结或提前 unpin。
3. 新请求携带同一 512-token 前缀和不同短后缀，申请 2048 个 decode token。确认它实际持有原 32 个物理 block 的额外引用后关闭客户端 socket。
4. scheduler 正常取消新请求，确认全部共享引用从 2 减到 1，原保护/manager 批次仍在途。随后才打开回执门。
5. 原批次回执解除保护引用，全部从 1 减到 0。用固定短请求淘汰 GPU 前缀，再实际从 CPU 回载，逐层比较 KV 与恢复输出。

每个事件关联原 batch ID、原 request ID、新 request ID 和完整物理 block 集合。审计要求取消状态为 `FINISHED_ABORTED`，新请求的 allocation 消失；原保护引用恰好由原回执解除一次。服务自身的原 reservation/native terminal 审计独立执行。

## r3 结果

| 项目 | 结果 |
|---|---:|
| 完整响应请求 / 另行取消的流式请求 | 347 / 1 |
| 默认与保护 STORE 批次合计 | 13 |
| 保护 STORE / 保护 block 峰值 | 6 / 32 |
| PREPARED 零 token 回滚 | 12 |
| 共享前缀 / 共享物理 block | 512 token / 32 |
| 同一组 block 引用变化 | 2 → 1 → 0 |
| 全轮回载 chunk / KV bitwise 相同层 | 26 / 936 |
| 全部严格保护来源 chunk / 相同层 | 12 / 432 |
| 被取消请求复用的原批次严格来源 chunk / 相同层 | 2 / 72 |
| 原 reservation 释放 / native terminal | 30 / 26 |
| 最终 free block / 保护引用 | 909 / 0 |

目标恢复的 external hit 为 512 token、GPU hit 为 0，恢复文本与冷请求一致。原保护批次的两个 chunk 都有实际服务 STORE transfer、原 native terminal 和后续 H2D 的证据，期间没有相同前缀的中间 STORE。六个保护批次均有严格来源覆盖。

运行期 session、owner/job/controller、transfer、读写锁和 pin 归零；引擎停止后 GPU 注册归零。没有未知 terminal 或强制释放。固定 tail 是工作负载的一部分，本轮没有另加延迟回执恢复流量，不能据此宣布空闲延迟收尾已修复。

新增 19 项专项测试，涵盖真实 future 与诊断门的交集、超时拒绝、错误结果拒绝、错身份/部分前缀/提前释放/重复回执负例，以及全部 chunk 的严格来源要求。完整服务器显式 CUDA 回归 **525 passed、115 subtests、121 warnings，18.57 秒**。

## 失败轮与修正

- **r1**：共享引用取消正常，但脚本在命中指标 checkpoint 前额外回载源前缀；随后的目标请求变成 GPU hit 560、external hit 0，命中断言失败。删除提前回载，使用现有目标恢复与 pressure replay；原始结果保持 `passed=false`。
- **r2**：引用专项、所有权审计及 936 层 KV 比较通过，但初始 cold/hot 两个相同请求留下不同 raw ID 的延迟 STORE。保护源为 hot，cold 随后又 STORE 相同前缀，目标回载无法唯一归因于原保护批次，完整来源断言失败。
- **r3**：该专项仅预热源前缀一次，其他 tail/replay 机制沿用；不放宽来源审计，原两个 chunk 均通过。普通零预算/预算对照仍保留 cold/hot 工作负载。修正属于实验流程，不涉及保护算法或上游运行时源码。

证据：[r1/result.json](protection-shared-cancel-evidence/r1/result.json)、[r2/result.json](protection-shared-cancel-evidence/r2/result.json)、[r3/result.json](protection-shared-cancel-evidence/r3/result.json)、[r3 独立专项审计](protection-shared-cancel-evidence/r3/shared-cancel-audit.json) 及各目录的原始 events/probe、config、请求响应和状态。完整日志已下载，但按仓库 `*.log` 规则不提交。

## 复现与范围

在固定 cachepilot 环境、端口空闲时使用新输出目录：

```bash
python scripts/protection_zero_smoke.py --output artifacts/shared-cancel-new --bounded-workload --modes two --shared-cancel
python -m scripts.audit_protection_shared_cancel artifacts/shared-cancel-new/two --output artifacts/shared-cancel-new/two/shared-cancel-audit.json
CACHEPILOT_RUN_CUDA_CONTRACT=1 python -m pytest -q
```

实际通过目录：`artifacts/protection-shared-cancel-20261010-r3/two`。

本轮关闭“原保护 STORE 回执未交付时，新请求复用同一 GPU 前缀后取消”的专项。实际 STORE 已成功，不能把诊断门说成 DMA 执行中断；未覆盖自然抢占、重复/跨代回执的 GPU 注入、真实 STORE 执行失败、在途预算耗尽、容量回退、较长前缀拆分或性能收益。下一步优先在保护引用存在的窗口内引入容量压力并观察实际自然抢占，明确是否抢占了共享前缀请求；随后补回执身份拒绝验证。
