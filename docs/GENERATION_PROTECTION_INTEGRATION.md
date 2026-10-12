# generation账本与非零保护：后续接线边界

21:11更新：真实模型服务提交后主机异常专项完成：a缺失败snapshot不通过；将只读证据前移至异常捕获后，b证明EngineCore/API自行退出、16个保护引用未误回滚、目标未END、未完成票据落盘。本地独立复算通过；服务器879 passed/115 subtests，本地审计18 passed单列。下一步长prefix、取消抢占、失败回执GPU专项；无性能或自动恢复结论。见 [服务故障报告](experiments/2026-10-12/GENERATION_PROTECTION_SERVICE_FAULT.md)。

21:04更新：新组合正常GPU诊断zero/one/two三格通过，共1,044请求，KV层比较720/720/864全部bitwise相等；one/two保护STORE11/9，最终pins为零、BlockPool各909 free，348代/格精确END。a轮缺observer状态证据失败保留；b原始文件与服务器字节一致，本地独立重算通过。下一步实际EngineCore故障退出/终态snapshot及长prefix、取消抢占、失败回执；无性能结论。见 [GPU报告](experiments/2026-10-12/GENERATION_PROTECTION_GPU.md)。

20:52更新：真实双候选正常/第二次注册前后失败契约及shutdown失败证据保存完成；全套显式CUDA/native **878 passed、115 subtests**。首轮fixture sequence冲突失败已保留，修复测试身份后通过。下一步独立服务GPU诊断及真实EngineCore故障退出验证，尚无新组合模型服务/bitwise结果。见 [报告](experiments/2026-10-12/GENERATION_PROTECTION_FAULTS.md)。

20:38进展：独立Connector候选已接线，873项显式CUDA/native回归通过；旧zero类限制保留。CPU回调与底层CUDA契约不能替代组合服务GPU验收。下一步多候选部分提交、故障进程停止/证据保存及独立GPU审计入口，见 [接线报告](experiments/2026-10-12/GENERATION_PROTECTION_WIRING.md)。

19:29进展：已实现独立 CPU 候选并通过60项真实库回归（18项新组合），见 [契约报告](experiments/2026-10-12/GENERATION_PROTECTION_CONTRACT.md)。原设计中的 prefix/suffix/receipt 已有测试入口；服务接线和 GPU 准入未完成。native 注册后通用 rollback 的误释放窗口新增为必测支线；候选 poison 后保留 pin 并拒绝回滚，必须停止实例，不能宣称自动恢复。

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
