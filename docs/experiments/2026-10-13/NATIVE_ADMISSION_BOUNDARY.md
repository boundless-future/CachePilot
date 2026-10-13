# 下一版策略前置：原生准入边界

2026-10-13。状态：固定版源码审计、真实单调用容量契约、两格只读模型服务诊断与真实Scheduler序列契约完成。见 [逐次准入报告](SHARED_STRUCTURE_NATIVE.md)。**未实现新的pin策略、未降低reserve、没有恢复formal资格。**

## 为什么先研究这个位置

原匿名结构pilot的双chunk没有提交，后续独立窗口诊断合格窗口稀少。实际分配诊断确认原完整live footprint reserve常大于整个909块池，但gross新分配不能反映缓存命中touch和原生in-flight保留。本轮应先建立对原生准入的准确解释，再判断是否值得新策略。

目前的保护在schedule最前面执行：此时知道所有live请求，却尚不知道本步到底准入哪些waiting请求、GPU命中多少、外部命中多少，因此使用极保守的全footprint预算。vLLM随后到allocate_slots时已有本次请求、本地命中、外部命中、计算token和native reservations；这是更精确的观测位置，但不能因为信息更精确就直接pin。

## 固定安装版的顺序

依据服务器实际site-packages源码，SHA与原bounded helper固定值一致，不假设其他vLLM版本相同：

1. scheduler确定GPU前缀命中和connector外部命中。异步回载可以令num_new_tokens=0，却仍传入正的num_external_computed_tokens。
2. 对异步回载，scheduler先计算其他in-flight prefill的剩余reservation，再传给allocate_slots。
3. manager可先做full_sequence_must_fit检查；该门槛与随后本次chunk需求不同。
4. manager处理skipped blocks后，用coordinator计算最终需求。当前单FullAttentionManager下，需求包括需要的新块和本地命中中ref=0、需要从free队列拿走的块。
5. 最终检查是 `required + watermark <= free - reserved_blocks`。通过才touch本地命中、给外部KV分配物理块、补齐计算位置。
6. 异步load时delay_cache_blocks只推迟缓存发布，不取消前面已经发生的物理分配；scheduler把请求转到WAITING_FOR_REMOTE_KVS。

读取范围：scheduler.py约1026/1134–1216及_request_remaining_blocks/_inflight_prefill_reserved_blocks；kv_cache_manager.py 468–590；single_type_kv_cache_manager.py 177–399；block_pool.py get_new_blocks/touch/free_blocks。完整原文件及SHA另存证据包，按固定版行号理解。

## 已通过的真实容量契约

使用实际KVCacheManager、FullAttentionManager、BlockPool及Request，32块可用CPU元数据池；不运行模型/kernel。

| 情形 | 原生需求/空闲消费 | get_new_blocks成功数 |
|---|---:|---:|
| 0计算token，64外部tokens | 4 | 4 |
| 64token GPU命中、命中块ref=0，再计算1token | 5 | 1 |
| 相同命中块仍有其他owner，再计算1token | 1 | 1 |
| 32本地+32外部tokens，0计算 | 4 | 2 |
| 正在运行请求从16增长到17tokens | 1 | 1 |
| 原生reserved或watermark只剩3块、需求4 | 拒绝，0消费 | 0 |
| 完整序列需5块、当前free2、下一chunk仅1块 | 开启full-fit拒绝；关闭后接受 | 0/1 |

8项通过（两种owner、两类headroom分参数计数）。首次测试接口返回值错误3失败，修正后通过；测试a/b日志保留。该表只说明原生单次调用的容量语义，不是端到端吞吐结论或下一步需求预测。

## 已补的真实多调用反例

`test_native_admission_sequence_contract.py`另4项通过，使用相同真实manager：

- free5时依次准入需要2块、3块的两个外部回载，原序列都通过；额外占1块后，第一个仍通过、第二个被拒绝。这说明仅保证“当前请求仍能准入”不足以保持原调度序列。
- 额外pin恰好位于本地命中块时，native需求从5降至4，包含pin在内总消费仍5，而非6；不能将旧需求加预算直接当精确需求。
- 保护1个前缀块后分配其他空闲块，再释放所有引用，free恢复32，但原64token缓存只剩16token连续命中。回滚引用不恢复已驱逐hash或调度历史。

这些是边界反例，不是CachePilot实际服务已发生相同退化的证明；测试未提交保护STORE，也不覆盖原批次回执。源文件和原始4 passed日志纳入边界契约证据包。

## 不能跳过的四个设计约束

**整步资源不能由单次调用代表。** 本次准入完成后，还可能轮到其他waiting/running请求；本次有剩余空间不代表整个schedule能保持原请求集与推进能力。native保留与watermark不可扣掉或重复计算。

**保护会改变需求本身。** 如果待保护块也是本地GPU命中，touch使其ref从0变1，native计算的evictable需求相应减少。共享块只增加引用、不消耗free，但owner可能在之后离开。不能简单将预算16/32全部当新增free消费，也不能用先前快照重复扣除。

**零token不能提交当前保护STORE。** 现有worker路径在没有计算时不能随意发送新保护批次，PREPARED要按原协议rollback；若在原生分配中临时pin，会改变分配/抢占结果，rollback恢复引用不代表恢复整个调度历史。不能把“最后资源相同”当作没有行为差异。

**原有交接必须保持。** 前缀hash/token完整证明、generation身份、prefix/suffix ticket、native注册、原批次回执、异常后pin所有权都需要原样保留。COMMITTED之后不能因为后续准入失败而回滚。

## 具体后续工作及退出门槛

1. 已完成本轮native调用只读账本：每次native需求与新分配+touch逐调用闭合，原reserve/原生reserved/watermark分开记录；one/two各35项通过。这是单调用事实，不是安全额外pin预算。
2. 已补同初态两调用/命中重叠/驱逐不可逆反例；真实Scheduler已覆盖独立冷prefill、共享前缀、异步外部回载及reservation。继续构造完整scheduler对照序列，覆盖GPU/外部混合命中、共享块的owner离开及完整序列门槛；对比额外保护引用存在时下一次和后续准入，而非只比较free总数。
3. 只有能表达清楚“哪些块可被临时引用、如何保留未来准入能力、零token如何安全退回”的契约后，才独立实现新的opt-in候选。初版仍同步/单FullAttention/TP1；不在原SharedPrefixProtection上直接改系数。
4. 新候选需先CPU真实库/异常契约，再独立GPU KV bitwise与资源准入，再固定开发pilot及新输入验证。原3/4失败、旧formal和本轮诊断不可混算；不使用诊断结果选择性删输入或调参数后宣称泛化。

若新接入点不能在不影响原生推进能力的前提下得到足够窗口，应明确把当前保护机制列为适用范围受限的研究结果，保留可复现基线、所有权正确性和原生压力观测成果；不无限重复相同性能实验。
