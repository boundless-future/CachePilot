# MP server 拒绝 STORE 的生命周期诊断

## 方法与边界

环境沿用 [STORE 失败回执诊断](STORE_FAILURE.md)：RTX 4090 24GB、Qwen3-4B BF16、vLLM 0.30.0、LMCache v0.5.5+g1a4b40b1d、2 GiB GPU KV 池、`EVICTION_AWARE`。诊断 Connector 只在首个 STORE 提交时将一组原本 128 个 block ID 改为空列表，其余参数和原始 future 返回值不变。LMCache MP server 因 block ID 不足拒绝整个写入并返回 `false`；这是**人为构造的协议错误**，不是自然网络、磁盘或内存故障，也不用于性能比较。

每轮先用固定的 4,570-token prompt 生成 256 token，使 decode 期间提交 STORE。验收 server 的同 request ID 拒绝日志、原始 `false` future、worker/scheduler 失败回执及 block pin 释放。随后停止 vLLM、保留 LMCache server，重启 vLLM 并以同一前缀生成 32 token，用外部命中 token 增量检查是否留下可复用 CPU KV。运行入口：

```bash
python scripts/server_rejected_store_smoke.py \
  --output artifacts/server-rejected-store-2026-09-27-r2
```

## 结果

r1 和增加 server 日志断言后的 r2 均通过。r2 中 MP server 对目标请求记录 `STORE block ID underflow for request_id=cmpl-8dac9902b58b96f5-0-b63c13e2`，需要 8 个 chunk、每个 chunk 16 个 block ID，并明确写出 `skipping the store`。原始 STORE future 返回 `false`，worker 同时报 `completed=1` 和 `failed`，scheduler 收到失败回执后清除在途批次及 pending 后缀。128 个被 pin 的 block 引用计数逐个从 2 降到 1；请求结束后空闲块恢复为 909/909。目标请求完整生成 256 token，最终状态为 `FINISHED_LENGTH_CAPPED`。

vLLM 重启后，同前缀后续请求生成 32 token，外部命中 token 增量为 0。实验前后 LMCache healthy、STORE/prefetch 队列和读写锁为 0；引擎退出后 GPU 注册列表为空且 `active_sessions=0`。r2 的 [结果](server-rejected-store-r2-result.json)与[目标请求事件](server-rejected-store-r2-events.jsonl)已入库；完整日志、HTTP 响应及 r1/r2 事件保存在本地忽略的 `artifacts/server-rejected-store-2026-09-27-r1/`、`r2/`，服务器同名目录也保留。本地单测 54 项通过、6 项因缺少 LMCache 跳过；服务器 60 项全通过，实验后无残留服务或 GPU compute 进程。

这个结果比“成功写入后模拟失败回执”更接近真实的 worker 失败分支：失败值来自 MP server，且没有观察到可复用外部前缀。不过它只覆盖整次 STORE 被协议校验拒绝，不能推断部分写入、传输中断、存储耗尽或未注入生产路径的行为。重启后零外部命中与 server 拒绝日志相符，但不是逐字节检查 CPU 存储。阶段 3B 仍需自然故障/部分写入、远端异步 lookup/传输中止、自然抢占时的在途 STORE 与迟到回执，以及异步 reset API 和 session TTL 复核。
