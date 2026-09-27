# 异步 KV 回载失败后的重算诊断

## 范围

RTX 4090 24GB、Qwen3-4B BF16、vLLM 0.30.0、LMCache v0.5.5+g1a4b40b1d、2 GiB GPU KV 池。诊断 Connector 保留真实 LMCache CPU 回载及 future 的完成时序；仅在目标 retrieve 的原始 future 返回 `true` 后，向 worker adapter 返回一次 `false`。这验证的是**传输完成后模拟失败结果**及下游重算/清理链，不是实际网络中断、超时或生产 Connector 无注入路径。使用 `kv_load_failure_policy=recompute`，不作性能结论。

脚本先用 4,570-token prompt 写入 CPU KV，停止并重启 vLLM 清除 GPU prefix，随后用同一输入请求触发异步回载及注入；greedy、seed 42、`ignore_eos=true`、生成 32 token。记录 worker future 结果、错误 block、scheduler 状态、输出、block 引用、deferred free、LMCache 锁/任务和后续请求。命令：

```bash
python scripts/retrieve_failure_smoke.py \
  --output artifacts/retrieve-failure-2026-09-27-r3
```

## 结果

| 轮次 | 观察 | 结论 |
| --- | --- | --- |
| r1 | `EVICTION_AWARE` 在低压力 warmup 中没有提交 STORE；重启后外部命中为 0 | 前提不成立，未测到 retrieve |
| r2 | 改用即时 STORE 后外部命中 4,352 token；实际 retrieve 成功，错误 block 与重算均出现；旧验收错误要求清零时请求已离开 `WAITING_FOR_REMOTE_KVS` | 实验链路有效，脚本 `passed=false` 是验收状态假设错误 |
| r3 | 修正验收后完整自动通过 | 诊断失败回退及资源清理通过 |

r3 中原始 future 返回 `true`，随后一次性返回模拟 `false`；272 个回载 block 被 worker 标为 load error，scheduler 收到同一集合并按 `recompute` 处理。请求在 `WAITING_FOR_REMOTE_KVS` 时把已计算 token 数清零，随后进入 `RUNNING` 并执行本地 prefill。目标完成 32 token，文本与 warmup 对照相同，最终状态为 `FINISHED_LENGTH_CAPPED`。GPU 空闲 block 从 909 恢复到 909；请求从注册表消失，跟踪 block 引用和 deferred free 均为 0。后续 8-token 请求成功。LMCache 运行中读/写锁及 STORE/prefetch 队列/任务均为 0；vLLM 退出后 GPU 注册清空且 `active_sessions=0`。

验收先检查 `actual_result=true` 和外部命中，再检查 worker load error、scheduler invalid block、完成通知、清零/重算、最终输出与资源。否则本地重算成功会误判为回载失败恢复成功。r2 原始事件用修正后的验收函数复核通过，但原始 `passed=false` 保留。r1、r2、r3 结果 JSON 及 r3 worker/scheduler 事件在本目录；完整日志和事件备份在本地忽略的 `artifacts/retrieve-failure-2026-09-27-r*/`，服务器同名目录仍保留。

## 边界与下一步

本次没有中断正在传输的 IPC 请求，也没有模拟部分加载、真实超时或 LMCache 进程故障。即使结果通过，阶段 3B 的真实异步 lookup/传输中止仍未完成；还需验证 worker STORE 失败与 scheduler 回执、自然抢占/在途 STORE 和迟到回执。诊断 Connector 不进入正式策略或性能对照。
