# TTL 后的锁所有权边界

真实原生 `TTLLock` 与 `L1Manager.finish_read` 的特征测试 `tests/test_ttl_lock_ownership.py` 复现：旧请求加锁 → 等待 TTL 到期 → 新请求对同一 key 加锁（计数重置为 1）→ 旧请求迟到调用 `finish_read(key, 1)` → 新请求的锁被解除，返回 SUCCESS。

测试使用 1 秒 native TTL、真实 finish_read 方法和假的内存分配器，不使用 GPU。它证明 key/count 接口本身不携带 reader generation；不能据此断言正常 Connector 已发生错误释放，但也不能用 session TTL 回调直接调用旧 key/count 解锁作为完整修复。没有 reservation 身份或可证实的存储 epoch，迟到释放可能干扰新读者。

还有一个可观测性限制：固定版 `StorageManager.finish_read_prefetched()` 调用 L1 `finish_read()`，将 succeeded/failed keys 写入事件后返回 `None`。当前回收 wrapper 只捕获异常，因此“方法没有抛异常”的 `reclaim_completed` 事件单独不能证明全部对象解锁成功。本项目 GPU 实验同时检查对象事件、读写锁、job 和最终内存，不只依赖该事件；但未来候选应显式验收底层 per-key release result。

改进约束：

1. 为 read reservation 引入可校验的身份/epoch，锁过期、重建或对象替换后旧释放应成为 no-op。
2. 已过期对象的旧 completion bitmap 可以消费以清理 bookkeeping，但不能据其 key 盲目减锁；临时对象删除也需同一 epoch 校验及无新使用者证明。
3. 底层部分释放失败必须记录失败 key，不能只把调用返回当全部成功；不盲目重试可能已经成功的部分。
4. 客户端 timeout/death、未完成 controller 和 server shutdown 应共享明确所有权协议。短 TTL tombstone 不足以区分旧 LOOKUP 和 request-id 重用。

这不推翻已完成的短时受控 L1/L2 实验，也不证明 CachePilot 的策略不可行；它限定候选修复的有效窗口，并继续阻止将其包装成完整上游补丁。当前版本的 unacked/client-death 缺口仍是 3B 支线，3C 可继续独立模型工作。


新增 `test_prefetch_release_result.py` 的两项真实接口特征测试通过：同一次释放包含正常key、仍有write lock的key和不存在的key，原生L1返回SUCCESS/KEY_IN_WRONG_STATE/KEY_NOT_EXIST并保留失败锁；StorageManager调用返回None，只把成功/失败列表写进事件。这把部分释放的可观测性限制变成可运行反例。仅分配器与事件投递被mock，native锁及两个manager方法真实执行；尚未修改候选回收算法或正式库接口。

后续增量：可选候选已增加[逐对象释放检查](CHECKED_RELEASE.md)，独立C++锁与真实L1方法的CPU契约已验证[token/epoch隔离](RESERVATION_EPOCH.md)。上述安装版反例继续保留；新的锁尚未进入controller或实际服务，不把原型验证表述为完整TTL/失联修复。
