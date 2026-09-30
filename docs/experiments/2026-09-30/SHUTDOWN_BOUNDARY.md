# L2 等待期关闭：区分 controller 清理与进程退出

沿用真实 FS L2 取消脚本，增加 `--shutdown-held-phase lookup|load`。取消请求并让 server 候选接管 job 后，在真实 FS coroutine 的 lookup 或 load 前异步屏障处保持等待，对本实验 LMCache 父进程发送 SIGTERM。记录 LookupModule.close、StorageManager.close、PrefetchController.stop、FSL2Adapter.close 的进入/返回。此屏障可被 asyncio cancellation 打断，不能代表内核 I/O 永久卡死。

初始三轮：

| 轮次 | 等待位置 | 结果 | 局限 |
|---|---|---|---|
| load r1 | 已预留 17 个 L1 写锁，load 尚未真正读取 | 12.44 秒退出，四个关闭函数均返回，未强杀 | 只记录进入状态，未证明 controller 返回后的锁状态 |
| lookup r1 | L2 lookup 等待，尚无 L1 分配 | 9.18 秒退出，四个关闭函数均返回，未强杀 | 同上 |
| load r2 | 同 load r1，增加返回后状态观测 | 超过 15 秒观察预算后强杀，未进入四个关闭钩子 | 日志停在 HTTP 关闭阶段的 usage telemetry flush；不能归因为 prefetch controller 死锁 |

原始结果在同目录 `l2-shutdown-{load,lookup}-2026-09-30-r*`。失败轮保留，不纳入通过次数。后续将观察预算调整为 40 秒（仍短于 60 秒屏障自动放行期限），明确记录 telemetry 是否启用，再复核 controller 返回后的 L1 锁、内存和 in-flight 状态。

## 增强观测复核

load r3 显式设置 `LMCACHE_TRACK_USAGE=false`，40 秒观察预算下 3.92 秒退出、未强杀。关闭前有 17 个对象/17 个写锁、612 MiB；`PrefetchController.stop()` 返回后，真实 L1 状态为对象、读写锁、temporary、内存全部归零，见 [r3 原始结果](l2-shutdown-load-2026-09-30-r3/result.json)。这比仅检查进程退出多了一层清理证据。

同时保留两处限制：LookupModule 关闭返回时仍有 1 个 abandoned job；controller 的 `report_status()` 在关闭后仍显示 in-flight=1/load=1。源码 `_cleanup_in_flight_requests()` 清空实际字典，但没有同步所有状态计数，因此不能把关闭后这一计数直接判为真实任务仍在访问内存。下一轮会并列记录真实字典长度，不以私有字段替代 live-service 公共接口。r3 的 `passed=false` 仍表示候选未完成全生命周期回收；`diagnostic_completed=true` 只表示四个关闭调用返回。

lookup r2（telemetry关闭）4.57秒完成退出；新增 `actual_in_flight_entries=0` 证实实际任务字典已空，而公开计数仍为in-flight=1/lookup=1；L1对象与读写锁均0。见 [r2结果](l2-shutdown-lookup-2026-09-30-r2/result.json)。仅将其记录为关闭后状态计数未同步，不将“计数1”报告为底层I/O还在运行，也不据此把candidate abandoned job算作已终结。

固定源码中 `PrefetchController.stop()` 在后台线程停止后调用 `_cleanup_in_flight_requests()`，释放 in-flight write reservation、L2 lock、已取得的 L1 read lock。它与 LookupModule 的 job bookkeeping 是两层所有权。当前候选 `close()` 只做最后一次非阻塞 reap，controller 还没完成时记录 unresolved abandoned job；不能把进程结束后的地址空间释放当成候选已达成 exactly-once job 终结。真正阻塞的文件系统 executor、DMA/传输尚在访问内存以及关闭后 completion 的协议仍需单独设计。
