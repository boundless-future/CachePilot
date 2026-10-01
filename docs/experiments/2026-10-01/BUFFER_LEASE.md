# Token 校验与活动 buffer lease：CPU 合约

日期：2026-10-01。属于阶段 3B 所有权支线，**3B 未通过**。没有修改安装的 LMCache，没有启动推理服务或执行 GPU DMA。

## 解决的具体问题

[Lookup reader slot](OWNED_LOOKUP.md) 保存了原始 reservation，但 reservation 身份隔离只约束释放，不足以保护数据访问。校验 token 后若 TTL 到期，原有 `is_locked()` 会变为 false；普通 eviction、delete、clear 或 reserve_write 随后可以释放或覆盖消费者仍持有的内存。

本轮把 reservation 与活动读取分开：reservation TTL 决定是否还能开始读取；已开始读取建立独立 pin，只有消费者明确确认停止访问所有 buffer 后才能解除。TTL 和 reset 不解除活动 pin。消费者永久不终结时引用保持可见，没有通过超时自动回收的实现。

## 接口与同步范围

独立 C++ 扩展新增 `pin(token)`、`unpin(lease)` 和 `active_count()`。pin 在 native mutex 内先执行 TTL 检查，再验证 lock ID、epoch 和有效 serial；失效 token 无法获得 lease。lease ID 单调递增且不受 reset 影响。`is_locked()` 同时检查 live reservations 和活动 pins；有效 token 若还有 pin，release 返回 `ACTIVE_LEASE`，不隐式结束读取。

`scripts/leased_l1_contract.py` 提供独立 `LeasedL1Harness`，沿用真实 L1Manager 的普通 reserve_write、delete、clear 与 eviction eligibility 方法：

1. `begin_read_owned(reservations)` 在 L1 元数据锁内查找原 entry、检查 writer、校验 token 并 pin，然后取得原 buffer。全部成功才返回 batch 的 buffers。
2. 获取中途失败时回滚已建立的 pins，不交付部分 buffers。回滚未全部成功时保留 handle、原 core/pin 和错误，不自动重试。
3. 消费者停止访问全部 buffers 后，调用 `finish_read_lease(handle, terminal=True)`。正常终结逐 pin 解除；TTL 已过期且原 temporary entry 没有其他读写者时，用真实 delete 清理。
4. lease 终结不消费仍有效的 reservation；原 owner 继续使用 `finish_read_owned`。过期 reservation 的释放返回 stale，不将它记为成功。
5. 逐项 unpin 成功先从持有集合移除，再记录结果；后续通知或 allocator 失败保留终结证据，不重复释放或 free。

`terminal=True` 是调用方的停止访问承诺，当前没有 CUDA event、DMA future 或实际 worker 提供证明。异常、取消意图、GC 和时间到期均不代表读取结束。

原生 `delete(force=True)`、`clear(force=True)` 和 `close()` 可以绕过读锁。CPU harness 明确拒绝这些接口；安全 shutdown 的 drain 和 writer ownership 尚待实现。直接改 `_objects`、调用基类 force/free、单独释放 allocator，或未建立 lease 就使用 acquisition 中的原始 buffer，均不在本轮保证范围内。

## 验证

新增 **7 项 native 测试、19 项 L1 lease 测试及 9 个子测试**。专项共 **37 passed、9 subtests passed**，包含原有 11 项 native reservation 测试。

CPU allocator fixture 使用真实 `bytearray`；释放时写入 `F` 并放回池中，再分配写入 `N`，因此可实际观察释放、覆盖和同一内存复用。这不是 LMCache 的 pinned-memory allocator，也不是 KV tensor 比较。

| 路径 | 结果与范围 |
|---|---|
| 先 pin、后 TTL | live=0、active=1；delete/clear/write/eviction 拒绝，原 buffer 64 字节仍全为 K |
| TTL 先于 begin | 拒绝旧 token，未交付 buffer |
| reset / 新 reader / 旧终结 | 老 pin 继续保护；重复老终结不解除新 lease |
| 读取期间释放 reservation | 返回 active_lease，不消费 pin 或 reservation |
| 多 reader temporary | TTL 后第一个 lease 结束不 free，最后一个结束恰好 free 一次 |
| 同 key 删除重建且复用同一 buffer | 终结后可 free/复用；旧 token 因 foreign_lock 不能读取新对象 |
| batch 缺失、stale、inactive、foreign、writer | 不交付部分 buffers；成功 rollback 不遗留活动 pin |
| rollback / terminal 的部分 unpin 失败 | 原身份、剩余 pins 与错误保留，成功项不重试 |
| terminal=False / 错误 manager / 重复终结 | 拒绝，不改变其他活动 lease |
| pin/release native 竞争 | 100 轮；pin 获得保护或 release 先消费 token，不出现已释放 token 的新 pin |
| begin/reset/delete 竞争 | 50 轮；取得 buffer 则删除被拒绝，否则无 buffer 交付 |
| 元数据同步范围 | 屏障暂停 pin 时，另一个线程的真实 delete 等待 L1 锁 |
| CPU future 延迟消费 | TTL 后仍不能回收；future 实际读出原内容后才声明 terminal |
| 并发重复 unpin / terminal | 各 8 线程，恰好一个成功；temporary free 一次 |
| GIL 释放下并发 lease | 8 线程、512 个独立 lease，全数解除且 reservation 闭合 |
| allocator / 通知异常 | 已知 unpin 成功与清理错误同时保留，不重复 free |
| listener 重入替换后续 batch key | 按 entry 身份检查，不删除新对象 |

完整服务器回归：**311 passed、55 subtests passed、117 warnings**。本地：**122 passed、189 skipped、26 subtests passed**。服务器设置 `CACHEPILOT_REQUIRE_RESERVATION_NATIVE=1`，强制执行 Linux native/L1/controller/storage/lookup 契约；警告来自上游 telemetry/torch 弃用接口。

证据：[完整回归日志](buffer-lease-contract/full-pytest.txt)、[源码与依赖校验](buffer-lease-contract/evidence.json)、[本轮独立扩展构建](buffer-lease-contract/build.json)。旧报告中的扩展 hash 和事实保留；当前 artifacts 下的扩展已经重新构建为 lease 版本。

```bash
export PATH=/usr/local/miniconda3/envs/cachepilot/bin:$PATH
python scripts/build_reservation_native.py
CACHEPILOT_REQUIRE_RESERVATION_NATIVE=1 python -m pytest tests/test_reservation_native.py tests/test_leased_l1_contract.py -q
CACHEPILOT_REQUIRE_RESERVATION_NATIVE=1 python -m pytest -q
```

## 下一步

先把 Lookup 的 running reader slot 接到该 lease API，固定 claim、begin、实际消费停止、unpin 与 reservation release 的次序，覆盖 END、TTL 和失败竞争。之后核查并接入真实 RETRIEVE 的 buffer 获取、异步传输 future/终结和 wire metadata，不从 bitmap/key 补造 token。此次没有证明真实 DMA 终结，也没有解决 writer TTL、客户端失联、跨进程/重启身份或安全 shutdown。

这一步不改变 GPU 策略或既有服务候选。无 END 死亡残留、原生匿名 TTLLock 反例和 RETRIEVE underflow 的资源失败仍是未关闭项；GPU 策略消融继续等待 3B 资源门槛。
