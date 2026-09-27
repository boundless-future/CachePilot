# 异步 STORE 失败回执诊断

## 范围与方法

RTX 4090 24GB、Qwen3-4B BF16、vLLM 0.30.0、LMCache v0.5.5+g1a4b40b1d、2 GiB GPU KV 池。使用 `EVICTION_AWARE`，horizon=2.5，每步最多提交一个 STORE。诊断 Connector 等待真实 STORE future 完成；若原始结果为 `true`，仅把返回 worker adapter 的这一次结果改为 `false`。因此验证的是**成功写入后模拟失败回执**，不是实际写入失败、部分写入、网络中断或生产 Connector 的未注入路径。不用于性能评估。

先用 4,570-token prompt 生成 256 token，使请求在 decode 期间提交 STORE。随后发送后续请求验证服务仍可用。两次请求均使用 greedy、seed 42、`ignore_eos=true`。验收读取 worker 和 scheduler 的原始事件，要求成功 future、`completed_store_requests` 与 `failed_store_requests` 同时报告、scheduler 关闭在途批次并丢弃待保存后缀，每个被 pin 的 block 引用计数恰好减一，最后请求完成且空闲 block 回到初始容量。运行入口：

```bash
python scripts/store_failure_smoke.py \
  --output artifacts/store-failure-2026-09-27-r2
```

## 结果

| 轮次 | 观察 | 结论 |
| --- | --- | --- |
| r1 | 真实 STORE 返回 `true`，worker 报 `completed=1` 和失败，scheduler 清除在途批次及 pending 后缀；目标请求完成，最后空闲块为 909 | 旧验收要求请求表为空时必有快照，但首次空请求表快照尚未生成，`passed=false` 是验收误判；r1 没有逐 block pin 计数，不能单独作为完整验收 |
| r2 | 增加绑定 block pool 时的容量基线和回执前后逐 block 引用计数，自动验收通过 | 该诊断路径下回执、pin 与最终资源闭合 |

r2 中 STORE future 先返回 `true`，然后诊断层报告 `false`。worker 在同一条回执中给目标请求 `completed=1` 且置于 `failed` 集合；scheduler 收到前该请求仍有在途批次和 pending 后缀，收到后两者均清空。128 个被 pin 的 block 引用计数逐个从 2 降到 1；回执前后空闲数均为 623，说明本次只解除额外 pin，block 当时仍由活动请求持有。目标生成 256 token 并以 `FINISHED_LENGTH_CAPPED` 结束；请求离开 scheduler 后空闲块恢复为 909/909。后续请求生成 32 token，也正常完成。

运行中 LMCache healthy，STORE/prefetch 队列与任务、读写锁均为 0；vLLM 退出后 GPU 注册列表为空且 `active_sessions=0`。r2 的原始结果与事件见本目录 `store-failure-r2-result.json` 和 `store-failure-r2-events.jsonl`，r1 的误判结果见 `store-failure-r1-result.json`。完整日志、HTTP 响应和事件保存在本地忽略的 `artifacts/store-failure-2026-09-27-r1/`、`r2/`，服务器同名目录仍保留。验收回归测试在本地与服务器均通过；服务器全套 58 项通过，本地 52 项通过、6 项因缺少 LMCache 跳过。实验后无 vLLM/LMCache 服务或 GPU compute 进程。

## 边界与下一步

真实写入已经成功，因此 CPU KV 中可能仍有有效副本；本实验不能证明失败时数据没有写入，也不能验证部分写入后的重复提交或一致性。阶段 3B 仍需真正的 worker STORE 失败、远端异步 lookup/传输中止、自然抢占时的在途 STORE、迟到回执，以及异步 reset API 与 session TTL 复核。诊断 Connector 不进入最终策略或性能对照。
