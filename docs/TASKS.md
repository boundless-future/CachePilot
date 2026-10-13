# 待办与阶段验收

2026-10-13 工程收敛：主线已迁入 `src/cachepilot`，旧入口保留模块身份兼容；
统一配置/CLI、安装元数据、源码快照及模块边界测试已建立。隔离目录真实库回归通过
1147项、9项跳过、115个subtests；这不是新的GPU性能实验。
逐步实测、已知限制和下一步见 [工程收敛记录](ENGINEERING_CONSOLIDATION.md) 与
[模块架构](ARCHITECTURE.md)。后续按模块修改：候选/预算与共享引用 → 服务所有权和终结 → generation回执 → vLLM接线 → 观测和实验。
开放：历史脚本分批分类、策略接口进一步解耦、联合异常与完整抢占恢复、异步/多worker支持和收益验证。

2026-10-13 14:26，用户授权持续推进至14:30：匿名结构共享候选pilot完成但仅 **3/4通过**（双chunk0提交），原60请求/格、资源与generation闭合；formal未启动，禁止重跑筛选。独立只读windows、实际allocation及native逐次准入诊断各2格闭合，均不是性能；native one/two分别2,901/2,902次调用成功、91/85次拒绝，逐调用需求、新分配与空闲命中touch闭合。最终相关回归 **远端33项、本地19项通过**（范围不同，远端包含6项真实Scheduler序列测试）；首次错误预期及原始失败日志保留。原pilot与三组诊断证据均完成服务器/本地/冻结源复算。完整进度见[当日记录](experiments/2026-10-13/PROGRESS.md)，[准入报告](experiments/2026-10-13/SHARED_STRUCTURE_NATIVE.md)、[边界设计](experiments/2026-10-13/NATIVE_ADMISSION_BOUNDARY.md)。当前仍是3C策略评估与覆盖限制诊断，收益未成立；未降低reserve、未启动formal，联合异常/完整恢复/生产门槛继续开放。以下旧日期记录均为历史。

2026-10-13最新（用户授权“推进下一阶段”）：共享候选历史输入pilot **4/4**、同240项冻结源formal **12/12**完成。正式816请求/39,168输出tokens，one/two各4/4/4批且回执/69代END/资源闭合。TTFT P95均值相对selected-zero **−1.52%/−1.58%**，two在r1退化；所有格prefill24,240、H2D1,811,939,328 bytes相同，尚无稳定收益或减少重算的证明。pilot/formal共258原始文件SHA及服务器/本地/冻结源复算一致；本地37项、远端27项范围不同单列。输出差异、default-zero-r2关闭traceback及CUDA IPC警告保留。下一主线：既有六session匿名结构的共享候选覆盖pilot，全部通过才formal；联合异常/完整抢占恢复仍开放。见 [正式报告](experiments/2026-10-13/SHARED_STRATEGY_FORMAL.md)。服务器已空闲、容器保持开机。以下旧日期记录是历史状态，不能代替本条。

2026-10-13更新（用户授权推进1–3）：共享前缀独立服务接线、正常/异常GPU限定准入完成。最终显式CUDA/native **961 passed、115 subtests**。正常zero/one/two共1,044请求、720/720/864层KV全等；取消、自然抢占、失败回执、raw ID重用四专项通过。历史新文本全量KV探针两轮零覆盖、稀疏探针缺回载的失败均保留；轻量诊断3次共享保护，独立323请求尾段补齐1批/36层先共享再保护的严格STORE→H2D。全局资源/代际闭合、pin0、909free；七格及补充两轮服务器/本地/冻结源复算一致，305原始文件SHA核对。**不是新性能结论**，未运行pilot/formal；同一先共享再保护批次与取消/抢占等的联合GPU覆盖、完整抢占恢复、异步/多worker仍开放。下一主线：冻结新四组历史输入pilot，全部通过后才formal。详见 [本轮准入报告](experiments/2026-10-13/SHARED_PREFIX_ADMISSION.md)。

23:17停机交接：今日实验已收尾，无在途实验/推理服务，端口空闲、GPU1MiB/0%；231源未漂移，代码已推送，heartbeat保持PAUSED。等待用户开机续授；下一步独立共享前缀Connector接线，不能直接启动性能实验。见 [停机交接](experiments/2026-10-12/SHUTDOWN.md)。

23:14更新：共享前缀独立引用CPU候选完成，真实BlockPool/manager/generation相关84项通过（新增29项），本地源码差异约束2项通过。允许共享完整哈希块增加自己的保护引用；rollback只恢复有原free-rank的归零块，原回执释放不改。成功/失败/逆序回执、其他owner引用变化、旧代/多候选部分注册故障均有契约。原230源不变，仅新增candidate；模型服务bind仍拒绝，无新GPU/收益结论。下一步独立Connector接线及callback契约，再共享KV bitwise/资源准入。见 [契约报告](experiments/2026-10-12/SHARED_PREFIX_CONTRACT.md)。

23:01更新：独立容量/候选窗口GPU诊断两格通过，136测量请求，开发保护4批、新文本0批。新文本存在203次容量/预算满足的候选观测，其中147次有压力排名且全部因共享首块ref>0拒绝，另56次无压力位置。实际tokenizer确认新文本首16tokens仅1种、开发20种。不能把零覆盖仅归因reserve过大；下一步共享前缀引用安全契约，不能直接删除ref检查。230源/36原始文件SHA核对、三处审计一致；真实契约34项及本地5项通过。见 [诊断报告](experiments/2026-10-12/GENERATION_CAPACITY_DIAGNOSTIC.md)。

22:34更新：固定参数新文本pilot在selected-one零保护覆盖处停止。默认/调优零保护通过，三格204请求/9,792输出；候选35次capacity回退、提交0，但原请求/资源/generation/回执/计时门槛通过。selected-two与formal未启动，2/4通过、无聚合，不将无覆盖耗时写成收益。历史trace原样复用，68条各多17tokens；静态边界不是容量根因证明。50文件SHA/226源核对与服务器/本地/冻结源报告一致，56项回归通过。下一步独立只读容量/候选窗口诊断，不改reserve或输入追求通过。见 [反例报告](experiments/2026-10-12/GENERATION_STRATEGY_VALIDATION.md)。

22:16更新：新generation＋保护四组轮转三重复formal 12/12通过，816测量请求/39,168输出tokens。one/two TTFT P95均值相对selected-zero −4.74%/−3.34%；two在r2退化，所有格prefill24,156、H2D1,811,939,328 B相同，不宣称稳定或泛化收益。one提交4/4/4、two4/4/3，69代/格及保护资源闭合。183原始文件SHA核对、224冻结源核对及服务器/本地/冻结源三报告一致；47项测量/入口/审计回归通过。下一步固定参数的新文本覆盖准入，pilot全通过才formal；无覆盖则停止并记录触发局限。见 [正式报告](experiments/2026-10-12/GENERATION_STRATEGY_FORMAL.md)。

21:49更新：新组合无逐步探针策略 pilot 四格通过，272测量请求。候选与调优零保护统一 h5/d1；one/two 实际保护各4批、峰值16/32，69代/格闭合。单次 TTFT P95 相对 selected-zero −2.57%/+10.33%，prefill均24,156，不能宣称稳定收益。a轮入口误导入根目录旧脚本失败保留，b修复路径后221项源冻结；本地独立时间戳/资源/字节复算通过，38测试通过。下一步四组轮转三重复 formal 预注册与入口，保留全部候选，不混入pilot。见 [报告](experiments/2026-10-12/GENERATION_STRATEGY_PILOT.md)。

更新：2026-10-12。未勾选项均未完整完成；历史设计与当前实测环境以 ENVIRONMENT.md 及对应日期的记录区分。

晚间持续推进至北京时间 22:00，最新步骤、运行目录与下一步见 [晚间记录](experiments/2026-10-11/EVENING_PROGRESS.md)。恢复执行前检查服务器进程，避免与在途实验重复。

## 已完成

- [x] 明确项目角色：接入现有推理栈的卸载决策模块。
- [x] 归档相关论文、仓库、数据资料与显存估算。
- [x] 找到 `OffloadPolicy` / `LazyOffloadManager` 接入边界。
- [x] 明确先用 4090 24GB＋Qwen3-4B 验证，5090 32GB＋8B 后续复核。
- [x] 核对外部 Connector 加载条件，保存官方兼容性文档。

## P0：租机前可完成

- [x] 固定 LMCache 候选 commit，核对 lazy-offload 所需的 vLLM scheduler/block-pool hooks。
- [x] 采用独立 Conda 环境，记录 vLLM、torch、CUDA；不采用集成镜像，因此无镜像 digest。
- [x] 对比 LMCache 0.5.5 wheel 与研究 commit；已针对现有 torch/CUDA 重编 native 扩展。
- [x] 少量下载/读取轨迹，统计完整短会话覆盖率；固定来源编号前 20 个文件，原尺寸 8K 覆盖 0/20，缩比筛出 1 个完整 24 轮会话，详见 experiments/2026-09-24/real-source-manifest.json。
- [x] 固定输入输出长度、轮数、种子、时间语义和初始压力点；实现 schema v2、SSE 回放、计时测试和 baseline-experiment.json 实验清单。
- [ ] 确认租机主存、CPU、磁盘、驱动、Docker/SSH 权限及价格。

交付：候选环境表、数据小样本与统计、可执行实验配置。CPU 数据工作尽量在租卡前完成。

## P1：GPU 环境与连接验证（先决条件）

- [x] 记录实际硬件与软件元组，验证 PyTorch CUDA 运算。
- [x] 下载 Qwen3-4B，固定 model/tokenizer revision。
- [x] 单独启动 vLLM，确认模型正常生成。
- [x] 启动 LMCache MP server，再启用外部 `LMCacheMPConnector`。
- [x] 打印实际 Connector 模块路径，确认没有加载到 vLLM bundled 版本。
- [x] 验证冷请求、GPU 热命中、CPU 保存、GPU miss 后 CPU 回载。
- [x] 单条 greedy prompt 的冷/热/回载输出与原生 vLLM 一致；检查布局、传输、worker 错误（不代表全面正确性回归）。
- [x] 确认 lazy-offload 开关和 `EVICTION_AWARE` 分支真的执行。
- [x] 保存固定源码版本、包快照、启动命令、日志与实际 KV 池容量。
- [x] 实测 FIFO 停机遗留会话的 TTL 回收：600 秒仍有 1 个会话，630 秒时为 0；GPU 注册与读写锁均清空。见 experiments/2026-09-24/ttl-result.json。
- [ ] 更广泛的取消/抢占/错误恢复与 EVICTION_AWARE 会话生命周期回归；FIFO 的一次 TTL 实测不代表所有路径已验证。

交付：基线可运行环境和 smoke 证据。仅 import 成功、HTTP 成功或 GPU prefix hit 都不算通过完整验收。

## P2：基线 profiling（决定是否继续）

- [x] A：vLLM 原生前缀缓存；4/12 会话、各 4 轮、三次重复初始测量。
- [x] B：相同 GPU KV 预算下，LMCache 默认延迟卸载；另加入立即卸载对照。
- [x] 固定 horizon=2.5/5.0 两点初测，共 24 个实验单元、768 个测量请求；结果见 experiments/2026-09-24/REPORT.md。
- [x] C：完成六点 horizon/提交上限开发调优，pilot 八格与 formal 24 格通过，按预注册选中 h5.0/d1；新文本保护验证未通过覆盖门槛，未将调优完成写成泛化成功。见 [固定基线报告](experiments/2026-10-11/FIXED_BASELINES.md)。
- [x] 测工作集小于 GPU、超过 GPU 两个区间（GPU KV 人为固定 2 GiB）。
- [ ] 补测超过总缓存区间及更多 GPU KV 容量点。
- [ ] 加入长 prefill 突发、稳定 decode、多轮集中恢复。
- [ ] 分解排队、重算、回载耗时，记录搬运量与调度开销。
- [x] 保存排队/precompute 聚合指标、CPU/GPU 命中和 staging 搬运字节；尚未得到独立 DMA 关键路径分解。
- [ ] 完成多轮自然缓存状态下的输出差异归因；HTTP/SSE/长度检查通过不等于正确性通过。
- [x] 完成同输入/同前缀长度的 KV 探针：GPU 热命中与 CPU 回载逐层逐块 bitwise 一致；受控 4 案例输出一致。
- [x] 2026-09-26 在原始串行多轮序列中复现 2/16 输出差异；探针不改变文本，96 次 chunk 回载、3456 次逐层比较全部 bitwise 一致且参考完整。见 experiments/2026-09-26/README.md。
- [x] Eager 对照：原生/立即卸载/探针三组各 16 条输出完全一致，3456 次 KV 比较通过；后续独立拆分结果见下一项。
- [x] 独立拆分 compile-only 与 graph-only：前者串行 16 请求有 1/16 输出差异，后者为 0/16；两者回载 KV 均 3456/3456 层 bitwise 相同。三配置各四组成对冷/回载 decode 探针均覆盖 188 个 slot、6768 层且相同；仅覆盖非异步串行诊断，差异根因仍未定位。见 [复核](experiments/2026-10-01/NATIVE_TRANSFER_AND_DECODE.md)。
- [x] 增加 EVICTION_AWARE decision ledger，记录 admission、danger depth、emitted、dropped_evicted、提交和前缀摘要。
- [x] 分配前诊断确认异步回载分配与策略压力信号错位：11 个丢弃操作均有 allocator 证据，9 个关联后续相同前缀缺失；不能将丢弃数直接等同于性能损失。见 experiments/2026-09-26/STRATEGY_DIAGNOSIS.md。
- [x] 实现压力阈值自适应 horizon 原型并完成一次压力回放；目前略有退化，尚不作为有效优化。
- [ ] 验证真实结构轨迹上也存在相关现象。
- [x] 完成单个匿名真实结构缩比会话的四配置回放（96 请求，输出一致）；该样本无 GPU 压力，不等于真实压力场景验证。

交付：原始指标、trace、参数扫描与明确瓶颈结论。若默认策略足够好、瓶颈主要在 decode，先调整选题，不直接写新策略。

## P3：策略实现（有证据后开展）

- [x] 选择自适应卸载窗口作为第一个机制；实现 `AdaptiveConnector` 与 `adaptive-trace.json`，保持默认行为可复现。
- [x] 完成高/低压力各一次原型回放；高压力单次略慢，低压力无额外搬运，尚无性能收益结论。
- [ ] 增加策略工厂正式分支与配置，当前仍是外部实验 Connector 原型。
- [ ] 按需增加提交/完成时间、在途字节、步时反馈；区分端到端回执时间与 GPU DMA 时间。
- [ ] 保持前缀闭合、哈希复核、block pin/unpin、失败和 ID 重用语义。
- [ ] 正确性测试覆盖取消、抢占、部分保存、保存失败和资源回收。
- [ ] 比较默认策略、调优固定策略与新策略；配置资源和请求量一致。
- [x] 正式计时前分离 AdaptiveConnector 的策略与逐步 JSON 账本，增加参数校验、重复绑定和异常恢复测试。
- [x] 完成无日志默认/自适应在高低压力下各三次重复，384 请求无错误；压力 P95 均值约 3.686/3.694 秒，未证明新策略收益。
- [x] 实现实际分配计数消融；独立诊断验证 6,494 块分配与消费闭合，异步恢复不重复计数，零 token 步累计到下次原有 drain。
- [x] 完成默认/修正信号/立即卸载的高低压力各三次重复（576 请求）；修正信号压力 P95 改善 2.82%、prefill 减少 6.21%，D2H 增加 5.54%，仍慢于立即卸载。见 experiments/2026-09-26/ALLOCATION_SIGNAL.md。
- [x] 核查分配前需求预算的接入与生命周期边界，记录 worker 零 token 路径不提交 STORE 的限制；设计见 experiments/2026-09-26/PREALLOCATION_DESIGN.md。
- [x] 实现独立准入前需求观测并完成一次 12 会话压力诊断；计算槽估计漏掉异步回载，宽泛异步上界误报过多，当前不进入保护原型。见 experiments/2026-09-26/PREALLOCATION_OBSERVATION.md。
- [x] 在独立 16 会话压力轨迹上比较 `compute_slots`、`lookup_ready`、`lookup_inflight` 与宽泛异步上界；核对 STORE worker 回执并完成分配闭合审计。结论见 experiments/2026-09-26/LOOKUP_SIGNAL_VALIDATION.md：lookup 状态比宽泛上界更有区分度，但仍有大量无效报警，暂不进入提前 pin/准入保护。
- [x] 在第二类压力轨迹和多次重复中复核 lookup 状态信号；取消/抢占/保存失败生命周期另列为独立门槛，尚未全部补齐。
- [x] 完成第二类批量到达轨迹两次重复；`lookup_inflight` 未增加有效提前覆盖，仍有大量无效报警，两个账本分配与 STORE 回执均闭合。见 experiments/2026-09-26/LOOKUP_SIGNAL_REPLICATION.md。
- [x] 单独记录异步回载物理分配与 LMCache lazy-offload 压力信号的时间错位、项目影响及未来上游贡献条件；见 [UPSTREAM_ASYNC_KV_PRESSURE.md](UPSTREAM_ASYNC_KV_PRESSURE.md)。此问题不阻塞现有实验，`compute_slots` 也不是 vLLM 的实际准入逻辑。
- [x] 补充高重叠持续到达轨迹并完成两次重复；逐请求 lookup→allocation 配对和 STORE 回执均闭合，仍有大量无效报警，暂不进入提前保护。见 [HIGH_OVERLAP_LOOKUP_ALLOCATION.md](experiments/2026-09-27/HIGH_OVERLAP_LOOKUP_ALLOCATION.md)。
- [x] 增加并在真实 LMCache 环境通过 registry/policy 生命周期契约测试，覆盖 reset、迟到回执、保存失败和 request-id 重用；见 [LIFECYCLE_CONTRACT.md](experiments/2026-09-27/LIFECYCLE_CONTRACT.md)。
- [x] 完成一次真实客户端断流 smoke：取消后 LMCache 队列/锁归零，后续请求 200；未把 active_sessions 计数解释为回收结论。见 [CANCELLATION_SMOKE.md](experiments/2026-09-27/CANCELLATION_SMOKE.md)。
- [x] 用诊断 Connector 验证 `WAITING_FOR_REMOTE_KVS` 等待期取消：worker/scheduler 完成通知各一次、请求表删除、272 个 block 引用归零且空闲块恢复、follow-up CPU 回载成功。r5-r7 的诊断注入误判和协议冲突另行保留，见 [ASYNC_RETRIEVE_CANCELLATION.md](experiments/2026-09-27/ASYNC_RETRIEVE_CANCELLATION.md)。
- [x] 完成显式抢占的受控诊断：关闭异步调度时恢复与 API 均通过；默认异步调度时 API 返回 500，但生成、block 清理和 follow-up 通过。此结果不覆盖自然抢占、在途 STORE 或重新远端回载。见 [EXPLICIT_PREEMPTION.md](experiments/2026-09-27/EXPLICIT_PREEMPTION.md)。
- [x] 用真实 vLLM/LMCache 服务完成一次“实际 CPU 回载成功后模拟 worker 失败结果”的诊断；272 个错误 block 触发 scheduler 本地重算，输出一致且资源归零。此结果不是实际传输中断，见 [ASYNC_RETRIEVE_FAILURE.md](experiments/2026-09-27/ASYNC_RETRIEVE_FAILURE.md)。
- [x] 用真实 STORE future 完成后模拟失败回执，验证 worker 完成数与失败标记同报、scheduler 清除在途批次和 pending 后缀、128 个 pin 各解除一次，最终空闲块恢复；这不是实际写入失败。见 [STORE_FAILURE.md](experiments/2026-09-27/STORE_FAILURE.md)。
- [x] 注入 block ID 不足使 MP server 拒绝 STORE；原始 future 为 `false`，worker/scheduler 回执、128 个 pin 和空闲块闭合，重启后同前缀外部命中为 0。仅覆盖协议校验失败，见 [SERVER_REJECTED_STORE.md](experiments/2026-09-27/SERVER_REJECTED_STORE.md)。
- [x] 两次注入 block ID 不足使 MP server 拒绝 RETRIEVE：原始 future 为 `false`，272 个 GPU block 经本地重算后回收；但 r2 读锁从 0 增至 17，目标/后续请求和 vLLM 退出后均未释放，资源验收失败。见 [SERVER_REJECTED_RETRIEVE.md](experiments/2026-09-27/SERVER_REJECTED_RETRIEVE.md)。
- [x] 用未修改的 `LMCacheMPConnector` 完成三次受控远端 lookup 延迟期间的客户端取消：`deferred` 请求清零且 follow-up 正常命中，但三次均留下 17 个 CPU 读锁；r3 还留下 1 个 prefetch job，至 vLLM 退出仍在，资源验收为 `passed=false`。见 [REMOTE_LOOKUP_CANCELLATION.md](experiments/2026-09-27/REMOTE_LOOKUP_CANCELLATION.md)。这不是自然网络故障或真实传输中断。
- [x] 用只记录时序的诊断 Connector 补请求级事件：取消清理先移除 pending LOOKUP ack，随后提交 END_SESSION，server 恢复后仍有 17 个读锁与 1 个 job；固定版真实 adapter 的特征测试复现 END_SESSION 可先于 ack 提交。诊断轮不是未修改的原生 Connector，见 [REMOTE_LOOKUP_CANCELLATION.md](experiments/2026-09-27/REMOTE_LOOKUP_CANCELLATION.md)。
- [x] 用未修改的原生 Connector 和只记录事件的 MP server 包装器确认：LOOKUP 注册目标 job 后才处理 END_SESSION，目标没有状态查询；17 个读锁与 1 个 job 在取消、follow-up、引擎退出后仍在。隔离的 server 诊断释放轮消费已完成的 17-chunk prefetch 并释放读锁后资源归零，follow-up 仍命中；仅支持已完成 L1 命中场景的归因，不是正式修复或阶段 3B 通过。见 [REMOTE_LOOKUP_CANCELLATION.md](experiments/2026-09-27/REMOTE_LOOKUP_CANCELLATION.md)。
- [x] 对照上游 LMCache issue #5339 与 PR #5008，明确 #5339 是当前 MP prefetch bookkeeping 的直接相关记录，#5008 是范围不同的 worker async-loading 清理；新增 [UPSTREAM_LOOKUP_RECLAIM.md](UPSTREAM_LOOKUP_RECLAIM.md)。
- [x] 新增无 GPU 的 prefetch deferred-reclaim 生命周期模型与测试，覆盖完成/取消顺序、失败、重复 END_SESSION、正常消费和 request-id generation 重用；模型只固定不变量，不替代 LMCache 正式实现。
- [x] 在固定版真实 `LookupModule` 上实现可选 server 回收候选并通过 15 项契约测试；原生 Connector 加候选 server 的两轮受控 L1 取消复测均回收 17 个不同对象 key，读锁、job、controller result 归零，follow-up 输出一致。见 [LOOKUP_RECLAIM_CANDIDATE.md](experiments/2026-09-27/LOOKUP_RECLAIM_CANDIDATE.md)。这不是未修改 server，也不代表阶段 3B 整体验收通过。
- [ ] 将 RETRIEVE underflow 的 17 个未释放读锁保留为独立上游候选问题；项目收尾前核查当前 LMCache 版本、最小复现与修复测试，不将其归因于正常 Connector 的未注入路径。
- [x] 2026-09-30 完成真实 FS L2 在途 prefetch 的受控取消：两轮候选在 load 完成后逐对象释放 17 个锁并清空 job/result；关闭候选的对照残留 17 个读锁、1 个 job 和 1 个完成结果。三轮 follow-up 输出一致，见 [L2_PREFETCH_CANCELLATION.md](experiments/2026-09-30/L2_PREFETCH_CANCELLATION.md)。这是控制时序的真实文件读取，不是自然故障或传输中途取消。
- [x] 2026-09-30 用真实 adapter 恢复 cleanup→END 的 ack/status 顺序，四项契约测试及一轮组合 GPU 取消通过；另完成真实文件短读的候选/对照，候选恰好释放前 8 个对象，对照残留 8 锁。见 [ORDERED_END_AND_SHORT_READ.md](experiments/2026-09-30/ORDERED_END_AND_SHORT_READ.md)。同步等待和 MQ timeout 尚未解决，不是通用乱序修复。
- [x] 受控杀死 vLLM 进程组，在无 END_SESSION 下完成真实 L2 load 并观察 630 秒：读锁和 session 先后 TTL 清除，但 job/result/key snapshot、17 个 temporary 对象及旧 GPU 注册仍残留；follow-up 输出一致，资源验收失败。见 [CLIENT_DEATH_NO_END.md](experiments/2026-09-30/CLIENT_DEATH_NO_END.md)。
- [x] 原生 TTLLock 特征测试确认旧 key/count 释放能误减 TTL 后的新读者锁；不能使用 session TTL 盲目强制解锁。见 [TTL_OWNERSHIP_BOUNDARY.md](experiments/2026-09-30/TTL_OWNERSHIP_BOUNDARY.md)。
- [x] 将 worker 注册宽限期设为 120 秒的死亡对照确认旧 GPU 注册能清理，但 prefetch job/result/锁独立残留；默认 3,600 秒宽限期内存在注册不判为泄漏。
- [x] 真实 FS 等待期关闭增强观测：load r3 的 controller.stop 后 17 个对象、读写锁、612 MiB 归零；候选仍有 abandoned bookkeeping。早期 15 秒关闭预算因 telemetry flush 被截断的一轮保留为失败，见 [SHUTDOWN_BOUNDARY.md](experiments/2026-09-30/SHUTDOWN_BOUNDARY.md)。这不覆盖内核 I/O 永久阻塞。
- [x] 临时 RLIMIT_FSIZE 触发真实 EFBIG：8 个部分写入临时文件清理，L2 failure 与锁/job 闭合；重启前缀 miss、输出一致并恢复 17 个文件，见 [L2_WRITE_FAILURE.md](experiments/2026-09-30/L2_WRITE_FAILURE.md)。GPU→L1 已成功，不将此算作 worker STORE 失败回执测试。
- [x] 容量压力触发自然抢占两轮，8/8请求完成；另两轮5秒STORE回执延迟各16次抢占，逐pin闭合。最新轮630秒观察确认session TTL归零，并用manager reset/worker submit时序解释orphan批次，见 [NATURAL_PREEMPTION.md](experiments/2026-09-30/NATURAL_PREEMPTION.md)。尚未验证DMA执行中抢占或并发输出等价。
- [x] 自然抢占后真实KV回载首轮40个完整chunk/1440层比较全等；严格来源审计确认其中1个orphan源chunk/36层无中间STORE，见 [PREEMPTION_KV_INTEGRITY.md](experiments/2026-09-30/PREEMPTION_KV_INTEGRITY.md)。1秒延迟重复的1296层比较全等，但无orphan来源覆盖，专项失败保留；5秒追加重复同样通过40chunk/1440层及1chunk/36层严格来源。
- [x] 取消期间持有真实STORE回执两轮通过：没有tick请求，各128个pin释放且909free恢复；重复轮630秒观察确认session TTL归零，见 [CANCEL_HELD_STORE.md](experiments/2026-09-30/CANCEL_HELD_STORE.md)。
- [x] 形成 [LOOKUP_OWNERSHIP_PROTOCOL.md](LOOKUP_OWNERSHIP_PROTOCOL.md)：区分client incarnation、request generation、锁reservation epoch及controller终结。只是设计约束，尚未实现完整修复。
- [x] 为可选回收候选增加逐对象释放结果检查，部分失败/通知异常保留未解决job且不自动重试；9项真实native CPU契约及5项事件审计测试通过。正常L2取消与短读的实机结果见 [CHECKED_RELEASE.md](experiments/2026-09-30/CHECKED_RELEASE.md)。这不解决无END和跨TTL所有权。
- [x] 实现独立C++ reservation锁（11测试）及真实L1Manager方法CPU契约（12测试）：TTL后旧释放、重复释放、对象重建、多reader、通知/allocator失败及并发验证通过；见 [RESERVATION_EPOCH.md](experiments/2026-09-30/RESERVATION_EPOCH.md)。新token接口未进入实际controller/RPC，原生TTLLock反例保持复现。
- [x] 在CPU契约中将reservation token贯穿真实controller的L1命中、L2写转读、retained裁剪和完成结果；19测试及50轮QUERY/abandon竞争验证唯一移交，部分失败可见且不重试成功项。见 [OWNED_PREFETCH.md](experiments/2026-10-01/OWNED_PREFETCH.md)。真实L2 I/O、后台loop及RPC未接入。
- [x] 在CPU契约中覆盖StorageManager初始L1获取/裁剪、L2局部索引合并和纯L1一次消费；29测试、11子测试及50轮QUERY/abandon竞争通过，完整服务器回归254通过。见 [OWNED_STORAGE.md](experiments/2026-10-01/OWNED_STORAGE.md)。初始和下层原token均保留，错误/部分释放不自动重试；真实服务和RPC未接入。
- [x] 在独立CPU契约中对接真实LookupModule QUERY和每worker reader slot：新增30测试、9子测试及QUERY/END、CLAIM/END各30轮竞争；END只回收未领取槽，运行槽等模拟终结。修正真实IPC编码rank接入并补1项Storage测试，完整服务器285通过。见 [OWNED_LOOKUP.md](experiments/2026-10-01/OWNED_LOOKUP.md)。真实RETRIEVE/DMA和服务未接入，3B未通过。
- [x] 在独立C++和真实L1方法CPU契约中实现token校验与活动buffer lease：新增7项native/19项L1测试及9子测试，完整服务器311通过。TTL/reset不解除活动pin，可复用CPU字节buffer验证delete/clear/write/eviction保护、对象重建隔离及CPU future延迟消费；异常保留证据，force/free/close拒绝。见 [BUFFER_LEASE.md](experiments/2026-10-01/BUFFER_LEASE.md)。未接Lookup running槽或真实DMA，3B未通过。
- [x] 将Lookup running reader slot与buffer lease连接，固定claim→begin→消费终结→unpin→reservation disposition的次序；新增23测试、8子测试，完整服务器334通过。覆盖END/TTL/reset、CPU future、多worker/TP、部分失败和两组各30轮竞争；失败槽保留证据，独立worker仍可终结。见 [LEASED_LOOKUP.md](experiments/2026-10-01/LEASED_LOOKUP.md)。确认stale的原reservation单独记过期，不伪报release成功；仍是CPU契约，3B未通过。
- [x] 在固定版 TP=1/full-attention 的 opt-in 实际服务接入 RETRIEVE 获取点原 token/pin、wire identity 与原 native terminal；实际终结前保留活动 buffer。正常回载、提交后 host 异常、取消及缺 terminal 负例分别审计，见 [实际服务报告](experiments/2026-10-01/OWNED_SERVICE.md)。不覆盖真实 driver fault。
- [x] 完成固定安装版LMCache-driven RETRIEVE的Python调用链只读审计：区分RPC/event/dispatcher清理，定位异常立即匿名释放、stream回调key-only与wire缺ticket的接入边界，记录源码hash和下一步适配顺序。见 [RETRIEVE_INTEGRATION_AUDIT.md](experiments/2026-10-01/RETRIEVE_INTEGRATION_AUDIT.md)。未审计native错误终结或执行GPU验证。
- [x] 完成独立transfer身份/完成适配CPU契约：真实MessagingFuture/DeviceMessagingFuture配受控event，22测试、4子测试、30轮竞争，完整服务器356通过。RPC/event仅诊断；原身份server callback后才一次性清理；提交异常、TTL、END及部分unpin失败保留原所有权。见 [TRANSFER_COMPLETION.md](experiments/2026-10-01/TRANSFER_COMPLETION.md)。下一步原ticket绑定range/group/buffer提交计划，再接native completion和版本化wire；实际CUDA/服务仍未验证，3B未通过。
- [x] 实现原ticket绑定的transfer plan CPU契约：零起点Lookup/full attention的chunk对齐命中后缀、object/kernel映射、APC skip、目标block数量/容量与原buffer identity校验；整个shard持有到可信终结，登记历史禁止身份复用。新增19测试、42子测试及30轮竞争，完整服务器375通过，见 [TRANSFER_PLAN.md](experiments/2026-10-01/TRANSFER_PLAN.md)。未证明GPU allocator所有权、shape/dtype或实际DMA停止，3B未通过。
- [x] 将原身份 native completion、版本化 LOOKUP/QUERY/RETRIEVE 与真实 GPU copy 接入实际 MP 服务/Connector/BlockPool；正常资源审计、取消/失败后新请求和缺 callback 的拒绝关闭通过。RPC 或 event 完成仍不直接释放 lease。
- [ ] 验证真实 driver 执行错误、硬件 copy 执行期间中断、多 worker/multi-GPU 及更广 allocator 复用；既有 stream hold 只证明 native terminal 尚未确认。
- [x] 完成上述链路的独立实验原型：原身份/nonce native callback、版本化 ticket 的 ZMQ 往返、真实 worker 布局与 pinned CPU→CUDA kernel、独立 target arena，以及 D2H writer marker 后发布/失败丢弃。完整服务器 398 passed；缺 callback 保留 unresolved，真实 driver fault 未注入。见 [GPU 与 decode 证据](experiments/2026-10-01/NATIVE_TRANSFER_AND_DECODE.md)。这尚未接入正式 MP server/Connector/BlockPool。
- [x] 将 generation 检查和受控非法 block 预检作为 opt-in 桥接运行在真实 MP server/Connector/vLLM 上：正常 CPU 回载 1536 tokens，独立 CUDA stream marker 到达，BlockPool 最终 909 free；非法短 block 触发 272 block 重算后 909 free、CPU 读锁/job 为 0。显式 CUDA 完整回归 405 passed、115 subtests。见 [正式服务桥接](experiments/2026-10-01/OWNED_MP_INTEGRATION.md)。这只是两个受控路径的局部资源验收；原 ticket/lease、原生身份释放和无 END 等仍未接入，不能勾选完整 3B 条目。
- [x] 实际服务获取点保存 reservation/epoch/pin，贯穿初始 L1、L2 controller/crop、QUERY/RETRIEVE 和 L2 STORE；原释放身份逐项且恰好一次审计通过，不按 key 事后补 token。
- [x] 无 END 客户端死亡：真实进程 SIGKILL 后心跳过期回收 17 个原 token，job/controller/pin 归零，恢复请求外部命中 4352、输出一致；120 秒 GPU 注册 grace 是诊断参数。
- [x] 八个真实服务场景按 r2/r3 通过：L2 取消、文件短读、客户端死亡、持有 load 的关闭拒绝、STORE/RETRIEVE 提交后 host 异常与在终结等待期取消。失败轮全部保留，不能说单轮矩阵全通过。
- [x] 缺 STORE/RETRIEVE native terminal：分别保留 8 个 writer、17 个 reader/pin 和 GPU context，直接 transfer.close 与完整关闭均拒绝；按预期 unresolved 审计，完整实例重建为恢复边界。
- [x] 自然抢占 r8/r9：各 4 次抢占、各 2 次在指定原 transfer 的 native terminal 等待窗口内；各 512 次 pin 引用释放，BlockPool 恢复 454 free，各 432 层 whole-chunk KV bitwise 相同、6 个无中间 STORE 的 orphan 源 chunk；关闭后原 token/job/terminal 审计通过。
- [x] 最终快照显式 CUDA 回归 438 passed、115 subtests；正常 eager 回载重新复核。限定单卡隔离实例的 3C 准入范围已写入 [总体计划](PROJECT_PLAN.md) 和 [开工顺序](3C_GPU_ENTRY.md)。
- [ ] 排查 30 秒回执延迟 r6 的空闲收尾：pin/transfer 归零但两个空 owner 到关闭才回收；不得把 5 秒成功轮算作此问题修复，也不得只看 free block 宣布 drain。
- [ ] 完成完整生产资源门槛：永久 I/O 自动恢复、真实 driver fault/传输中断、L2 不确定提交异常的实机注入；复核上游并形成最小可维护补丁。此项继续开放，与限定 3C 研究准入分开。
- [ ] 单独复核异步调度的 prefix reset API 与 deferred block free 时序；EVICTION_AWARE自然抢占延迟轮session TTL已实测归零，当前 reset API 的失败不能算作生成或资源泄漏，active_sessions 也不能作为回收通过证据。
- [x] 完成首版不接 GPU 的 fake worker 状态机：预算、连续前缀、身份复核、pin/unpin、部分保存、取消和 generation 回执隔离；17 项测试、6,000 步固定种子交错以及 14 个执行示例通过，见 [PROTECTION_STATE_MACHINE.md](experiments/2026-09-30/PROTECTION_STATE_MACHINE.md)。
- [x] 增加 16-token 物理 block / 256-token LMCache chunk 的 16:1 CPU 分组模型，七项测试验证整 chunk 预算、失效 block 停止前缀和回执范围；不是实际 GPU adapter。
- [x] 真实 vLLM BlockPool 的纯 CPU 元数据桥接八项测试通过；新增同 request-id 跨代单在途限制及 [PROTECTION_ADAPTER_CONTRACT.md](PROTECTION_ADAPTER_CONTRACT.md)，明确 block/chunk hash、零步回滚和 worker 布尔回执限制。
- [x] 从真实token ledger计算vLLM/LMCache两条hash链，6项测试含Request追加decode；新增真实STORE metadata CPU交接桥接，7项测试与原生tracker字段对照，不接GPU/RPC。
- [x] 实现 ProtectionConnector 零保护预算的真实 scheduler/worker 交接与配置开关；同一所有权栈的默认 EVICTION_AWARE、关闭和零预算三组各 96 请求、14 STORE 批次通过，范围/文本一致，各 216 层 KV 全等且资源闭合；新增 22 项测试，完整 CUDA 回归 460 passed、115 subtests。见 [零预算报告](experiments/2026-10-09/PROTECTION_ZERO.md)。无新增保护 pin/STORE；非零预算尚未支持，短尾流量不代表空闲收尾修复。
- [x] 最多两个完整 chunk 的真实分配前保护正常路径：两组各 348 请求、文本一致；保护组 9 STORE、32 block 峰值、19 次零步回滚、864 层 KV 全等，原回执及运行期资源闭合。严格保护来源 7 批/14 chunk/504 层。新增零步回滚刷新 LRU 问题已修，r1/r2 失败保留，见 [报告](experiments/2026-10-09/PROTECTION_TWO.md)。
- [x] 新增保护 STORE 回执专项：真实 future 延迟 5 秒后在固定恢复流量下 508 请求、720 层 KV 全等并闭合；真实成功后报告失败时 worker/scheduler 状态一致、pin 释放且 864 层 KV 全等。空闲延迟轮仍留下 pending owner，失败轮保留；注入不代表 DMA 中断或 STORE 执行失败。见 [报告](experiments/2026-10-10/PROTECTED_RECEIPTS.md)。
- [x] 整项超预算 GPU 回退对照通过：零预算/一个 chunk 各 348 请求，14 个默认 STORE 范围/次数及文本一致，各 720 层 KV 全等、909 free 恢复；17 个候选有 51 次预算拒绝，其中 14 个由默认 manager 保存，无保护 pin/STORE。完整 CUDA 回归 506 passed、115 subtests。见 [预算报告及下一组实验设计](experiments/2026-10-10/PROTECTION_BUDGET.md)。
- [x] 共享受保护 GPU 前缀的新请求取消 r3 通过：347 个完整响应和 1 个取消请求，同组 32 block 引用 2→1→0，原批次两个 chunk/72 层严格 STORE→H2D 来源，全轮 936 层 KV 全等、资源闭合；完整 CUDA 回归 525 passed、115 subtests。r1 checkpoint 提前回载、r2 重复预热中间 STORE 的失败保留，见 [报告](experiments/2026-10-10/PROTECTION_SHARED_CANCEL.md)。不覆盖 DMA 执行中取消或空闲延迟收尾。
- [x] 保护引用存在期间的无共享前缀请求自然抢占：一次真实抢占、七次分配未复用保护 block、原回执释放，504 层回载一致，原保护两个 chunk/72 层严格来源、运行期资源归零。首轮审计入口导入失败保留，r2 整轮通过，见 [自然抢占报告](experiments/2026-10-11/PROTECTED_PREEMPTION.md)。
- [x] 实际 GPU 保护状态下错误代际 envelope、原回执后的重复 envelope 拒绝；原生 manager 调用数和引用/所有权不变，864 层 KV 一致、资源闭合。完整 CUDA 回归 554 passed、115 subtests。错误代际 ID 为诊断构造，未覆盖实际 raw ID 复用，见 [身份报告](experiments/2026-10-11/RECEIPT_IDENTITY.md)。
- [x] 共享受保护前缀请求的自然抢占 r2 通过：同组 32 block 引用 2→1→0、保护窗口 39 次分配未复用、原批次两个 chunk/72 层严格来源、全轮 504 层 KV 一致且资源闭合。r1 审计入口导入失败保留，见 [共享抢占报告](experiments/2026-10-11/SHARED_PREEMPTION.md)。
- [x] 一个 chunk 的真实保护 GPU 对照通过：零预算/单 chunk 各 348 请求且输入输出及默认 STORE 范围一致；单 chunk 组七个保护 STORE、峰值 16 block，468 层 KV 全等，其中六批/216 层有严格来源；两组资源闭合。见 [一个 chunk 报告](experiments/2026-10-11/PROTECTION_ONE_CHUNK.md)。
- [x] 在途保护占满 32-block 预算时，另一 32-block 候选零新增 pin 回退，原回执后被拒候选由默认 manager 保存；347 请求、864 层 KV 全等，原保护两个 chunk/72 层严格来源，资源闭合。见 [在途预算报告](experiments/2026-10-11/INFLIGHT_BUDGET.md)。
- [x] 物理容量不足 GPU 回退：1.25 GiB KV 池、预算仍有 32 block 且无在途保护时，free=439 小于 reserve+required=452，候选零 pin 回退并随后由默认 manager 保存；347 请求、936 层 KV 全等、保护 14 chunk/504 层严格来源，资源闭合。完整 CUDA 回归 585 passed、115 subtests。见 [容量报告](experiments/2026-10-11/CAPACITY_FALLBACK.md)。
- [x] 实际 HTTP raw ID 复用后的跨代回执隔离：关闭 vLLM 默认 ID 随机化的隔离诊断中，同 API ID 的两代真实请求拥有不同 wire identity；旧成功、旧失败、重复旧成功和终结后的重复 envelope 均被拒绝，第二代 pending prefix 保留并实际回载 72 层 KV。正常旧回执和“实际 STORE 成功、报告失败”的旧回执各通过一轮，见 [报告](experiments/2026-10-11/RAW_ID_REUSE.md)。这不覆盖真实 STORE/DMA 执行故障。
- [x] 真实保护 STORE 在 CUDA copy enqueue 后的受控 server host exception：LMCache 向未改写 worker future 交付原始 `false`，native terminal、32 个 pin 释放和 worker/protection/scheduler 失败回执闭合；348 请求、864 层回载 KV bitwise 全等、909 free 恢复。它不覆盖 driver/DMA/PCIe fault 或自然硬件故障，见 [报告](experiments/2026-10-11/PROTECTION_SERVER_STORE_FAULT.md)。
- [ ] 补真实 CUDA driver/DMA/PCIe fault 的边界；不能用成功 future 后修改 receipt 语义，或受控 host exception，替代。
- [x] >512-token pending STORE 的 chunk 前缀拆分：`[0,1024)` 先保护 `[0,512)`，成功 receipt 后默认 manager drain `[512,1024)`；前缀失败则丢弃 suffix，policy ledger 和 prefix closure 均闭合。成功及真实 CUDA copy enqueue 后 server host exception 两条 GPU 路径均通过，见 [报告](experiments/2026-10-11/PROTECTION_LONG_PREFIX.md)。范围仍是已结束、无外部引用、从零开始的前两个完整 chunk。
- [x] 新增路径的 KV/输出正确性通过后，关闭诊断日志完成 zero/one/two chunk 各三次重复消融，报告 TTFT、实际 prefill、排队、搬运、保护/在途峰值与策略开销；九个 cell 都通过严格资源与服务错误门槛，one chunk 在该固定 trace 的 TTFT P95 均值相对 zero 低 6.22%，two chunk 低 3.45%。保留输出哈希差异和范围限制，见 [正式报告](experiments/2026-10-11/PROTECTION_ABLATION.md)。

交付：可启用和禁用的实现、测试、消融及反例。性能门槛由基线噪声与实际需求决定，不先填加速目标。

## P4：复核与展示

- [x] 固定开发 trace，完成 prompt SHA 无交集、到达结构相同的合成验证输入 zero/one/two 各三次重复；九格 612 请求及资源门槛通过，三格严格分类的关闭竞态单列。整体 TTFT P95 相对 zero 为 one -0.57%、two +0.31%，未稳定复现开发集改善，不作泛化结论。见 [独立输入复核](experiments/2026-10-11/PROTECTION_ABLATION_VALIDATION.md)。这不是独立真实 Agent 数据或新到达分布。
- [x] 完成 1.5 GiB 容量点 zero/one/two 试点及三次正式重复（12 格、816 请求），保留 zero/one/two、输出 hash 差异、H2D/D2H 和资源证据；one/two TTFT P95 相对 zero 为 -5.09%/-3.99%，但 H2D 增加 8.92%/6.49%，不作默认预算结论。2.5 GiB zero 试点 68 请求因 1 个 owned job/session 未闭合而按预注册规则停止，one/two 未运行；见 [容量扫描](experiments/2026-10-11/CAPACITY_SCAN.md)。
- [x] 独立诊断 2.5 GiB zero 的 owned job/session 残留：两轮观测复现已结束请求保留 `[0,1024)` pending STORE 且无 in-flight，第三轮追加 3 个固定压力请求后观察到 STORE→terminal→END→reclaim 并归零；新增零 token pending 契约测试。诊断结果不并入性能聚合，见 [CAPACITY_OWNER_DIAGNOSIS](experiments/2026-10-11/CAPACITY_OWNER_DIAGNOSIS.md)。
- [x] 设计并以 opt-in 方式实现空闲 pending STORE 的有界丢弃/结束候选；立即收尾反例、15 秒宽限 GPU 轮及 zero/two trace 重入已归档。候选保留提交批次身份和资源安全，不释放 GPU block、不 touch/free、不修改已提交 batch；见 [IDLE_FINALIZE](experiments/2026-10-11/IDLE_FINALIZE.md)。
- [x] 2.5 GiB two/15 秒宽限交互专项：136 请求、9 个保护提交/回执、288 次保护 pin 释放、2 次 idle drop，137 个 job/280 个原 reservation/84 个 transfer 终结闭合；输出 hash 差异 8/68 保留。两种动作的 wire identity 不相交，不宣称同请求先保护再丢弃；启动环境失败 a 单列，b 通过。完整显式 CUDA 回归 686 passed、115 subtests。
- [x] 空闲收尾后实际 raw ID 复用：136 请求、68 对不同 wire generation，旧 reclaim 均早于新 lookup；含 1 个实际 idle-dropped raw ID 重入，9 个保护提交/回执，137 job/282 reservation/91 transfer 闭合。输出 hash 8/68 差异保留；完整显式 CUDA 回归 703 passed、115 subtests（含下一专项配置测试）。不覆盖在途 ID 重叠。见 [空闲收尾报告](experiments/2026-10-11/IDLE_FINALIZE.md)。
- [x] 固定原负载验证候选与失败回执、共享取消的兼容性：分别 348/347 完整请求、864/936 层 KV 全等，资源及严格来源通过，但两格 idle drop=0；延迟回执 a 资源局部通过却仍在 GPU 命中 512、H2D 未覆盖，失败保留。见 [组合矩阵](experiments/2026-10-11/IDLE_RECEIPT_MATRIX.md)，不将三格概括为全通过。
- [x] 按 [新栈容量预注册](experiments/2026-10-11/IDLE_CAPACITY_PREREGISTRATION.md) 完成 2.5 GiB pilot 三格；zero/one/two 均通过源码签名、token、H2D、active session/temporary 清零门槛，每格 idle drop=1。formal 九格已启动，尚未完成；诊断/旧性能/新性能分开，任一资源或覆盖失败就停止该轮。
- [x] 完成新栈 2.5 GiB formal 九格、612 请求及独立复算/原始证据归档；每格 idle drop=1，运行期及退出后 session/temporary 清零。one/two TTFT P95 均值相对 zero 为 -3.07%/-2.00%，第一组重复均更慢，输出 hash 差异和两格退出 traceback 保留，见 [正式报告](experiments/2026-10-11/IDLE_CAPACITY.md)。
- [ ] 扩展空闲收尾门槛：多进程/异步调度、共享前缀、取消/抢占、失败回执、多个 pending chunk、长空闲窗口和真实 driver fault；当前候选只允许固定版本单卡同步 text-only 实例，默认关闭。
- [ ] 在更多到达模式上多次重复；再增加长 prefill、稳定 decode 和集中恢复。
- [x] 六点固定参数开发调优及 one/two 同期对照：pilot 八格、formal 24 格全部通过；1,632 个正式请求独立时间戳/资源复算通过，按预注册选择 h5.0/d1。one/two 对选中项 +0.88%/-0.19%，未证明保护优于调优固定基线，见 [FIXED_BASELINES](experiments/2026-10-11/FIXED_BASELINES.md)。
- [ ] 固定选择后的新文本验证：原 12 格在第三格 one 零保护覆盖处停止（8 次 capacity fallback）；已有三个 cell 请求与资源通过，整体未完成，原失败和九个未运行格保留，不重跑筛选。
- [x] 独立无保护 reference 四组（原生、同所有权栈立即卸载、默认 fixed、选中 fixed）：pilot→formal 共 12 格、816 请求通过；native TTFT P95 最低，整套 lazy 仍有搬运/接入成本，见 [UNPROTECTED_REFERENCES](experiments/2026-10-11/UNPROTECTED_REFERENCES.md)。不替代前述保护验证。
- [x] 到达时间压缩对照：pilot 4/4、formal 12/12 格通过；816 正式请求独立复算一致，native 最低，缓存组 TTFT 高 3.47–5.81%，见 [报告](experiments/2026-10-11/ARRIVAL_COMPRESSION.md)。
- [x] 原新文本零覆盖的一次独立观测：9 个首次 capacity fallback、free 423–704、reserve 972–2208，预算可用且无在途 pin；全部随后默认 STORE。68 请求/资源通过，保护仍 0/0，未改变原性能失败，见 [诊断](experiments/2026-10-11/COVERAGE_DIAGNOSTIC.md)。
- [ ] 长 prefill 四组 pilot：2/4 格通过后停止；默认 fixed 的 68 请求返回后在 idle wire identity 预检 fatal，残留 1 owned job/11 sessions。源 trace、部分归档、失败补审计已保存，未运行选中 fixed 与 formal，见 [失败报告](experiments/2026-10-11/LONG_PREFILL.md)。
- [x] 为长 prefill 身份失败补两个真实 manager/BlockPool 特征测试；最新完整 CUDA/native 回归 737 passed、115 subtests。准备异常后只读身份观察器及独立 runner，另 4 项 CPU 测试通过；GPU 观察尚未执行。
- [x] 完成两轮独立 long-prefill 诊断和真实 tracker/manager 最小复现：抢占后新 tracker 生成新 wire，但原生 PREEMPTED 路径跳过 lookup，mapping 仍旧代；timeline 观察 4 个 STORE-only wire 未 END、4 sessions 残留。完整 CUDA/native 746 passed、115 subtests；离线审计另 5 项通过。见 [身份缺口报告](experiments/2026-10-11/PREEMPTED_IDENTITY.md)。两轮均未再次触发 idle fatal，资源均失败，不算修复。
- [x] 新增默认关闭的 `GenerationSessionLedger` 身份终结候选：分离当前 lookup 路由与各 generation 登记，ticket 固定 acquisition-time wire identity；CPU 契约覆盖旧 STORE 迟到提交/回执、连续两次抢占、raw ID 重用、取消、多个 pending chunk、重复/部分 END、client-wide high-water 和生命周期 tombstone。实现尚未接入 vLLM/LMCache 生产 connector，见 [GENERATION_LEDGER_CANDIDATE.md](experiments/2026-10-11/GENERATION_LEDGER_CANDIDATE.md)。下一步是 adapter integration gate，之后才允许单独 GPU pilot。
- [x] 账本 client 边界及真实 tracker/manager/BlockPool 手动交接契约通过：PREEMPTED 后 LOOKUP A、STORE B、END A/B 分离，原 block 引用闭合；服务器专项合计 39 passed。terminal 和 producer seal 由测试显式交付，未证明真实回调接线；原错误路径测试保持复现。
- [x] 独立 opt-in generation connector 接通真实 tracker/manager admission、pending drop、原跨进程 batch receipt、lookup/status/free future、RETRIEVE 及 producer seal；真实 raw ID 重用的旧 in-flight success/failure 和 queue owner-thread pump 契约通过。完整显式 CUDA/native 回归 805 passed、115 subtests；见 [接线报告](experiments/2026-10-12/GENERATION_BRIDGE.md)。这不等于 GPU 服务验收。
- [x] 独立长 prefill generation 诊断 b/c 通过；c 的 78 代际 END、589 reservation、69 lookup job、75 native transfer 与 BlockPool 909 free 恢复逐项审计通过，原失败和启动失败 a 保留。诊断不计入性能。
- [x] 新栈长 prefill 四配置pilot 4/4、formal 12/12通过，816正式请求；本地独立审计、请求计时/阶段/搬运分解和图表完成，见 [报告](experiments/2026-10-12/GENERATION_LONG_PREFILL.md)。整套缓存配置低于native，但立即卸载优于两种lazy；无非零保护收益推断。
- [ ] 按 [decode192预注册](experiments/2026-10-12/DECODE_MATRIX_PREREGISTRATION.md) 完成新pilot/formal；68固定请求、13,056输出tokens，保持prompt/arrival/seed；原pilot在立即卸载session残留处停止，default/selected与formal未运行。
- [x] decode192立即卸载只读时序复现：END返回后末批STORE重建session，两代残留；原reservation/native transfer闭合。独立原回执后END候选通过真实库回调/future契约和GPU68请求、144 reservation、69 lookup、101 transfer、909 free。范围同步TP1零保护，无抢占/unhealthy/reuse重叠；见 [报告](experiments/2026-10-12/DECODE_IMMEDIATE_DIAGNOSIS.md)。
- [x] 新decode192 receipt pilot 4/4通过，272请求/52,224输出；服务器、本地及205项冻结源独立收集完全相同，旧immediate失败单列，见 [pilot报告](experiments/2026-10-12/DECODE_RECEIPT_PILOT.md)。
- [x] 同签名decode192 formal十二格三次重复通过，816请求/156,672输出tokens；服务器/本地/冻结源独立报告一致，154原始文件字节一致。native最低，immediate/default/selected相对+4.24%/+5.85%/+2.29%，一格正常关闭traceback和输出hash差异保留，见 [正式报告](experiments/2026-10-12/DECODE_RECEIPT_FORMAL.md)。
- [x] 固定commit的真实匿名结构编号21–100共80文件CPU准入审计，全部六条eligible/60请求；实际Qwen3-4B tokenizer生成209,661输入tokens/2,880输出预算。源SHA、54前缀转换、3,262源block身份、零跨session前缀碰撞审计与三项重建/篡改测试通过，见 [方案](experiments/2026-10-12/REAL_STRUCTURE_PLAN.md)。
- [x] 六session真实匿名结构观测GPU pilot四格通过，240请求/11,520输出；0.5秒采样见94.94–99.67% KV峰值、5–6等待峰值，lazy两组各2次实际抢占，缓存组有H2D且资源/generation闭合。80原始文件服务器/本地/Git字节一致；不是live Agent或正式性能，见 [结构GPU报告](experiments/2026-10-12/REAL_STRUCTURE_GPU.md)。
- [x] 关闭额外采样的同结构pilot4/4通过，另240请求/11,520输出；210项冻结源、72文件/19,800,020 bytes，服务器/本地/冻结源报告一致。lazy各1次抢占且资源闭合，输出差异保留。
- [x] 同签名匿名结构formal12/12，720请求/34,560输出，服务器/本地/210项冻结源独立报告一致；186原始文件字节一致。native整体TTFT最低，immediate/default/selected相对+5.76%/+11.33%/+9.69%，全六会话差异、原输出hash和一格关闭traceback保留，见 [正式报告](experiments/2026-10-12/REAL_STRUCTURE_FORMAL.md)。
- [x] generation与保护真实 manager CPU 交接候选：60项回归通过（新增18项），原票据/prefix/suffix lineage、原回执、旧代隔离、零步回滚及 native 注册后异常禁止误释放；服务 bind 明确禁用，见 [契约报告](experiments/2026-10-12/GENERATION_PROTECTION_CONTRACT.md)。
- [x] 独立generation保护Connector接线及12项native callback契约；最终873 passed、115 subtests显式CUDA/native回归。原zero guard保留，新组合GPU未运行，见 [接线报告](experiments/2026-10-12/GENERATION_PROTECTION_WIRING.md)。
- [x] 真实双候选部分提交故障、poisoned shutdown终态证据保存及写证据失败保留原异常；新增5项测试，显式CUDA/native全套878 passed、115 subtests。服务进程故障退出未验证，见 [报告](experiments/2026-10-12/GENERATION_PROTECTION_FAULTS.md)。
- [x] 新组合正常GPU zero/one/two准入：1,044请求，720/720/864层KV全等，保护11/9次、最终pins0、各909 free；三格generation精确闭合，a采集失败保留。见 [报告](experiments/2026-10-12/GENERATION_PROTECTION_GPU.md)。
- [x] 新组合实际服务after-commit主机故障：a未落盘失败保留；捕获位置保存snapshot后b通过，自行退出、16pins unresolved、无误rollback/END；879服务器回归通过。本地审计单列，见 [报告](experiments/2026-10-12/GENERATION_PROTECTION_SERVICE_FAULT.md)。
- [x] 新组合长prefix及失败回执两个独立GPU专项：各348请求，1800/864层KV全等；长prefix8条完整回载链、9派生票据闭合；失败回执1次成功后报告失败，原资源闭合。见 [长prefix](experiments/2026-10-12/GENERATION_PROTECTION_LONG_PREFIX.md) / [失败回执](experiments/2026-10-12/GENERATION_PROTECTION_FAILED_RECEIPT.md)。
- [x] 新组合共享取消/共享受害请求自然抢占GPU专项，936/504层全等、refs2→1→0、generation348/351闭合；共享取消teardown traceback/CUDA IPC警告保留，见 [报告](experiments/2026-10-12/GENERATION_SHARED.md)。
- [x] generation与非零保护显式交接的受控准入：真实manager/独立Connector、prefix/suffix/原回执与故障票据，GPU bitwise及取消/抢占专项，四格pilot和十二格开发formal完成；完整生产故障与泛化不在本勾选范围。见 [正式报告](experiments/2026-10-12/GENERATION_STRATEGY_FORMAL.md)。
- [x] 新组合固定参数新文本覆盖准入的执行与失败归档：两个零保护通过，selected-one因35次capacity/0提交失败，selected-two未运行；这是反例记录，不是准入成功。见 [报告](experiments/2026-10-12/GENERATION_STRATEGY_VALIDATION.md)。
- [x] 独立opt-in容量/free/reserve/live footprint与候选窗口诊断：两格136请求，原策略不变，确定新文本容量满足后共享首块ref检查拒绝147次；原零覆盖失败保留。见 [报告](experiments/2026-10-12/GENERATION_CAPACITY_DIAGNOSTIC.md)。
- [x] 共享前缀引用保护CPU候选：新增29项真实BlockPool/generation契约，相关84项、本地AST约束2项通过；原引用保留、rollback/free-rank及多候选部分故障闭合。CPU模拟owner释放不等于服务取消验证。见 [报告](experiments/2026-10-12/SHARED_PREFIX_CONTRACT.md)。
- [x] 独立共享前缀服务 Connector/绑定子类、probe MRO 与已有 owner 引用 callback 契约；原 CPU bind 拒绝与旧默认保留。native-a 76、native-b 85 passed，见 [共享前缀准入](experiments/2026-10-13/SHARED_PREFIX_ADMISSION.md)。
- [x] 新共享前缀 helper 正常 zero/one/two 及取消/自然抢占/失败回执/raw ID重用 GPU 七格、自然零步回滚独立准入；严格KV/原回执/资源及generation闭合，源码冻结复算一致。
- [x] 先共享再保护 GPU 独立正确性：原历史trace轻量诊断3次提交，扩展尾段1个实际保护来源chunk/36层bitwise相等；原trace全量探针零覆盖/稀疏无回载失败保留，其他2批未声称回载覆盖。
- [ ] 同一先共享再保护批次与取消/抢占/旧代的联合GPU覆盖，以及抢占后完整恢复；当前CPU契约和分立GPU证据不代替组合验收。
- [x] 冻结共享候选历史输入pilot4/4→formal12/12：240源同签名，one/two各4/4/4批；TTFT P95均值−1.52%/−1.58%，无稳定收益结论。原失败保留，见 [正式报告](experiments/2026-10-13/SHARED_STRATEGY_FORMAL.md)。
- [x] 既有六session匿名结构的非零共享保护pilot复核已执行：3/4通过，双chunk零提交，原失败归档；formal未启动。匿名结构属于历史负载扩展，不冒称未见样本。
- [x] 原失败后的独立windows/allocation/native逐次准入诊断及四包证据复算；真实manager与Scheduler冷输入/共享前缀/异步回载边界契约通过，见 [报告](experiments/2026-10-13/SHARED_STRUCTURE_NATIVE.md)。诊断不替代性能或原pilot资格。
- [ ] 补真实Scheduler混合命中、owner离开、完整序列门槛的连续准入契约；明确整步安全边界后才实现独立opt-in候选，不直接降低reserve。
- [ ] 新候选真实库异常契约、独立GPU KV/资源准入、固定pilot及验证负载；通过后才正式性能比较。当前无稳定或泛化收益证明。
- [ ] 用 Qwen3-8B＋5090 复核；如要区分模型/硬件效应，需要额外控制变量实验。
- [x] 开发和验证消融已统计客户端 ready-to-first/ready-to-end 及会话完成时间；原始值与聚合均归档，不将入口等待隐藏。新容量和到达负载继续沿用该口径。
- [ ] 整理复现文档、版本清单、原始数据、图表和个人贡献。
- [ ] 视结果决定独立项目发布或小范围上游贡献。
- [ ] 项目阶段收尾后重新核查 LMCache/vLLM 上游状态；若仍可复现且有可维护的最小修复与回归测试，考虑提交异步回载压力信号的上游 issue/PR，范围见 [UPSTREAM_ASYNC_KV_PRESSURE.md](UPSTREAM_ASYNC_KV_PRESSURE.md)。

## 优先顺序

2026-10-13：共享候选历史输入正式比较已完成。下一步既有匿名结构输入的固定参数覆盖pilot；无覆盖即保留反例，不扩模型/卡或调reserve凑收益。先共享再保护联合异常与完整抢占恢复另列支线。

2026-10-12 23:14：共享引用CPU契约完成。先独立服务绑定/callback，再GPU正确性；不把CPU契约替代GPU准入，不放宽reserve、不抹去零覆盖反例。

2026-10-12 23:01：容量诊断发现共享首块引用拒绝，优先共享引用安全契约；不改输入、降reserve或直接删除ref检查。正式泛化矩阵仍未具备准入。

2026-10-12 22:34：新文本保护覆盖失败，正式泛化矩阵不具备准入。先只读容量/候选窗口诊断，不能重跑筛选、降reserve或改压力来抹去反例。

2026-10-12 22:16：新组合开发formal已闭合。优先固定策略新文本覆盖准入；不重复开发formal、不重新调参选择更好结果、不扩大卡/模型替代泛化验证。

2026-10-12 21:29：共享取消/抢占联合GPU审计完成，下一步限定准入汇总与新签名策略pilot预注册，先coverage/resource门槛。退出traceback仍开放；不得以有限专项宣称全面正确性。

2026-10-12 21:21：长prefix/失败回执独立GPU通过，下一步共享取消和实际抢占。长suffix失败、其他服务故障点仍开放；之后才新签名策略pilot。

2026-10-12 21:11：实际服务提交后主机故障及失败snapshot门槛完成，下一步新组合长prefix/共享取消/抢占/失败回执；其他故障点与driver fault仍开放。

2026-10-12 21:04：正常GPU三格通过；下一步服务故障退出及失败证据，长prefix/取消抢占/失败回执，之后新签名策略pilot。原性能结论不变。

2026-10-12 20:52：多候选故障和shutdown证据CPU门槛完成，下一步独立服务GPU诊断runner、正常bitwise/resource准入与实际进程故障退出。新组合无模型服务结果，旧性能结论不变。

2026-10-12 20:38：独立Connector候选已接线；补多候选部分失败和poison进程退出/终态证据，再独立GPU zero/one/two准入，最后新签名策略消融。873项全套含底层CUDA通过，不等于组合GPU bitwise或性能。

2026-10-12 19:29：CPU候选完成，下一步独立服务Connector接线及回调故障测试，再GPU正确性准入，最后新签名策略消融。未开启非零预算，未新增性能结论；历史210项源码在归档中，服务器目录已增加候选。

2026-10-12 18:50：匿名结构formal12/12完成，720请求，三处独立复算一致。零保护负载复核阶段闭合；下一策略主线为generation与保护交接契约及独立正确性准入，不能直接开启非零预算。暂不扩大模型/卡或改trace追求正结果。

2026-10-12 18:18：用户再次继续授权，同签名匿名结构formal十二格已启动，服务器210源保持冻结。完成后独立复算、全六会话描述与证据归档；不可将pilot混入正式聚合。

2026-10-12 16:53：decode formal及匿名结构观测/无采样两个pilot全部闭合并归档。下一步为服务器210项冻结源下匿名结构formal十二格；本窗口不再启动GPU，17:00汇报。

2026-10-12 16:43：decode formal十二格已完成并推送；六session结构观测准入4/4通过，确认高占用/等待及lazy两组真实抢占，资源闭合。关闭额外采样的pilot运行中；本窗口只闭合pilot、证据及下一窗口正式入口，不启动预计超过17:00的formal。

2026-10-12 16:04（用户续授至17:00）：205项服务器源审计通过，同签名decode receipt formal十二格正在运行，不得同步CPU开发或重复启动。真实匿名结构GPU试点runner、来源/采样独立复算、预注册已准备，本地16项通过；等待decode完整门槛后才运行。16:55停止启动新格，17:00安全收尾。

2026-10-12 14:48：新decode receipt pilot四格完整通过并归档，formal仍未开始；时间不足完成十二格，因此安全收尾并为下一窗口保留服务器205项冻结源。六会话真实匿名结构CPU生成和独立来源/前缀审计完成，尚无GPU结果。下一步先同签名decode formal，再真实结构GPU准入。

2026-10-12 14:32：decode原pilot失败已定位为END先于末批STORE的session重建；独立immediate回执候选诊断通过。新四配置pilot进行中，源码冻结；完整834 passed/9 skipped/115 subtests，CUDA另9 passed。今日先闭合pilot与证据，不赶在截止前启动不能安全完成的formal。

2026-10-12 13:51：新栈长prefill正式12格完成并独立复核，原失败不改写。decode192新源签名已冻结，先pilot后formal；14:55停止启动新GPU格、15:00安全收尾汇报。零保护限定，暂不扩卡或调参。

2026-10-12 13:21：独立 generation GPU 诊断 b/c 通过，原身份和物理 BlockPool 证据已归档；新栈四配置性能 pilot 已启动，禁止修改其冻结源码或重复启动。通过完整独立复核后进入 formal；原启动失败 a 和旧身份失败不改写。下面日期/时点较早的条目为历史开发记录。

2026-10-12 连接恢复：服务器专项 52 项及新增真实 MessagingFuture 契约 3 项通过，原始输出已归档；固定 adapter/manager 哈希一致。当前不再阻塞于 SSH，下一步仍是真实 manager/connector 回调接线，尚未启动 GPU pilot。

2026-10-12 追加：独立 `GenerationRPCClient` 实现 scheduler-thread lookup/status/free future 追踪和原身份完成，账本增加操作生命周期上限；本地 49 项通过，见 [RPC 候选报告](experiments/2026-10-12/GENERATION_RPC.md)。旧 SSH 地址两次超时，未接上服务器；实际 future 验证、真实 manager 接线和 GPU pilot 均未执行。

最新阻塞已从未知身份失配推进到可重建调用链：抢占后 tracker 新代跳过 lookup，client 保留旧代 mapping，STORE 与 END 分属不同 wire。真实 GPU 时序和两项真实库 CPU 复现已归档；原 pilot 仍失败，两个独立诊断资源也未通过。下一步实现分离 lookup 路由和 session 终结身份的独立候选，补迟到旧批次、连续抢占、复用及部分失败，再逐身份 GPU 审计；冻结后重开新的长 prefill pilot，不能直接 formal。短上下文已完成矩阵和其退化结论不变。

保护 v1 在调优固定基线上尚无稳定收益，零覆盖独立诊断说明保守 reserve 限制触发范围；暂不扩大预算或削减预留。之后补稳定 decode、真实结构压力和耗时分解，再决定是否扩模型/GPU。3B 的单卡限定验收继续保留，真实 driver/DMA fault、多 worker/异步、永久 I/O 自动恢复和编译输出差异仍开放。当前不需要升级 GPU；上游 PR 在最小修复和维护边界明确后评估。
