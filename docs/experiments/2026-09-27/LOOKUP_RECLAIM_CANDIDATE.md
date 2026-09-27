# MP lookup 取消回收候选：两轮受控 L1 复测

## 范围和方法

2026-09-27，在 RTX 4090 24GB、Qwen3-4B BF16、vLLM 0.30.0、LMCache `1a4b40b1d79b0e76244f127f96ee0982f8bd270f`、2 GiB GPU KV 池上运行。客户端使用原生 `LMCacheMPConnector`；MP server 通过 `--reclaim-cancelled` 加载 [源码固定的候选 wrapper](../../../../scripts/lookup_server_reclaim.py)，不修改已安装包。此轮是修复候选的受控功能验证，不是未修改 server 的基线或性能比较。

先保存固定的 4,570-token prompt 到 CPU KV，重启 vLLM 清掉 GPU KV，再暂停 MP server。提交同一 prompt 的流式请求，观察到 `deferred=1` 后断开客户端；`deferred=0` 后恢复 server。候选在 `END_SESSION` 接管未消费的 prefetch job，保留原 job 的对象 key、reader 数和 handle；只有 controller 返回完成 bitmap 后，才释放该 bitmap 对应的 CPU read lock。未完成时等待，超时本身不触发解锁。运行命令：

```bash
python scripts/remote_lookup_cancel_smoke.py --trace-server --reclaim-cancelled \
  --output artifacts/remote-lookup-candidate-2026-09-27-r1
python scripts/remote_lookup_cancel_smoke.py --trace-server --reclaim-cancelled \
  --output artifacts/remote-lookup-candidate-2026-09-27-r2
```

## 结果

| 轮次 | 取消后回收事件 | 逐对象核对 | 取消后 / follow-up 后 / 引擎退出后读锁 | job / abandoned / controller result | 输出与外部命中 | 验收 |
| --- | ---: | --- | ---: | ---: | --- | --- |
| r1 | 1 | 17 个不同 key，reader=1 | 0 / 0 / 0 | 均为 0 | 相同；4,352 tokens | `passed=true` |
| r2 | 1 | 17 个不同 key，reader=1 | 0 / 0 / 0 | 均为 0 | 相同；4,352 tokens | `passed=true` |

两轮的 `reclaim_owned` 和 `reclaim_completed` 事件具有相同 request ID 和 job token；完成事件各包含 17 个不重复对象 key，两轮对象 key 集合相同。server 事件显示 LOOKUP 注册 job 后，END_SESSION 到达时 job 已被候选移入清理队列；后续正常请求仍经 `query_prefetch_status` 消费自己的 job。取消后、follow-up 后和引擎退出后的活动 job、abandoned job、候选 key 快照、失败回收数、controller completed result、读写锁及 prefetch 队列均为 0。参考和 follow-up 各生成 32 token，文本一致。

[r1 结果](candidate-r1-result.json)、[r2 结果](candidate-r2-result.json)、[r1 回收事件](candidate-r1-reclaim.jsonl)、[r2 回收事件](candidate-r2-reclaim.jsonl)、[r1 server 事件](candidate-r1-server.jsonl)和 [r2 server 事件](candidate-r2-server.jsonl)入库；完整服务日志仍在服务器 `artifacts/remote-lookup-candidate-2026-09-27-r{1,2}/`。固定版真实 `LookupModule` 的 15 项测试另外覆盖未完成 job、重复 END_SESSION、并发查询与取消、request-id 重用、正常消费不重复解锁、部分 bitmap 和失败可观测性。这些单元测试使用 fake storage，不等于相应场景的 GPU 端到端验证。

## 结论与未覆盖边界

候选修复了本次复现的**LOOKUP 先于 END_SESSION、L1 命中并最终完成**的取消路径；这一结论比之前只在 END_SESSION 前即时释放的诊断对照更强，但仍不能把阶段 3B 标为通过。尚未在真实服务上验证在途 L2 prefetch、END_SESSION 先于 LOOKUP 到达、客户端死亡但没有 END_SESSION、永久不完成的 controller、自然 I/O 故障、真实传输中止、进程关闭时 unresolved job，以及压力/性能影响。尤其 `END_SESSION` 先到的协议乱序不能靠本候选的现有 request-id 映射解决；需要可靠 generation/顺序协议。候选关闭时对 unresolved job 仅告警，不宣称已释放。

下一步先构造真实在途 prefetch 完成晚于 END_SESSION 的可控实验，核对等待期间读锁保持、完成后逐对象释放和 follow-up 正确性；再处理协议乱序与无 END_SESSION 的所有权终止条件。未达到完整资源门槛前，继续关闭 GPU 提前 pin/准入保护。上游最小补丁与 PR 留待边界验证和当前版本复核。
