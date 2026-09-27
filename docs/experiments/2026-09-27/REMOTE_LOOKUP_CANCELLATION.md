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

## 源码线索与下一步

固定版 `lmcache_mp_connector.py` 的 `request_finished()` 先调用 `scheduler_adapter.cleanup_lookup_result()`，再调用 `end_session()`。适配器的 `cleanup_lookup_result()` 移除 `_unacked_lookups` 和 `_lookup_status`，而 `end_session()` 只会等待它还能看到的 future。MP server 的 `lookup.py` 中，`query_prefetch_status()` 消费完成结果并移除 `_prefetch_jobs`；`END_SESSION` 删除 session，却不移除 job 或直接释放 lookup 读锁。取消时本地状态清理、server LOOKUP 注册、状态查询和 END_SESSION 的具体先后仍需请求级事件证据确认。上述源码路径与实测吻合，但**不是已定位并修复的根因**。

这个原生路径的资源缺口比此前诊断 Connector 故意提交非法 block ID 的 RETRIEVE underflow 更直接影响 CachePilot 的生命周期门槛。阶段 3B 仍未通过，不能据此启动 GPU 上的提前 pin/准入保护。下一步应做请求级时序与最小回归复现，验证取消语义下 job/读锁的恰当释放时机；同时继续独立实现不接 GPU 的 fake worker 状态机。是否向上游提 issue/PR，留待确认当前版本、最小修复和回归测试后决定。
