# 异步 KV 回载等待期取消

## 范围与方法

使用 Qwen3-4B、RTX 4090 24GB、vLLM 0.30.0、LMCache `0.5.5+g1a4b40b1d`、2 GiB GPU KV 池。诊断 Connector 在 worker 侧延迟提交 retrieve 8 秒，同时记录 scheduler 的队列、`scheduler.requests`、block 引用和空闲块。先以约 4,570-token prompt 写入 LMCache CPU 缓存，重启 vLLM 清除 GPU prefix cache，再提交相同前缀的流式请求，在首 token 前约 1 秒关闭客户端连接。后续请求验证服务和缓存仍可用。

这是生命周期诊断注入，不是默认 LMCache 路径，也不能用于延迟或吞吐性能比较。诊断注入通过文件标记向 worker 传递 scheduler 取消状态，只适用于这个单机实验。

## 迭代与误判

- r5 曾输出 `passed=true`，但只检查请求不在调度队列中。取消后 272 个 block 仍被 `scheduler.requests` 持有，空闲块为 637/909。这个结果无效，不能解释为上游资源泄漏，因为诊断 Connector 自己跳过 retrieve 却未发送完成通知。
- r6 补了定时器到期后的 `finished_recving`，但取消后服务空闲，不再调用 worker `get_finished()`；只有回执排队，没有完成传递。
- r7 由 follow-up 驱动轮询后，补发的 `finished_recving` 与 eager STORE 路径先返回的 `finished_sending` 冲突。vLLM 已经删除请求，再收到接收完成通知触发 `assert req_id in self.requests`，HTTP 500。原因仍是诊断协议实现错误。
- r8 在 worker 观察到取消标记的同一步只返回一次接收完成，抑制该请求的发送完成，并确保延迟定时器不会重复回执。

## r8 结果

取消请求确实进入 `WAITING_FOR_REMOTE_KVS`，回载预留 272 个 block，空闲块从 909 下降至 637。客户端在首 chunk 前断流，retrieve 没有提交给 LMCache。worker 一次 `worker_cancel_completion` 对应 scheduler 一次 `finished_recving`；请求从 `scheduler.requests` 消失，所跟踪 block 引用为零，空闲块恢复至 909。follow-up 完成 8 个 token，外部 prefix hit 累计从 4,352 增至 8,704 token。LMCache `/status` 仍 healthy，活动 session 和待处理传输均为 0；`l1_read_locked=17` 是缓存对象状态，不能单独解释为泄漏。服务日志无 `ERROR` / `Traceback`，退出后无残留 vLLM/LMCache 进程或 GPU compute 占用。

原始结果为 `lifecycle-r8-result.json`，调度与 worker 事件为 `lifecycle-r8-events-*.jsonl`；服务器完整日志留在 `artifacts/lifecycle-2026-09-27-r8/`。r5-r7 的失败结果留在服务器同名目录，避免把诊断注入错误归咎于上游。

锁与验收条件收紧后，最终代码以同样参数完成 r9 复跑，`lifecycle-r9-result.json` 再次记录 272 个 block 从 637/909 恢复到 909/909、单次 worker/scheduler 完成回执和 follow-up 命中。服务器日志无错误，测试为 10 passed。

## 边界与下一步

本次只验证等待期取消，且需注意完成通知依赖 worker 后续轮询；实验通过诊断 Connector 保证协议闭合，不代表未修改的 LMCache 生产路径已覆盖这一时序。显式抢占、真实远端回载中止、worker STORE 失败以及相同 request ID 的 generation/迟到回执仍需分别验证。完成这些生命周期门槛后，才进入准入预算和候选保护的正式策略原型。

后续使用原生 Connector 的受控远端 lookup 取消实验三次观察到读锁残留，r3 还观察到未移除的 prefetch job，见 [原生 lookup 取消](REMOTE_LOOKUP_CANCELLATION.md)。因此本节 r8/r9 的诊断协议闭合不能用于判定原生路径资源门槛通过；当时仅凭 17 个读锁无法归因，后续的取消前 0、取消后 17 和正常回载对照提供了更强的证据。

```bash
python scripts/lifecycle_smoke.py \
  --output artifacts/lifecycle-2026-09-27-r8
```
