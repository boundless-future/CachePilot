# CachePilot 当前状态

2026-10-20最新：原生deadline匹配pilot 2/2、同304项运行时冻结源formal 6/6均已完成，正式360请求/17,280输出tokens；不得重复启动。deadline=0.5相对0的三轮TTFT P95变化−17.36%/−12.14%/−8.13%，各格P95均值−12.58%；computed prefill−18.24%，H2D/D2H+8.62%/+11.83%。这是生命周期适配层上的原生配置基线，自定义保护收益仍未成立。192项正式证据SHA、服务器/本地/冻结源及归档审计一致；相关离线测试两侧各18通过，输出差异与关闭warning保留。source1三段STORE与确认CPU回载相交384/2,048/256tokens，候选观测时请求活跃、block ref=1且无free-rank，暂不采用finished/free-only筛选。下一步在已有全轨迹记录中找在线可区分的卸载选择信号，再按门槛决定是否实现。详见[正式报告](experiments/2026-10-19/NATIVE_DEADLINE.md)与[主线](MAINLINE.md)。23:52:59空闲快照确认服务停止、GPU1MiB/0%；本次离线续推未启动新GPU实验，容器保持开机。以下为历史检查点。

2026-10-19：接续3C净效果主线，默认关闭的无候选早退通过真实栈42项；同原轨迹zero/legacy-two/filtered-two pilot 3/3、formal 9/9。filtered相对legacy的prepare累计wall每轮减少73.67–74.78%，TTFT P95三轮均快0.90–1.19%，单轨迹描述性结果；filtered-r1关闭期traceback按既有分类保留。filtered与legacy重算/搬运在r1/r2相同，r0有变化，不证明P95改善由扫描省时造成。571文件证据服务器/本地/298项冻结源码复算及SHA全部通过；服务空闲、GPU1MiB/0%、容器开机。唯一下一步是用确定request ID对齐保护批次、vLLM GPU prefix命中、LMCache CPU回载和重算。见[净效果主线](experiments/2026-10-19/NET_EFFECT.md)与[当前执行主线](MAINLINE.md)。以下为历史记录。

## 2026-10-13 工程收敛

运行时主线已经迁入 `src/cachepilot`，旧 `scripts` 路径保留兼容身份；统一配置、CLI、源码快照和安装产物已建立。Windows回归663 passed，固定Linux环境隔离回归1147 passed、9 skipped、115 subtests passed。此轮没有启动新的性能cell，也没有改变既有保护/回执语义。模块结构和下一步顺序见 [ARCHITECTURE](ARCHITECTURE.md)，执行记录见 [ENGINEERING_CONSOLIDATION](ENGINEERING_CONSOLIDATION.md)。

2026-10-13 14:26，用户授权持续推进至14:30：匿名结构共享候选pilot完成但仅 **3/4通过**（双chunk0提交），原60请求/格、资源与generation闭合；formal未启动，禁止重跑筛选。独立只读windows、实际allocation及native逐次准入诊断各2格闭合，均不是性能；native one/two分别2,901/2,902次调用成功、91/85次拒绝，逐调用需求、新分配与空闲命中touch闭合。最终相关回归 **远端33项、本地19项通过**（范围不同，远端包含6项真实Scheduler序列测试）；首次错误预期及原始失败日志保留。原pilot与三组诊断证据均完成服务器/本地/冻结源复算。完整进度见[当日记录](experiments/2026-10-13/PROGRESS.md)，[准入报告](experiments/2026-10-13/SHARED_STRUCTURE_NATIVE.md)、[边界设计](experiments/2026-10-13/NATIVE_ADMISSION_BOUNDARY.md)。当前仍是3C策略评估与覆盖限制诊断，收益未成立；未降低reserve、未启动formal，联合异常/完整恢复/生产门槛继续开放。以下旧日期记录均为历史。

2026-10-13最新（用户授权“推进下一阶段”）：共享候选历史输入pilot **4/4**、同240项冻结源formal **12/12**完成。正式816请求/39,168输出tokens，one/two各4/4/4批且回执/69代END/资源闭合。TTFT P95均值相对selected-zero **−1.52%/−1.58%**，two在r1退化；所有格prefill24,240、H2D1,811,939,328 bytes相同，尚无稳定收益或减少重算的证明。pilot/formal共258原始文件SHA及服务器/本地/冻结源复算一致；本地37项、远端27项范围不同单列。输出差异、default-zero-r2关闭traceback及CUDA IPC警告保留。下一主线：既有六session匿名结构的共享候选覆盖pilot，全部通过才formal；联合异常/完整抢占恢复仍开放。见 [正式报告](experiments/2026-10-13/SHARED_STRATEGY_FORMAL.md)。服务器已空闲、容器保持开机。以下旧日期记录是历史状态，不能代替本条。

2026-10-13更新（用户授权推进1–3）：共享前缀独立服务接线、正常/异常GPU限定准入完成。最终显式CUDA/native **961 passed、115 subtests**。正常zero/one/two共1,044请求、720/720/864层KV全等；取消、自然抢占、失败回执、raw ID重用四专项通过。历史新文本全量KV探针两轮零覆盖、稀疏探针缺回载的失败均保留；轻量诊断3次共享保护，独立323请求尾段补齐1批/36层先共享再保护的严格STORE→H2D。全局资源/代际闭合、pin0、909free；七格及补充两轮服务器/本地/冻结源复算一致，305原始文件SHA核对。**不是新性能结论**，未运行pilot/formal；同一先共享再保护批次与取消/抢占等的联合GPU覆盖、完整抢占恢复、异步/多worker仍开放。下一主线：冻结新四组历史输入pilot，全部通过后才formal。详见 [本轮准入报告](experiments/2026-10-13/SHARED_PREFIX_ADMISSION.md)。

23:17停机交接：今日实验已收尾，无在途实验/推理服务，端口空闲、GPU1MiB/0%；231源未漂移，代码已推送，heartbeat保持PAUSED。等待用户开机续授；下一步独立共享前缀Connector接线，不能直接启动性能实验。见 [停机交接](experiments/2026-10-12/SHUTDOWN.md)。

23:14更新：共享前缀独立引用CPU候选完成，真实BlockPool/manager/generation相关84项通过（新增29项），本地源码差异约束2项通过。允许共享完整哈希块增加自己的保护引用；rollback只恢复有原free-rank的归零块，原回执释放不改。成功/失败/逆序回执、其他owner引用变化、旧代/多候选部分注册故障均有契约。原230源不变，仅新增candidate；模型服务bind仍拒绝，无新GPU/收益结论。下一步独立Connector接线及callback契约，再共享KV bitwise/资源准入。见 [契约报告](experiments/2026-10-12/SHARED_PREFIX_CONTRACT.md)。

23:01更新：独立容量/候选窗口GPU诊断两格通过，136测量请求，开发保护4批、新文本0批。新文本存在203次容量/预算满足的候选观测，其中147次有压力排名且全部因共享首块ref>0拒绝，另56次无压力位置。实际tokenizer确认新文本首16tokens仅1种、开发20种。不能把零覆盖仅归因reserve过大；下一步共享前缀引用安全契约，不能直接删除ref检查。230源/36原始文件SHA核对、三处审计一致；真实契约34项及本地5项通过。见 [诊断报告](experiments/2026-10-12/GENERATION_CAPACITY_DIAGNOSTIC.md)。

22:34更新：固定参数新文本pilot在selected-one零保护覆盖处停止。默认/调优零保护通过，三格204请求/9,792输出；候选35次capacity回退、提交0，但原请求/资源/generation/回执/计时门槛通过。selected-two与formal未启动，2/4通过、无聚合，不将无覆盖耗时写成收益。历史trace原样复用，68条各多17tokens；静态边界不是容量根因证明。50文件SHA/226源核对与服务器/本地/冻结源报告一致，56项回归通过。下一步独立只读容量/候选窗口诊断，不改reserve或输入追求通过。见 [反例报告](experiments/2026-10-12/GENERATION_STRATEGY_VALIDATION.md)。

22:16更新：新generation＋保护四组轮转三重复formal 12/12通过，816测量请求/39,168输出tokens。one/two TTFT P95均值相对selected-zero −4.74%/−3.34%；two在r2退化，所有格prefill24,156、H2D1,811,939,328 B相同，不宣称稳定或泛化收益。one提交4/4/4、two4/4/3，69代/格及保护资源闭合。183原始文件SHA核对、224冻结源核对及服务器/本地/冻结源三报告一致；47项测量/入口/审计回归通过。下一步固定参数的新文本覆盖准入，pilot全通过才formal；无覆盖则停止并记录触发局限。见 [正式报告](experiments/2026-10-12/GENERATION_STRATEGY_FORMAL.md)。

21:49更新：新组合无逐步探针策略 pilot 四格通过，272测量请求。候选与调优零保护统一 h5/d1；one/two 实际保护各4批、峰值16/32，69代/格闭合。单次 TTFT P95 相对 selected-zero −2.57%/+10.33%，prefill均24,156，不能宣称稳定收益。a轮入口误导入根目录旧脚本失败保留，b修复路径后221项源冻结；本地独立时间戳/资源/字节复算通过，38测试通过。下一步四组轮转三重复 formal 预注册与入口，保留全部候选，不混入pilot。见 [报告](experiments/2026-10-12/GENERATION_STRATEGY_PILOT.md)。

21:29更新：新组合共享取消/实际共享受害请求抢占两个GPU专项通过联合审计：完整请求各347，另1取消流/4压力取消流；936/504层KV全等，generation348/351精确闭合，引用2→1→0、最终pin0、BlockPool909。共享取消格正常teardown后出现output_handler traceback与CUDA IPC警告，单列保留，不宣称无错误。下一步限定准入汇总及新策略pilot预注册；不直接formal。见 [报告](experiments/2026-10-12/GENERATION_SHARED.md)。

21:21更新：新组合两个独立GPU专项完成：长prefix348请求/1800层KV全等、9次拆分/8条完整四chunk来源链；失败回执348请求/864层全等、1次真实STORE成功后报告失败。各格generation闭合、pins0、BlockPool909；独立复算与原始SHA归档通过。下一步共享取消和实际抢占；本次不是长suffix失败联合验证，也无性能结论。见 [长prefix](experiments/2026-10-12/GENERATION_PROTECTION_LONG_PREFIX.md) 与 [失败回执](experiments/2026-10-12/GENERATION_PROTECTION_FAILED_RECEIPT.md)。

21:11更新：真实模型服务提交后主机异常专项完成：a缺失败snapshot不通过；将只读证据前移至异常捕获后，b证明EngineCore/API自行退出、16个保护引用未误回滚、目标未END、未完成票据落盘。本地独立复算通过；服务器879 passed/115 subtests，本地审计18 passed单列。下一步长prefix、取消抢占、失败回执GPU专项；无性能或自动恢复结论。见 [服务故障报告](experiments/2026-10-12/GENERATION_PROTECTION_SERVICE_FAULT.md)。

21:04更新：新组合正常GPU诊断zero/one/two三格通过，共1,044请求，KV层比较720/720/864全部bitwise相等；one/two保护STORE11/9，最终pins为零、BlockPool各909 free，348代/格精确END。a轮缺observer状态证据失败保留；b原始文件与服务器字节一致，本地独立重算通过。下一步实际EngineCore故障退出/终态snapshot及长prefix、取消抢占、失败回执；无性能结论。见 [GPU报告](experiments/2026-10-12/GENERATION_PROTECTION_GPU.md)。

20:52更新：真实双候选正常/第二次注册前后失败契约及shutdown失败证据保存完成；全套显式CUDA/native **878 passed、115 subtests**。首轮fixture sequence冲突失败已保留，修复测试身份后通过。下一步独立服务GPU诊断及真实EngineCore故障退出验证，尚无新组合模型服务/bitwise结果。见 [报告](experiments/2026-10-12/GENERATION_PROTECTION_FAULTS.md)。

20:38更新：独立 `GenerationProtectionConnector` 接线与真实native metadata/worker envelope/receipt契约完成；最终显式CUDA/native全套 **873 passed、115 subtests，无跳过**。新增12项接线测试。尚无新组合的模型服务、GPU bitwise或策略性能结论；下一步多候选部分失败/进程退出证据门槛和独立GPU诊断入口。见 [接线报告](experiments/2026-10-12/GENERATION_PROTECTION_WIRING.md)。

19:29更新：generation＋非零保护独立 CPU 候选实现，真实 manager/BlockPool 回归 **60 passed**（新增组合18项）；包括 prefix/suffix lineage、跨代回执和部分提交后拒绝误回滚。尚未接入服务 Connector、无新 GPU 或性能结论；下一步为独立服务接线及回调故障门槛，见 [契约报告](experiments/2026-10-12/GENERATION_PROTECTION_CONTRACT.md)。服务器已增加候选脚本，不再是历史210项冻结目录。

更新：2026-10-12 18:50。逐步执行记录见 [今日推进记录](experiments/2026-10-12/PROGRESS.md)，完整阶段与未完成项见 [TASKS](TASKS.md)。本页区分实现、正确性范围和性能证据，历史日期的“下一步”不能代替当前计划。

## 项目定位与实现

CachePilot 是 vLLM＋LMCache 的外部实验 Connector 和小预算 KV 卸载策略：在真实 scheduler 的分配前，复核即将面临驱逐的已结束请求前缀，最多保护一个或两个完整 chunk；由原 manager 提交 STORE，并在原身份回执后释放保护 pin。单个物理 block 为 16 tokens，一个 LMCache chunk 为 256 tokens。它不是新推理引擎，也没有重新实现 GPU copy kernel。

原 token/reservation、请求 generation、native terminal、取消和失联回收构成单独的所有权适配支线。性能对照共用该数据通路，不能把它的修复算作保护策略收益。空闲 pending STORE 的 15 秒宽限收尾是另一默认关闭的候选机制；立即丢弃导致 H2D=0 的反例保留。

## 当前阶段

六session匿名结构GPU观测及无额外采样pilot各4/4通过，共480请求/23,040输出tokens；观测见94.94–99.67% KV峰值及lazy自然抢占，两轮资源闭合。三处独立审计通过，formal十二格已完成，720请求/34,560输出tokens，native TTFT P95均值1.893秒，immediate/default/selected相对+5.76%/+11.33%/+9.69%，见 [正式报告](experiments/2026-10-12/REAL_STRUCTURE_FORMAL.md) 与 [结构报告](experiments/2026-10-12/REAL_STRUCTURE_GPU.md)。服务器保留210项冻结源；下一步为 [generation与非零保护交接](GENERATION_PROTECTION_INTEGRATION.md) 的契约与正确性准入。

最新：generation ledger 真实接线和独立GPU诊断通过；新栈长 prefill pilot 4/4、formal 12/12，816正式请求经服务器/本地独立审计通过。立即卸载/default/selected 的TTFT P95为15.559/17.210/19.611秒，native为21.266秒；整套零保护配置对照，不评价保护收益。输出hash差异保留。请求计时、分阶段和搬运字节分析已归档，见 [正式报告](experiments/2026-10-12/GENERATION_LONG_PREFILL.md)。原decode192 pilot在立即卸载两个session残留处停止；只读观察定位END之后末批STORE重建session。独立原回执后END候选GPU诊断68请求/144 reservation/69 lookup/101 transfer及BlockPool909 free通过，新冻结四配置pilot 4/4通过，272请求/52,224输出tokens，三处独立复算一致；同签名formal十二格已完成，816请求/156,672输出tokens，native TTFT P95均值50.783秒；immediate/default/selected相对为+4.24%/+5.85%/+2.29%，三处复算一致，见 [正式报告](experiments/2026-10-12/DECODE_RECEIPT_FORMAL.md)。见 [诊断](experiments/2026-10-12/DECODE_IMMEDIATE_DIAGNOSIS.md) 与 [新预注册](experiments/2026-10-12/DECODE_RECEIPT_PREREGISTRATION.md)。最新全套834 passed、9 CUDA显式跳过、115 subtests；显式CUDA另9 passed，分开执行。

| 阶段 | 已完成 | 仍开放 |
|---|---|---|
| P0/P1 环境与接入 | 4090 24GB、Qwen3-4B/BF16、vLLM 0.30.0、锁定 LMCache 研究版、CUDA 13.0，实际 D2H/H2D 验收 | 第二模型/硬件控制实验 |
| P2/3A 观测与候选 | 固定 trace、profiling、分配与 lookup 诊断；六点固定参数开发调优 24 格完成，选中 h5.0/d1 | 固定选择的新文本验证未通过覆盖门槛；更多到达分布、耗时分解 |
| 3B 所有权支线 | 受控单卡服务链路、原身份 native terminal、L2/取消/失联和资源审计；满足限定 3C 准入 | 真实 driver/DMA 故障、永久 I/O 自动恢复、多 worker/异步范围 |
| 3C 保护策略 | 零/一/两 chunk、预算与物理容量回退、共享取消/抢占、raw ID 跨代、长前缀拆分和受控 host exception；首轮正式消融 | 生产级全面正确性、可泛化收益结论 |
| P4 复核 | 首轮新文本/容量重复、新栈 2.5 GiB 九格、固定参数 24 格、无保护 reference 12 格和边界专项 | 原新文本因 one 零保护覆盖停止；到达压缩 formal 已完成；原长 prefill 失败保留；新栈长 prefill formal 12格已完成；原decode192 pilot失败；独立立即卸载候选通过诊断及新pilot 4/4；formal十二格完成；真实结构观测与无采样pilot各4/4完成，formal十二格已完成 |
| P5 展示/上游 | 原始证据持续归档 | 最终复现入口、整体图表、最小可维护上游补丁评估 |

## 能支持的结论

| 对照 | 当前观察 | 解释边界 |
|---|---|---|
| 2 GiB 开发 trace，zero/one/two 各三次 | one/two 的 TTFT P95 均值相对 zero 为 -6.22%/-3.45% | [首轮消融](experiments/2026-10-11/PROTECTION_ABLATION.md)；n=3，未证明稳定通用收益 |
| 相同到达结构、新文本验证，各三次 | -0.57%/+0.31%，未稳定复现改善 | [独立输入](experiments/2026-10-11/PROTECTION_ABLATION_VALIDATION.md)；不是新分布或真实 Agent 泛化 |
| 1.5 GiB 容量，各三次 | -5.09%/-3.99%；H2D 增加 8.92%/6.49% | [容量扫描](experiments/2026-10-11/CAPACITY_SCAN.md)；搬运代价与波动一并报告 |
| 原 2.5 GiB 试点 | zero 因 owned job/session 残留失败，后续格未运行 | 原失败永久保留；独立诊断定位空闲未提交 STORE |
| 新栈 2.5 GiB 正式重复 | 九格 612 请求通过，one/two TTFT P95 均值 -3.07%/-2.00% | [正式报告](experiments/2026-10-11/IDLE_CAPACITY.md)；第一组重复退化，输出差异保留，pilot 不进入正式均值 |
| 同栈六点固定参数开发调优 | 24 格、1,632 请求通过；h5.0/d1 均值最低，one/two 相对它 +0.88%/-0.19% | [调优报告](experiments/2026-10-11/FIXED_BASELINES.md)；差异小、波动大，未证明保护优于调优固定策略 |
| 固定选择后的新文本验证 | 前两格通过；one 的 8 次候选均容量回退，保护 0/0，矩阵按规则停止 | 请求和资源通过、H2D 存在；整体未完成，无性能聚合，不重跑筛选 |
| 无保护 reference 四组 | 12 格、816 请求全部通过；native TTFT P95 6732.95 ms，h5.0/d1 7187.49 ms | [独立报告](experiments/2026-10-11/UNPROTECTED_REFERENCES.md)；整套配置对照，不评价保护策略 |
| 到达时间减半 | 12 格、816 请求通过；native 9503.55 ms，其余三组高 3.47–5.81% | [到达报告](experiments/2026-10-11/ARRIVAL_COMPRESSION.md)；缓存组少算 prefill，但整体及回访标签组均未改善 |
| 零覆盖单次独立诊断 | 9 个首次容量回退，预算充足、在途 pin 为零，之后默认 STORE；保护仍 0/0 | [诊断](experiments/2026-10-11/COVERAGE_DIAGNOSTIC.md)；安全预留限制触发范围，未修改 reserve |
| 长 prefill pilot | 前两格通过；默认 fixed 68 请求后在空闲身份预检 fatal，剩 1 owned job/11 sessions | [失败报告](experiments/2026-10-11/LONG_PREFILL.md)；2/4 格、无均值，选中 fixed 与 formal 均未运行 |

各性能组仍存在输出 hash 差异；固定输入、48 decode token、HTTP 200 或资源闭合不等于输出完全确定。独立诊断曾验证多组回载 KV bitwise 全等，但不能代替所有性能请求的逐层正确性证明。编译相关输出差异根因仍开放。

## 晚间新增正确性证据

- 空闲收尾后相同 raw ID 的 68 对 generation：旧 reclaim 均早于新 lookup；包括实际 idle-dropped 请求重入。只覆盖旧代完整结束后的复用。
- 失败回执与共享取消组合分别通过 864/936 层 KV 比较和资源审计，但 idle drop=0，只算兼容性。
- 延迟回执组合有实际 idle drop 且资源回收，但恢复命中仍在 GPU、H2D 未覆盖，整体失败。详见 [组合矩阵](experiments/2026-10-11/IDLE_RECEIPT_MATRIX.md)。
- 2026-10-11 当时完整 CUDA/native 回归 746 passed、115 subtests；之后新增离线身份审计 5 项 CPU 测试单列。新增默认关闭的 generation/session ledger 及显式 client 边界，服务器专项 39 passed（含 22 项新增账本/边界/真实库手动交接测试）；原失败路径仍可复现。真实 tracker/manager 交接验证 A/B 分代 END，但 terminal/seal 由测试交付，尚未接入生产 adapter，不能算长 prefill 修复。

## 接下来按顺序推进

1. 完成采用ImmediateReceiptConnector的新decode192四配置pilot，独立复核后另行12格formal；旧immediate失败不改写。14:55停止启动GPU格，15:00汇报。
2. 延续长 prefill 已完成的请求计时、48/8/12阶段和staging字节分析，对decode192执行同口径复核；不包含非零保护。
3. 评估真实结构压力与后续集中恢复实验是否有额外信息，先独立设计再pilot/formal。原固定选择的新文本保护验证仍因覆盖不足停止，不重跑筛选。
4. 整理可复现入口、版本边界和项目贡献；真实 driver/DMA fault、多 worker/异步与永久 I/O 自动恢复仍开放。

2026-10-11 的 22:00/23:24 收尾是历史记录，见 [收尾记录](experiments/2026-10-11/LEDGER_CLOSEOUT.md)。今天服务器已恢复，当前实验状态以上述新记录为准。
