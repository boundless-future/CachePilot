# L2 等待期关闭：区分 controller 清理与进程退出

沿用真实 FS L2 取消脚本，增加 `--shutdown-held-phase lookup|load`。取消请求并让 server 候选接管 job 后，在真实 FS coroutine 的 lookup 或 load 前异步屏障处保持等待，对本实验 LMCache 父进程发送 SIGTERM。记录 LookupModule.close、StorageManager.close、PrefetchController.stop、FSL2Adapter.close 的进入/返回。此屏障可被 asyncio cancellation 打断，不能代表内核 I/O 永久卡死。

初始三轮：

| 轮次 | 等待位置 | 结果 | 局限 |
|---|---|---|---|
| load r1 | 已预留 17 个 L1 写锁，load 尚未真正读取 | 12.44 秒退出，四个关闭函数均返回，未强杀 | 只记录进入状态，未证明 controller 返回后的锁状态 |
| lookup r1 | L2 lookup 等待，尚无 L1 分配 | 9.18 秒退出，四个关闭函数均返回，未强杀 | 同上 |
| load r2 | 同 load r1，增加返回后状态观测 | 超过 15 秒观察预算后强杀，未进入四个关闭钩子 | 日志停在 HTTP 关闭阶段的 usage telemetry flush；不能归因为 prefetch controller 死锁 |

原始结果在同目录 `l2-shutdown-{load,lookup}-2026-09-30-r*`。失败轮保留，不纳入通过次数。后续将观察预算调整为 40 秒（仍短于 60 秒屏障自动放行期限），明确记录 telemetry 是否启用，再复核 controller 返回后的 L1 锁、内存和 in-flight 状态。

固定源码中 `PrefetchController.stop()` 在后台线程停止后调用 `_cleanup_in_flight_requests()`，释放 in-flight write reservation、L2 lock、已取得的 L1 read lock。它与 LookupModule 的 job bookkeeping 是两层所有权。当前候选 `close()` 只做最后一次非阻塞 reap，controller 还没完成时记录 unresolved abandoned job；不能把进程结束后的地址空间释放当成候选已达成 exactly-once job 终结。真正阻塞的文件系统 executor、DMA/传输尚在访问内存以及关闭后 completion 的协议仍需单独设计。
