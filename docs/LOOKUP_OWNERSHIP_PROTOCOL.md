# MP prefetch 所有权协议：修复前的约束

状态：设计约束与独立CPU原型，未接入或修改 LMCache 正式安装。依据 [TTL 边界](experiments/2026-09-30/TTL_OWNERSHIP_BOUNDARY.md)、[客户端死亡](experiments/2026-09-30/CLIENT_DEATH_NO_END.md)、[关闭观测](experiments/2026-09-30/SHUTDOWN_BOUNDARY.md)。当前受控取消候选仍仅适用于已登记 LOOKUP、明确 END、controller 能终结且未跨锁 TTL 的测试窗口。

## 为什么只补 END 或 TTL 不够

END 到达时 job 可能尚未登记，客户端也可能永远不发 END。session、GPU 注册、prefetch job、completion bitmap、L1 reservation 是不同对象，回收其中一个不自动释放另一个。固定版 reader unlock 只有 key/count；旧锁到期并被新请求重新加锁后，旧 completion 不能再盲目减这个key的锁。单独持有Python job对象身份只区分job，不区分native读锁的代。

## 最小需要的身份和状态

| 对象 | 所需身份/信息 | 解决的问题 |
|---|---|---|
| client incarnation | 每次连接/引擎实例的新ID | 已死亡实例的迟到消息不能作用于重启后的实例 |
| request generation | incarnation内单调generation或唯一nonce，所有LOOKUP/QUERY/END携带 | 请求ID重用和END/LOOKUP乱序不能靠短TTL猜测 |
| prefetch handle | server incarnation + job序号；保留原keys/layout/readers | 迟到controller completion不能查新session重建旧keys |
| read reservation | 不可复用reservation ID，绑定key及对象/锁epoch | 释放只消费自己持有的引用，过期旧reservation成为no-op |
| write reservation | 对象epoch与正在写的任务身份 | shutdown不能在真实writer尚可访问缓冲区时释放/重分配它 |
| controller terminal result | 完成、失败或取消确认；标出实际保留的对象 | 收到取消意图不等于I/O不再使用内存 |

这意味着通用修复超出当前 `LookupModule` wrapper；可能需要修改storage/native接口和wire metadata。当前不凭空承诺小补丁即可解决全部边界。

## 状态迁移和回收

LOOKUP接纳后由server持有job；正常消费把相应read reservation移交消费者，取消/失联则标为abandoned。controller终结后，未移交的reservation按身份释放并消费完成结果，最后删除job。QUERY与abandon竞争只能有一个成功转移所有权。底层释放必须返回逐reservation结果；部分成功时保留明确失败集，不重试已成功项，不用“没有抛异常”当全部成功。

若END比LOOKUP先到：必须先知道哪个incarnation/generation被关闭。有限内存方案可使用客户端确认水位、受限未确认窗口和服务器已关闭水位；不能为任意无序ID永久保留tombstone，也不能几秒后删tombstone并声称迟到LOOKUP一定不会来。现有协议没有这些字段，因此同步等待ack的候选只能减少正常确认路径的乱序，timeout/death仍未解决。

客户端死亡可由明确的lease/连接终结触发abandon，**不能直接释放底层锁或GPU pin**。lease到期只决定“不再向这代客户端交付新结果”；I/O安全仍由controller终结确认。停止新任务后，关闭顺序应先停止/排空读写，确认executor/DMA不再访问资源，再释放reservation，最后销毁allocator。异步coroutine取消与其executor底层函数停止是两回事。

## 可先实施的有限改进

1. 保持当前可选候选及其范围，不安装为默认生产修复。
2. 已实现可选 `--checked-release`：读取真实 L1Manager 的逐key结果，保留部分成功/失败及通知异常；不完整结果不记完成，后续扫描不重试已成功项。真实native接口与候选的回归已通过，见 [逐对象释放](experiments/2026-09-30/CHECKED_RELEASE.md)。该服务候选仍是key/count接口，不提供reservation/epoch；下面的token契约尚未安装到服务。
3. 已实现独立C++ reservation锁及真实L1Manager方法的CPU契约适配，11项原生锁测试和12项L1契约测试通过，见 [原型与接入点](experiments/2026-09-30/RESERVATION_EPOCH.md)。token包含进程内lock identity、共享TTL epoch和单调serial；匿名API拒绝调用。随后将token贯穿真实controller的获取/裁剪/终结方法，新增19项CPU契约及50轮QUERY/abandon竞争通过，见 [唯一移交](experiments/2026-10-01/OWNED_PREFETCH.md)。StorageManager初始L1获取/裁剪、L2局部索引合并及纯L1一次消费也完成CPU契约，29测试、11子测试及50轮竞争通过，见 [所有权合并](experiments/2026-10-01/OWNED_STORAGE.md)。未接入实际服务和RPC；下一步接LookupModule/RETRIEVE的每worker slot和数据访问有效性，失联lease与writer所有权仍待实现。
4. 使用已完成的取消、短读、进程死亡、注册reaper及shutdown反例作回归矩阵；每个用例明确“完整通过”“已归因但失败”“未覆盖”。

上游issue #5339仍讨论该所有权边界，项目阶段收尾再评估正式issue/PR。CachePilot本身的3C CPU状态机与接口设计可以继续；GPU保护仍以资源门槛为前提。避免为获得“全绿”而把未关闭的故障路径从验收定义中删掉。
