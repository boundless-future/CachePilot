# MP server 拒绝 RETRIEVE：重算通过，CPU 读锁未释放

## 方法

RTX 4090 24GB、Qwen3-4B BF16、vLLM 0.30.0、LMCache v0.5.5+g1a4b40b1d、2 GiB GPU KV 池。先用正常的即时 STORE 将固定 4,570-token prompt 写入 LMCache CPU 缓存，再重启 vLLM 清除 GPU prefix cache。诊断 Connector 在下一次真实 RETRIEVE 提交时，把原本 272 个 GPU block ID 改为空列表，不改写 server future 的返回值。MP server 自身以 `RETRIEVE block ID underflow` 拒绝回载并返回 `false`。`kv_load_failure_policy=recompute` 允许 vLLM 本地重算。

这是人为构造的协议错误，只测试一个失败分支；不是自然 IPC 中断、存储故障或正常 Connector 会生成的输入，也不用于性能比较。运行命令：

```bash
python scripts/retrieve_failure_smoke.py --mode server-reject \
  --output artifacts/server-rejected-retrieve-2026-09-27-r2
```

## 两轮结果

| 轮次 | 目标前读锁 | 失败后读锁 | vLLM 退出后读锁 | 验收 |
| --- | ---: | ---: | ---: | --- |
| r1 | 未记录 | 17 | 17 | `passed=false`，资源未闭合 |
| r2 | 0 | 17 | 17 | `passed=false`，复现同一缺口 |

r2 的 server 日志显示目标请求 `cmpl-bc9a90ea40e2c852-0-9f482c7e` 需要 17 个 chunk、每个 16 个 block ID，随后写出 `skipping the retrieve`。原始 future 返回 `false`；worker 标记 272 个 load-error block，scheduler 收到同一组错误并执行重算。目标请求生成 32 token，与正常参考文本一致，以 `FINISHED_LENGTH_CAPPED` 结束。跟踪的 GPU block 引用与 deferred free 归零，空闲块恢复为 909/909；后续 8-token 请求也完成。故障发生前外部前缀命中计数增量为 4,352 token，说明这确实进入了远端回载路径。

CPU 侧不同：r2 目标前 `l1_read_locked=0`，目标结束后为 17；后续请求和 vLLM 退出后仍为 17，虽然 LMCache 报 healthy、活动 session、STORE/prefetch 队列和其他锁均为 0。因此**端到端资源验收失败**，不能把生成成功或 GPU block 回收当作完整通过。

固定版本的 `lmcache_driven_transfer.py` 中，未注册 GPU 的 RETRIEVE 失败路径调用 `_release_failed_retrieve_locks()`，而 block ID underflow 分支在正常 `try/finally` 释放区之前直接返回。这与 17 个未释放读锁的实测一致；是否已有上游修复、是否还有并发条件，需要在提报前另行核查。此实验没有修改安装的 LMCache 包。

[r1 结果](server-rejected-retrieve-r1-result.json)、[r2 结果](server-rejected-retrieve-r2-result.json)和[r2 目标请求事件](server-rejected-retrieve-r2-events-target.jsonl)已入库。完整日志、HTTP 响应和所有事件保存在本地忽略的 `artifacts/server-rejected-retrieve-2026-09-27-r1/`、`r2/`，服务器同名目录也保留。

## 对项目的影响

此缺口由诊断 Connector 提交非法 block ID 触发，尚无证据显示正常 vLLM/LMCache 会提交同样的列表，故不直接否定 CachePilot 的策略设计，也不等同于自然传输中断的行为。阶段 3B 的远端故障资源门槛仍未通过；后续需测真正的异步 lookup/传输中止和未修改路径，并在最终策略中保持故障回退，不依赖这个非法输入分支。该缺口作为独立的上游候选问题保存，项目阶段收尾时按可复现性、最小修复和回归测试决定是否提报。
