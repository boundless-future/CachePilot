# 显式抢占与恢复实验

## 范围与方法

使用 RTX 4090 24GB、Qwen3-4B、vLLM 0.30.0、固定 LMCache 研究提交及 2 GiB GPU KV 池。启用 `EVICTION_AWARE`，horizon=2.5、每步最多提交 64 项。诊断 `PreemptionConnector` 仅记录原始 scheduler 调用前后的状态，不修改抢占、释放、STORE 或 retrieve 行为；本实验不是性能测量。

先完成一个 4,570-token prompt、256-token greedy 对照请求。随后发送相同输入的流式请求，收到首个非空文本 chunk 后调用开发接口：

```text
POST /reset_prefix_cache?reset_running_requests=true&reset_external=false
```

验收要求实际出现一次 `RUNNING -> PREEMPTED`，computed tokens 清零、请求继续完成，最终请求表和 block 分配条目删除、跟踪 block 引用归零、延迟释放队列清空、空闲块恢复。额外比较输出文本、检查 SSE `[DONE]`、完成长度、后续请求和 LMCache 后台传输状态。HTTP 200 或接口 `success` 不能替代这些证据。

## 已观察结果

| 实验 | 异步调度 | reset 接口 | 抢占与恢复 | 最终资源 | 全套验收 |
| --- | --- | --- | --- | --- | --- |
| r1 | 默认开启 | 500 | 已抢占；旧脚本遇 HTTP 错误即关闭流，未完成恢复验收 | 未完整验收 | 失败 |
| r2 | 关闭 | 200 / success=true | 一次抢占，完成 256 token，输出与对照相同 | 909/909，引用归零，延迟释放为 0 | 通过 |
| r3 | 默认开启 | 500 | 一次抢占，完成 256 token，输出与对照相同 | 909/909，引用归零，延迟释放为 0 | 失败：reset API |
| r4 | 关闭，最终脚本复跑 | 200 / success=true | 一次抢占，完成 256 token，输出与对照相同 | 909/909，引用归零，延迟释放为 0 | 通过 |

两种调度模式下，抢占前请求持有 286 个 block，空闲块为 623/909。同步调度在抢占后立即恢复至 909；异步调度仍为 623，后续步骤才完成释放。r3 最终状态为 `FINISHED_LENGTH_CAPPED`，request 注册与分配条目均不存在，follow-up 正常完成 8 token，LMCache healthy，STORE/prefetch 队列与任务均为 0。

## 异步调度下的接口限制

r1/r3 的错误来自 vLLM `Scheduler.reset_prefix_cache()`：`_preempt_request()` 可以通过 `_free_request_blocks()` 把仍可能被在途 GPU step 写入的 block 放入 `deferred_frees`；紧接着 prefix reset 要求 block 已全部释放，失败后抛出 RuntimeError。r1 事件记录显示抢占后仍有 286 个 block 引用，一组 deferred free。日志为：

```text
Failed to reset prefix cache because some blocks (286) are not freed yet
RuntimeError: Failed to reset KV cache even when all the running requests are
preempted and moved to the waiting queue. This is likely due to the presence
of running requests waiting for remote KV transfer, which is not supported yet.
```

这次请求已经处于 `RUNNING`，并非等待远端回载；错误文本的推测不能当作原因。关闭异步调度的对照避免了上述延迟释放窗口。r3 保留 API 错误并继续消费流，证明本次 API 错误后生成和资源释放仍能完成。全套结果继续标记 `passed=false`，不把局部恢复成功改写为接口通过。

当前证据定位到 scheduler 的释放/reset 时序；尚未做原生 vLLM 对照或检查更新的上游修复，不能据此宣称 LMCache 独有缺陷或已具备上游 PR 结论。没有修改已安装的 vLLM/LMCache。

## 限制与后续

- 本次是开发接口强制抢占，不覆盖 GPU block 不足触发的自然抢占，也不代表多请求竞争已通过。
- LMCache 日志确认丢弃被抢占请求的一项 buffered STORE，但这几轮没有完成或失败 STORE 回执，不能宣称覆盖在途保存。
- LMCache 当前对 `PREEMPTED` 请求返回零外部命中，因此本次恢复主要是本地重算，不代表重新 CPU 回载验证。
- r3/r4 退出后 GPU 注册、读写锁与传输任务归零，但 `active_sessions=3/2`；这不是 session 清理通过证据。会话 TTL/退出回收仍需单独复核。
- r4 生成期间未发现 `ERROR` / `Traceback`，退出时仍出现 `CudaIPCTypes.cpp:16` 的 producer 先于共享 CUDA tensor 释放警告。退出后没有 GPU compute 进程，不等于 CUDA IPC 释放顺序已完整验证。
- r1/r2 与最终脚本存在迭代差异：r1 立即抛 HTTP 错误；r2 尚未把 reset 状态包装成 HTTP status/body，也未记录退出后状态。保留原始结果，不重新格式化成最终版本。

证据：本目录 `preemption-r1-result.json` 至 `preemption-r4-result.json`，以及 `preemption-r3-events/`、`preemption-r4-events/`。完整文本、服务日志和失败证据保留在服务器 `artifacts/preemption-2026-09-27-r*/`。新增 8 项验收回归测试覆盖缺失抢占、身份错配、重复事件、资源未释放、后续重新出现引用、保存失败和截断 SSE。最终收紧后的验收函数重新读取 r3/r4 事件，生命周期结果仍通过；本地全套测试 50 项中 44 通过、6 项因没有 LMCache 跳过。

服务器全套 50 项测试全部通过，包含真实 LMCache 契约测试。实验服务已退出，`pgrep` 未发现 vLLM/LMCache 进程，`nvidia-smi` 无 GPU compute 进程。

```bash
python scripts/preemption_smoke.py --no-async-scheduling \
  --output artifacts/preemption-sync
python scripts/preemption_smoke.py --output artifacts/preemption-async
```

下一步验证真实远端回载中止与 worker STORE 失败；异步调度 reset 限制保留为支线，不把关闭异步调度作为正式策略收益的一部分。
