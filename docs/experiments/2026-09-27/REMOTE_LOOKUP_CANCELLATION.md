# 原生 Connector 的远端 lookup 等待期取消：资源验收失败

## 方法与边界

RTX 4090 24GB、Qwen3-4B BF16、vLLM 0.30.0、LMCache `0.5.5+g1a4b40b1d`、2 GiB GPU KV 池。使用未修改的 `LMCacheMPConnector`，先把固定的 4,570-token prompt 存到 LMCache CPU 缓存，再重启 vLLM 清除 GPU prefix cache。脚本用 `SIGSTOP` 暂停自己启动的 MP server，提交流式请求；看到 vLLM 的 `reason="deferred"` waiting 数从 0 变为 1 后断开客户端，等该数回到 0 再恢复 server。随后检查 LMCache 状态、执行正常 follow-up，并在 vLLM 退出后再检查状态。

这制造的是**受控的 lookup 延迟**，不是自然网络中断、真实传输失败或部分写入；也不能用来比较性能。脚本的 `finally` 会恢复 server 并停止两个服务。运行命令：

```bash
python scripts/remote_lookup_cancel_smoke.py \
  --output artifacts/remote-lookup-cancel-2026-09-27-r3
```

## 观测结果

| 轮次 | 目标前读锁 | 取消后/后续请求后/引擎退出后读锁 | 取消后活动 prefetch job | 验收 |
| --- | ---: | ---: | ---: | --- |
| r1 | 0 | 17 / 17 / 17 | 未采集 | `passed=false` |
| r2 | 0 | 17 / 17 / 17 | 未采集 | `passed=false` |
| r3 | 0 | 17 / 17 / 17 | 1；后续请求和引擎退出后仍为 1 | `passed=false` |

三轮均看到 `deferred` 从 0 到 1，客户端断流后回到 0。follow-up 对同一前缀的外部命中增加 4,352 token，32-token 输出与参考一致，GPU KV usage 回到 0。r3 在取消、follow-up 和引擎退出后，`active_sessions=0`，STORE/prefetch controller 队列为 0，服务报告 healthy；但 17 个 CPU 读锁及 1 个活动 prefetch job 仍在。LMCache 日志显示该 prefetch 已完成，不能仅用“还有传输在进行”解释后续状态。结果中的资源硬验收正确返回 `false`，正常生成与服务健康不能替代资源释放验收。

正常回载对照 `artifacts/environment-2026-09-24/optimized-v2/immediate/` 的前、后及引擎退出状态中，读锁均为 0。该对照与本次并非同一启动过程，作用只是说明 17 个读锁不是正常回载必然留下的基线。本轮没有跟踪每个锁的对象 ID，也没有等待 300 秒 read TTL，因而只报告观测窗口内的残留，不断言永久泄漏。r3 引擎关闭时的 vLLM 日志还出现 `EngineDeadError`，时间位于主动 SIGTERM 后；生成和 follow-up 前没有相应服务错误，此关闭期日志保留在完整原始产物中。

[r2 结果](remote-lookup-cancel-r2-result.json)、[r3 结果](remote-lookup-cancel-r3-result.json)入库；三轮完整日志和结果位于本机及服务器的忽略目录 `artifacts/remote-lookup-cancel-2026-09-27-r{1,2,3}/`。

## 请求级时序补充（trace-r2）

为核对调度端顺序，`LookupTimelineConnector` 只包装固定版 `LMCacheMPConnector` 的 adapter/client 方法，写出请求级 JSONL，保留原调用和返回值；此轮**不是未修改的原生 Connector**，也不用于性能比较。运行 `python scripts/remote_lookup_cancel_smoke.py --trace-lookup --output artifacts/remote-lookup-cancel-2026-09-27-trace-r2`；完整日志和 JSONL 留在忽略目录，入库 [结果](lookup-timeline-r2-result.json) 与 [时序摘要](lookup-timeline-r2-summary.json)。`trace-r1` 因启动环境缺少 `lmcache` 可执行文件而未进入实验，不计为复现。

目标请求 `cmpl-81b57136f1bfcf6d-0-87f231a3` 的 LOOKUP 于 Unix 时间 `1790515211.508375` 提交，客户端于 `5211.59214` 断开。`cleanup_lookup_result()` 前 `_unacked_lookups` 仍包含 server（`5211.5960262`），清理后为空（`5211.5961077`），`END_SESSION` RPC 于 `5211.5962036` 提交，server 到 `5211.7072053` 才恢复。目标请求没有提交 `query_prefetch_status`。取消后和 vLLM 退出后均为 17 个读锁、1 个活动 job；follow-up 外部命中 4,352 token，输出与参考一致，资源验收仍为 `passed=false`。

固定版真实 `LMCacheMPSchedulerAdapter` 的特征测试 `tests/test_lookup_cancel_contract.py` 用 pending ack future 与 fake request client 复现：`cleanup_lookup_result()` 后，`end_session()` 能在 LOOKUP ack 前发送 RPC。此测试**刻画当前缺陷，不是期望行为的通过性回归**；修复后应反转断言，要求先完成/处理 LOOKUP ack，再保证取消路径消费或释放相应 prefetch job 与读锁。

## Server 侧事件与释放对照

为核对 MP server 处理顺序，两轮都使用**未修改的原生 `LMCacheMPConnector`**，并通过 `scripts/lookup_server_timeline.py` 临时包装固定版 LMCache `LookupModule`，保留 request-handler 元数据。只记录事件的 `server-trace-r1` 运行命令如下；`server-release-r1` 另加 `--release-cancelled`。两轮只用于生命周期归因，均不能用于性能比较，且诊断释放轮**不是未修改的 server**。

```bash
python scripts/remote_lookup_cancel_smoke.py --trace-server \
  --output artifacts/remote-lookup-cancel-2026-09-27-server-trace-r1
python scripts/remote_lookup_cancel_smoke.py --trace-server --release-cancelled \
  --output artifacts/remote-lookup-cancel-2026-09-27-server-release-r1
```

| 模式 | LOOKUP 后目标 job | END_SESSION 时 | 取消后 / follow-up 后 / 引擎退出后读锁 | job | 验收 |
| --- | ---: | --- | ---: | ---: | --- |
| 只记录 server 事件 | 1 | 没有目标请求的 `query_prefetch_status`，返回后 job 仍在 | 17 / 17 / 17 | 1 | `passed=false` |
| END_SESSION 前诊断释放 | 1 | 查询已完成结果为 17 chunks，移除 job 并执行原有 `free_lookup_locks` | 0 / 0 / 0 | 0 | `passed=true` |

只记录轮的目标请求 `cmpl-ab913b3e5099653e-0-a88b2a30`：server 恢复后 LOOKUP 于 Unix 时间 `1790516259.061148` 进入，job 于 `6259.065013` 注册，END_SESSION 于 `6259.065161` 进入、`6259.066115` 返回。诊断释放轮的目标请求 `cmpl-87315caaceace2c1-0-baa51607`：END_SESSION 于 `1790516513.773214` 进入，查询于 `6513.773684` 返回 17 chunks，释放读锁后于 `6513.775534` 返回。两轮的 `deferred` 都先升至 1、断流后归零；follow-up 外部命中均为 4,352 token，32-token 输出与各自参考一致。

[只记录结果](server-trace-r1-result.json)与[诊断释放结果](server-release-r1-result.json)已入库；完整事件 JSONL 和服务日志位于对应的忽略目录 `artifacts/remote-lookup-cancel-2026-09-27-server-{trace,release}-r1/`。两个结果均以事件时间和状态计数为依据，没有逐个核对 CPU lock 的对象 ID。诊断轮只验证已完成的 L1 命中 prefetch 能在 END_SESSION 前消费并释放；`query_prefetch_status()` 返回 `None` 的未完成 job、并发、重复 END_SESSION、request-id 重用和正常请求不重复释放都未解决。诊断代码不是上游修复，也不能据此通过阶段 3B 或启用 GPU 提前 pin/准入保护。

## 源码线索与下一步

固定版 `lmcache_mp_connector.py` 的 `request_finished()` 先调用 `scheduler_adapter.cleanup_lookup_result()`，再调用 `end_session()`。适配器的 `cleanup_lookup_result()` 移除 `_unacked_lookups` 和 `_lookup_status`，而 `end_session()` 只会等待它还能看到的 future。MP server 的 `lookup.py` 中，`query_prefetch_status()` 消费完成结果并移除 `_prefetch_jobs`；`END_SESSION` 删除 session，却不移除 job 或直接释放 lookup 读锁。

trace-r2 已确认调度端清理与 END_SESSION 的先后；server-trace-r1 又确认 server 先注册目标 job，再处理 END_SESSION，且没有目标状态查询。诊断释放轮将 17 个读锁和 1 个 job 均清零，强化了“取消后未消费的 prefetch job 及其读锁”这一归因；它不证明所有锁的逐对象归属，也**不是已验证修复或自然网络故障复现**。

这个原生路径的资源缺口比此前诊断 Connector 故意提交非法 block ID 的 RETRIEVE underflow 更直接影响 CachePilot 的生命周期门槛。阶段 3B 仍未通过，不能据此启动 GPU 上的提前 pin/准入保护。下一步需设计涵盖未完成异步 prefetch 和并发的最小修复，在未修改 server 的候选补丁上做原生 Connector 端到端资源复测；不接 GPU 的 fake worker 状态机可独立推进。是否向上游提 issue/PR，留待确认当前版本、最小修复和回归测试后决定。
