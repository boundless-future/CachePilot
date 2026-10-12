# generation账本与非零保护：后续接线边界

22:34更新：固定参数新文本pilot在selected-one零保护覆盖处停止。默认/调优零保护通过，三格204请求/9,792输出；候选35次capacity回退、提交0，但原请求/资源/generation/回执/计时门槛通过。selected-two与formal未启动，2/4通过、无聚合，不将无覆盖耗时写成收益。历史trace原样复用，68条各多17tokens；静态边界不是容量根因证明。50文件SHA/226源核对与服务器/本地/冻结源报告一致，56项回归通过。下一步独立只读容量/候选窗口诊断，不改reserve或输入追求通过。见 [反例报告](experiments/2026-10-12/GENERATION_STRATEGY_VALIDATION.md)。

22:16更新：新generation＋保护四组轮转三重复formal 12/12通过，816测量请求/39,168输出tokens。one/two TTFT P95均值相对selected-zero −4.74%/−3.34%；two在r2退化，所有格prefill24,156、H2D1,811,939,328 B相同，不宣称稳定或泛化收益。one提交4/4/4、two4/4/3，69代/格及保护资源闭合。183原始文件SHA核对、224冻结源核对及服务器/本地/冻结源三报告一致；47项测量/入口/审计回归通过。下一步固定参数的新文本覆盖准入，pilot全通过才formal；无覆盖则停止并记录触发局限。见 [正式报告](experiments/2026-10-12/GENERATION_STRATEGY_FORMAL.md)。

21:49更新：新组合无逐步探针策略 pilot 四格通过，272测量请求。候选与调优零保护统一 h5/d1；one/two 实际保护各4批、峰值16/32，69代/格闭合。单次 TTFT P95 相对 selected-zero −2.57%/+10.33%，prefill均24,156，不能宣称稳定收益。a轮入口误导入根目录旧脚本失败保留，b修复路径后221项源冻结；本地独立时间戳/资源/字节复算通过，38测试通过。下一步四组轮转三重复 formal 预注册与入口，保留全部候选，不混入pilot。见 [报告](experiments/2026-10-12/GENERATION_STRATEGY_PILOT.md)。

21:29更新：新组合共享取消/实际共享受害请求抢占两个GPU专项通过联合审计：完整请求各347，另1取消流/4压力取消流；936/504层KV全等，generation348/351精确闭合，引用2→1→0、最终pin0、BlockPool909。共享取消格正常teardown后出现output_handler traceback与CUDA IPC警告，单列保留，不宣称无错误。下一步限定准入汇总及新策略pilot预注册；不直接formal。见 [报告](experiments/2026-10-12/GENERATION_SHARED.md)。

21:21更新：新组合两个独立GPU专项完成：长prefix348请求/1800层KV全等、9次拆分/8条完整四chunk来源链；失败回执348请求/864层全等、1次真实STORE成功后报告失败。各格generation闭合、pins0、BlockPool909；独立复算与原始SHA归档通过。下一步共享取消和实际抢占；本次不是长suffix失败联合验证，也无性能结论。见 [长prefix](experiments/2026-10-12/GENERATION_PROTECTION_LONG_PREFIX.md) 与 [失败回执](experiments/2026-10-12/GENERATION_PROTECTION_FAILED_RECEIPT.md)。

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
