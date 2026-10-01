# Lookup reader slot 与 buffer lease 的 CPU 接入

日期：2026-10-01。阶段3B所有权支线；**3B未通过**。本轮没有修改安装的LMCache或执行GPU实验。

## 本轮实现

`scripts/leased_lookup_contract.py`新增`LeasedLookupHarness`，连接上一轮的Lookup reader slot和L1活动buffer lease。沿用固定版真实LookupModule、StorageManager、PrefetchController与L1普通方法；数据由可复用64字节CPU allocator fixture提供。原有模拟terminal的`OwnedLookupHarness`保留，历史结论不改写。

工作流如下：

1. LOOKUP/QUERY保留原reservation，向每个显式worker incarnation交付ticket。
2. CLAIM将该槽从offered改为running；`read_retrieve(ticket)`仅可调用一次，在Lookup gate→L1元数据锁的顺序下验证原token并建立lease，整个worker shard成功才交付buffers。
3. END只声明取消。领取前可释放；已领取但未读取时不再交付数据，等待失败terminal；已交付时仍保留lease。重入END发生在pin期间也不交付取消后的buffers。
4. 消费者停止所有buffer访问后，显式提交`succeeded=bool, terminal=True`。成功回执必须有先前交付；失败回执也必须确认实际消费停止。
5. 在同一个L1元数据同步范围内先unpin，再处置原reservation。有效token得到released；确认stale_epoch时单独记录过期并移除原账本项，**不把stale写成released**。

过期temporary对象可能在unpin后的清理中已删除；因此每个access保存获取时的原entry/core。对象消失或被listener重建时只查询原core，不向新对象按key释放。如果原对象异常消失但旧token仍有效，保留已知release结果及错误，不宣称整个job恢复。

## 错误与可用性边界

获取失败不交付部分buffers；正常回滚后可失败终结。rollback、不确定获取回执、部分unpin、allocator/通知或reservation处置失败时，保留原引用、已知结果与剩余项，不自动重试。成功处置的项从账本移除，未知或失败的项保留。

发现并处理一个槽级边界：job.error不能阻止其他已运行worker解除自己的lease。错误仍阻止新claim/read，但其他running槽可独立终结一次；失败槽已进入terminal，不允许重试。即使其他槽全部完成，错误job也不被计为回收成功。

`terminal=True`仍是调用方承诺，不是GPU完成证明。CPU future测试在真实CPU消费返回后才提交terminal。未证明CUDA event、DMA future或跨进程worker终结；没有wire序列化、客户端失联恢复、writer所有权、安全shutdown或强制回收。绕过harness访问原buffer、私自改`_objects`、force/free或消费者过早声明terminal均不在保证范围。

## 测试与实验

新增**23项测试、8个子测试**：

| 场景 | 验证结果 |
|---|---|
| 正常temporary读取 | 实际读取K字节，先unpin再release，3个buffer各free一次，所有registry闭合 |
| 未claim、重复read/claim、错误worker/server、缺少terminal | 拒绝，不改变活动lease；未交付不能成功ACK |
| END在claim后/read前、pin期间重入、terminal期间重入 | 不提前回收；未交付走失败终结，重入END不重复release |
| CPU future + END + 30ms TTL | 消费暂停期间delete/clear不能回收；60ms后仍读出K，future完成后才终结 |
| reset后新generation | 旧终结只处置原token，不减少新reader的reservation/pin |
| 两个reader共享temporary对象 | reset后首个终结不free，最后一个才free；6个stale单独记录 |
| TP两rank | 各自仅pin所属3个对象，不跨shard访问 |
| TTL/reset先于read、offered槽过期后END | 不交付失效buffer；已确认stale单独处置 |
| batch缺对象、rollback/unpin/获取回执未知 | 不交付部分结果；保留原引用/错误，失败项不重试 |
| 多worker部分unpin失败 | 失败槽留1个pin；另一个worker正常终结，不清除原错误 |
| 部分reservation释放失败 | 先前成功项移除，后续项保留，lease已终结证据可见 |
| allocator/通知失败 | 已知unpin保留，无重复free |
| listener重建后续key并复用buffer | 旧terminal查原core，新对象及其新reader不受影响 |
| 非法外部对象替换 | 原token的已知消费与异常同时记录，不伪报job恢复 |
| 8线程重复terminal | 恰好1次成功，3个token/3次free各一次 |
| READ/END竞争 | 30轮；已交付则lease保留，否则无交付；显式terminal后闭合 |
| READ/失败terminal竞争 | 30轮；terminal先行则无交付，read先行则等CPU消费停止 |
| 真实storage/controller方法的L2完成合并 | 初始L1 K字节与fixture L2 N字节正确交付，temporary清理闭合；未执行真实L2 I/O |

首轮专项22通过、1失败：Lookup错误消息未包含底层unpin原因，底层证据已保留。补充错误传播后专项**23 passed、8 subtests passed**。没有改变底层native实现，使用上一轮已构建的lease扩展。

完整服务器回归：**334 passed、63 subtests passed、117 warnings**。本地：**122 passed、212 skipped、26 subtests passed**。Linux native/LMCache相关项在本地跳过，服务器设置`CACHEPILOT_REQUIRE_RESERVATION_NATIVE=1`强制执行。警告来自上游telemetry和torch弃用接口。

证据：[完整回归stdout](leased-lookup-contract/full-pytest.txt)、[源码/依赖hash与范围](leased-lookup-contract/evidence.json)。stdout保存自本轮命令返回；source hash按服务器实际执行文件核对。native扩展沿用[上轮build manifest](buffer-lease-contract/build.json)。

```bash
cd /root/CachePilot
export PATH=/usr/local/miniconda3/envs/cachepilot/bin:$PATH
CACHEPILOT_REQUIRE_RESERVATION_NATIVE=1 python -m pytest tests/test_leased_lookup_contract.py -q
CACHEPILOT_REQUIRE_RESERVATION_NATIVE=1 python -m pytest -q
```

## 下一步

核查真实RETRIEVE从buffer获取、传输提交到future/CUDA完成的调用链，明确“尚未提交”“提交结果未知”“取消future但DMA未结束”如何持有reader slot；再实现带ticket的wire/worker适配与真实传输验证。后续仍需writer生命周期、无END失联恢复和shutdown。原生匿名TTL所有权、无END死亡残留与RETRIEVE underflow服务失败尚未关闭；GPU策略消融继续受3B资源门槛约束。
