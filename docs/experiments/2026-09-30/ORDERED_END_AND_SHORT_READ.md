# 3B：客户端 END 顺序与真实文件短读

## 客户端顺序候选

固定版 adapter 的 `end_session()` 本来会等待 LOOKUP ack 和已提交 status，但 Connector 先调用 `cleanup_lookup_result()`，提前移除了这些 future。新增实验 `LookupOrderingConnector` 在 cleanup 后保留这两类 future，由原始 END 方法消费；其他状态照常清理。客户端原方法的源码 SHA256 固定，安装包不改写。

`python scripts/remote_lookup_cancel_smoke.py --ordered-end --trace-server --reclaim-cancelled --output artifacts/ordered-end-reclaim-2026-09-30-r1` 使用客户端顺序候选和既有 server 回收候选。server 暂停期间客户端断流；观察到 cleanup 已保存 pending ack 后恢复 server，避免等待被同步 END 阻塞的 scheduler 指标导致实验死锁。

[结果](ordered-end-r1/result.json)和[目标事件](ordered-end-r1/target-events.jsonl)确认：

- cleanup 保留 ack：Unix `1790772412.7392864`。
- server LOOKUP 返回：`1790772412.8426845`。
- 客户端 END_SESSION RPC 提交：`1790772412.8433533`，晚于 LOOKUP handler 返回。
- server 回收 17 个读锁；取消、follow-up 和引擎退出后 job/锁/完成结果均为 0；follow-up 命中 4,352 token，32-token 输出一致。

真实 adapter 的四项测试分别覆盖 pending ack、pending status、无 pending 状态和 timeout 的现状。**此候选会同步阻塞 scheduler，不能当成性能策略。** MQ 超时仍可丢失 ack 并跳过 END；无 END 的进程死亡也未处理。尚未证明所有 wire 乱序下均安全，更没有 generation 协议。因此仅记为确认成功时的顺序恢复验证，3B 不标为全面通过。

## L2 真实短读与前缀回收

沿用 [FS L2 实验](L2_PREFETCH_CANCELLATION.md)，真实预存 17 个 KV 文件、重启清空 L1。在客户端取消后、原始 load coroutine 读取前，把**本轮实验目录**中的第 9 个文件（index 8）从 37,748,736 bytes 截短为 18,874,368 bytes，随后放行真实文件读取。原 FS adapter 报告 `Incomplete read ... expected 37748736, got 18874368`；不是伪造 worker 完成结果。读取完成后恢复原文件字节，验证 follow-up。失败时 finally 也恢复文件。

```bash
python scripts/l2_prefetch_cancel_smoke.py --reclaim --truncate-index 8 \
  --output artifacts/l2-short-read-2026-09-30-candidate-r1
python scripts/l2_prefetch_cancel_smoke.py --truncate-index 8 \
  --output artifacts/l2-short-read-2026-09-30-control-r1
```

| 模式 | controller 最终保留前缀 | load 完成 / follow-up / 引擎退出后读锁 | job / 完成结果 | 资源结果 |
| --- | ---: | --- | --- | --- |
| 开启回收候选 | 8 个对象 | 0 / 0 / 0 | 0 / 0 | 通过 |
| 关闭回收候选 | 8 个对象 | 8 / 8 / 8 | 1 / 1 | 失败 |

候选事件逐对象确认恰好回收目标 load 的前 8 个对象；未把短读失败位置及其后缀纳入候选释放。所有预留写锁归零、临时内存归零。对照的 8 个 retained 对象占 301,989,888 bytes（288 MiB）。两组恢复文件后的 follow-up 都命中 4,352 token、输出一致。[候选结果](short-read-candidate-r1/result.json)与[对照结果](short-read-control-r1/result.json)、状态快照和事件入库；完整日志在本地与服务器对应 artifacts 目录。磁盘文件仅存服务器，摘要入库。

这是**受控真实短读**，取消发生在 L2 lookup 等待阶段；不代表自然磁盘故障、STORE 部分写入或传输中途取消。七项审计测试包括 partial retained bitmap 和错误对象反例。没有更改 server 回收算法。后续优先观察无 END_SESSION 时 TTL 和 prefetch 资源的差异，再处理关闭/永久未完成边界。
