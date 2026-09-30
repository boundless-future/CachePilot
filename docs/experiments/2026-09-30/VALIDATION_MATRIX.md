# 3B / 3C 验收矩阵（2026-09-30）

固定栈：RTX4090 24GB、Qwen3-4B、vLLM0.30.0、LMCache `1a4b40b1`。这里的“通过”只表示该行明确范围的验收，不表示整个阶段通过。原始完整日志保留于本地及服务器artifacts，摘要/事件入库。

## 3B：服务生命周期

| 路径 | 已有证据 | 结论及剩余边界 |
|---|---|---|
| END后已完成L1 lookup未消费 | 原生Connector+候选server两轮，17对象逐key释放，follow-up一致 | 受控路径通过；未修改server原路径失败 |
| END后真实FS L2 prefetch在途 | 两轮候选通过，对照17锁/job/result残留 | 真实文件读取、受控屏障；不是中断I/O |
| cleanup→END顺序 | 保留ack/status并由原adapter同步等待，一轮GPU组合、4契约测试 | 确认路径通过；会阻塞scheduler，timeout/death未修 |
| L2短读前缀 | 第9文件真实短读，候选释放前8对象；关闭候选对照8锁残留 | 受控短读通过；未覆盖自然磁盘故障 |
| 客户端死亡无END | SIGKILL后630秒观察，锁/session到期，job/result/key snapshot各1、temporary17对象612MiB | **资源失败**；功能follow-up一致不抵消资源残留 |
| GPU注册reaper | 默认grace3600秒；对照改120秒后旧注册清除 | 注册清理通过；不驱动prefetch回收 |
| TTL后旧reader释放 | 原生TTLLock+真实finish_read复现旧key/count能误减新reader锁 | **所有权缺口已复现**；不能按session TTL盲目解锁 |
| server关闭 | 可取消lookup/load屏障，controller返回后L1对象/锁释放；实际inflight字典0但公开计数1 | **候选job终结未通过**；内核/executor永久阻塞未覆盖；早期telemetry超时轮另保留 |
| L2部分写入 | RLIMIT_FSIZE真实EFBIG，8临时文件各写1MiB后删除；重启miss、输出一致、17文件恢复 | L1→L2路径通过；不等于GPU→L1 worker STORE失败 |
| 自然容量抢占 | 两轮11/12次；迟到回执两轮各16次，均8/8生成完成 | 资源路径通过；抢占钩子时无在途STORE，随后提交旧代批次再reset；DMA中抢占未覆盖 |
| orphan迟到回执 | 5秒延迟两轮各13批真实成功、4 orphan、1120 pin引用闭合；最新轮session在420秒采样归零 | 指定资源+TTL通过；延迟的是观察回执，非DMA |
| 取消+迟到回执，无tick | 两轮各128 pin释放、909free恢复；重复轮session615秒首次采样归零 | 指定取消+TTL通过；不覆盖worker永久失联 |
| 保存失败回执 | 成功后模拟失败、server协议拒绝均有既有诊断 | 协议路径通过；真实GPU传输中止/部分写入失败未覆盖 |
| RETRIEVE协议拒绝 | 真实false future、GPU重算释放，第二轮残留17 CPU锁 | **资源失败**；故意非法block IDs，不冒充原生正常输入缺陷 |
| 抢占后缓存内容 | 两轮5秒延迟各40个回载chunk/1440层比较全部逐字节相等；各1个chunk/36层无中间STORE | 两轮有限内容通过；1秒轮内容全等但orphan覆盖不足专项失败，未证明整条生成输出等价 |

详细证据：[L2取消](L2_PREFETCH_CANCELLATION.md)、[顺序与短读](ORDERED_END_AND_SHORT_READ.md)、[客户端死亡](CLIENT_DEATH_NO_END.md)、[TTL所有权](TTL_OWNERSHIP_BOUNDARY.md)、[关闭](SHUTDOWN_BOUNDARY.md)、[EFBIG](L2_WRITE_FAILURE.md)、[自然抢占](NATURAL_PREEMPTION.md)、[取消迟到回执](CANCEL_HELD_STORE.md)、[抢占后KV内容](PREEMPTION_KV_INTEGRITY.md)。9月27日失败回执/协议拒绝等报告保留在对应日期目录。

## 3C：独立CPU接入契约

| 层次 | 已完成 | 不代表什么 |
|---|---|---|
| 保护状态机 | 17测试，含6000步交错；预算、prefix、pin、cancel/reset/generation、终结 | 需求仍是供给真值，未解决预测误报 |
| chunk映射 | 7测试，16个16-token block组成256-token chunk | 块hash不等于chunk hash |
| 原生BlockPool桥接 | 8测试，真实touch/free queue、分配避开pin、hash与外部引用检查 | 仅idle full block，未安装进scheduler，无CUDA KV |
| token provenance | 6测试，真实Request追加decode及两条真实hash链 | 仅文本、无salt/extra keys、group0；未证明tensor内容 |
| 实际STORE metadata | 7测试，对照原生tracker两个连续范围；交接前回滚、交接后等终结 | CPU构造，未发RPC；只支持单worker精确一次、有序回执假设 |

见 [模型与契约说明](PROTECTION_STATE_MACHINE.md)、[adapter约束](../../PROTECTION_ADAPTER_CONTRACT.md)。整体仍为 **3B未通过、3C CPU契约已推进、GPU策略未接入**。

## 下一轮优先级

1. 以无END死亡后的job/result/reservation为主要缺口，先把 [所有权协议](../../LOOKUP_OWNERSHIP_PROTOCOL.md) 落到可维护的存储接口和真实契约测试。不要用匿名TTL解锁抹平失败。
2. 区分最小正常取消补丁和完整失联恢复协议的范围；对部分释放结果显式验收，controller未终结时保持可见，不宣称已回收。
3. 再补真实worker传输失败、DMA期间抢占及实际KV内容对照；符合既定门槛后才接入GPU保护，随后才做性能消融。
