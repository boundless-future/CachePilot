# 客户端取消路径 smoke

## 实验范围

本轮验证真实 vLLM + LMCache MP connector 在客户端主动关闭流式响应后是否还能继续服务。客户端提交一个约 6.6K-token prompt、512-token streaming completion，在收到第一个非空 chunk 后立即关闭 HTTP response；等待 3 秒，再提交同一长前缀的 16-token follow-up。配置为 Qwen3-4B、RTX 4090、vLLM 0.30.0、LMCache `0.5.5+g1a4b40b1d`、2 GiB GPU KV、EVICTION_AWARE lazy offload。

这不是完整的取消正确性或资源回收证明：没有强制触发 `WAITING_FOR_REMOTE_KVS` 中途取消，没有模拟远端 lookup abort，也没有等待 600 秒 TTL 后复查 session 计数。它只验证客户端断流后的服务可用性和即时后台队列状态。

## 结果

- 取消请求收到 1 个 chunk 后关闭连接，客户端侧 HTTP 状态为 200，没有客户端异常；首 chunk 约在 481 ms 到达。
- 取消后等待 3 秒，LMCache `/status` 仍为 healthy：`in_flight_task_count=0`、`pending_keys_count=0`、`active_prefetch_jobs=0`、`lookup_phase_count=0`、`load_phase_count=0`，L1 对象数和锁计数均为 0。
- 后续请求返回 HTTP 200，16 个 completion tokens 完整生成，prompt tokens 为 6,653，耗时约 217 ms。
- 后续请求后 LMCache 仍 healthy，后台 transfer/prefetch 队列保持 0；GPU 服务退出后显存回到 1 MiB、利用率 0%，日志没有 `ERROR`、`Traceback`、`EngineDeadError` 或 CUDA error。
- `active_sessions` 从等待后的 1 变为 follow-up 后的 2。该计数不能直接等同于泄漏或已完成请求数，因此本轮不将其解释为回收失败。

原始结果和日志保存在 `artifacts/cancel-smoke-2026-09-27-r1/`。

## 判断

当前证据支持“客户端断流后服务仍可继续，短时间内没有待处理传输或锁住的 L1 对象”。它还不能证明异步回载中途取消、GPU block 延迟释放、request generation 隔离或 TTL 最终清理正确。下一步应在真实请求进入 `WAITING_FOR_REMOTE_KVS` 时中止连接，并在取消、迟到回载、request-id 重用后持续观察 block 数、prefetch 状态和 session TTL；保存失败则继续使用已通过的 registry/policy 契约测试先覆盖。

## 复现

```bash
python scripts/cancel_smoke.py \
  --output artifacts/cancel-smoke-2026-09-27-r1
```
