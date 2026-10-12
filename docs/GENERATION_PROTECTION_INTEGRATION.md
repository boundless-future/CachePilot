# generation账本与非零保护：后续接线边界

2026-10-12，匿名结构正式矩阵运行期间的只读代码复核。本文件是下一阶段设计，不代表已经实现或GPU通过。当前服务源码不变，`GenerationLedgerConnector` 的非零预算拒绝继续保留。

## 具体接口缺口

`GenerationManagerBridge.add_store_candidate()` 在原pending admission时建立原wire/range ticket；`on_scheduler_step()`仅将原manager返回的STORE范围从pending转入inflight，并分配原batch identity。`_drop_removed()`要求native policy的每个pending范围都已登记，删除项才可转dropped。

`BoundedProtection.bind()`则将保护`commit()`放在manager的原step之前，之后把保护STORE拼到`actions.stores_to_submit`。`commit()`直接注册原manager batch，删除或拆分policy pending。对于完整保护范围，原bridge可能把已提交的操作误判为drop，并未登记保护inflight；对于前缀保护，原`[0,1024)`被替换成`[512,1024)`，bridge会拒绝未登记的新suffix。随后`GenerationLedgerConnector.build_connector_meta()`从bridge.inflight读取batch身份，也缺保护批次。

这不是“保护算法已被证明不可行”，而是两条分别验收的接线路径尚无共同提交协议。删除零预算guard会跳过必要的不变量，而不是完成支持。

## 拟采用的最小接线范围

在独立opt-in实现中加入明确的保护提交交接：原wire、原pending区间、被保护prefix、保留suffix、原native batch身份必须一起校验。保持GPU pin和真正STORE由已有原manager/native worker负责；新桥只追踪身份与区间，不拥有copy或额外free权限。

1. 准备阶段只保存候选和原身份；零token回滚不提交STORE、不产生terminal或改变LRU语义。
2. 提交前验证原ticket、连续prefix/suffix分区和原wire。提交成功后prefix转inflight，suffix保持pending；不得先删除原ticket，再用key补一个新ticket掩盖来源。
3. 原worker receipt只能完成原batch的prefix。成功后suffix继续默认drain；失败时结合原manager实际移除范围，将suffix转dropped。重复/旧代/错误batch不能释放pin或改变新代ticket。
4. END等prefix原native terminal、suffix终结、lookup/RPC/retrieve闭合后才可提交。异常发生在native注册或policy变更之后时，保留unresolved证据，不伪造回滚成功。

## 验证顺序

先在真实固定版本manager/BlockPool的CPU契约覆盖完整保护、前缀拆分、零步回滚、成功/失败原回执、旧代回执、idle-finalize与连续抢占。额外覆盖“注册成功后抛异常”和“policy变更后抛异常”，明确哪些状态只能隔离实例重建。

之后独立GPU诊断：zero/one/two各自的STORE→原terminal→H2D来源、完整KV bitwise、精确资源回收、共享取消/自然抢占和长prefix成功/失败。既有保护栈的成功不能替代新组合栈验收，原失败保留。最后关闭诊断日志、新签名pilot、同签名三重复；必须在能实际触发保护的固定负载上与调优zero比较。

优先完成当前匿名结构formal，按完整结果决定新组合策略实验是否值得投入。不为制造保护收益削减reserve、扩大预算或改变输入而仍沿用原实验名称。模型/硬件扩展、上游PR与该接口任务分别记录。
