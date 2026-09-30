# 待办与阶段验收

更新：2026-09-30。未勾选项均未完整完成；历史设计与当前实测环境以 ENVIRONMENT.md 及对应日期的记录区分。

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
- [ ] C：扫描固定 horizon/提交上限，得到合理调优的基线。
- [x] 测工作集小于 GPU、超过 GPU 两个区间（GPU KV 人为固定 2 GiB）。
- [ ] 补测超过总缓存区间及更多 GPU KV 容量点。
- [ ] 加入长 prefill 突发、稳定 decode、多轮集中恢复。
- [ ] 分解排队、重算、回载耗时，记录搬运量与调度开销。
- [x] 保存排队/precompute 聚合指标、CPU/GPU 命中和 staging 搬运字节；尚未得到独立 DMA 关键路径分解。
- [ ] 完成多轮自然缓存状态下的输出差异归因；HTTP/SSE/长度检查通过不等于正确性通过。
- [x] 完成同输入/同前缀长度的 KV 探针：GPU 热命中与 CPU 回载逐层逐块 bitwise 一致；受控 4 案例输出一致。
- [x] 2026-09-26 在原始串行多轮序列中复现 2/16 输出差异；探针不改变文本，96 次 chunk 回载、3456 次逐层比较全部 bitwise 一致且参考完整。见 experiments/2026-09-26/README.md。
- [x] Eager 对照：原生/立即卸载/探针三组各 16 条输出完全一致，3456 次 KV 比较通过；尚未独立区分编译与 CUDA Graph 的影响。
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
- [ ] 将owned完成结果接入StorageManager的初始L1前缀/L2索引合并、纯L1一次消费，再对接LookupModule/RETRIEVE；读取数据也须验证token和内存生命期。不能在完成bitmap返回后反查key补造token。
- [ ] 为无 END_SESSION、迟到 LOOKUP、controller 永久不完成与 server shutdown 实现并验证安全所有权协议；优先落实reservation/epoch和controller终结的接口，再决定客户端失联如何转移job。复核当前上游版本并形成可维护最小补丁。继续补实际传输中止、worker层真实写入失败、DMA执行期抢占及内容正确性；资源门槛通过后再决定是否修改GPU保护/准入。
- [ ] 单独复核异步调度的 prefix reset API 与 deferred block free 时序；EVICTION_AWARE自然抢占延迟轮session TTL已实测归零，当前 reset API 的失败不能算作生成或资源泄漏，active_sessions 也不能作为回收通过证据。
- [x] 完成首版不接 GPU 的 fake worker 状态机：预算、连续前缀、身份复核、pin/unpin、部分保存、取消和 generation 回执隔离；17 项测试、6,000 步固定种子交错以及 14 个执行示例通过，见 [PROTECTION_STATE_MACHINE.md](experiments/2026-09-30/PROTECTION_STATE_MACHINE.md)。
- [x] 增加 16-token 物理 block / 256-token LMCache chunk 的 16:1 CPU 分组模型，七项测试验证整 chunk 预算、失效 block 停止前缀和回执范围；不是实际 GPU adapter。
- [x] 真实 vLLM BlockPool 的纯 CPU 元数据桥接八项测试通过；新增同 request-id 跨代单在途限制及 [PROTECTION_ADAPTER_CONTRACT.md](PROTECTION_ADAPTER_CONTRACT.md)，明确 block/chunk hash、零步回滚和 worker 布尔回执限制。
- [x] 从真实token ledger计算vLLM/LMCache两条hash链，6项测试含Request追加decode；新增真实STORE metadata CPU交接桥接，7项测试与原生tracker字段对照，不接GPU/RPC。
- [ ] 完成真实 adapter 契约：准入前需求预算、候选选择与真实 hash 复核、前缀闭合、pin/unpin、STORE 提交/回执和保护预算回退；原生 lookup 取消资源门槛通过后再做 GPU 消融，不替换默认策略。

交付：可启用和禁用的实现、测试、消融及反例。性能门槛由基线噪声与实际需求决定，不先填加速目标。

## P4：复核与展示

- [ ] 固定开发/验证会话集合，多次重复，保留退化配置。
- [ ] 用 Qwen3-8B＋5090 复核；如要区分模型/硬件效应，需要额外控制变量实验。
- [ ] 统计客户端就绪到响应及会话总完成时间，不将等待隐藏到入口外。
- [ ] 整理复现文档、版本清单、原始数据、图表和个人贡献。
- [ ] 视结果决定独立项目发布或小范围上游贡献。
- [ ] 项目阶段收尾后重新核查 LMCache/vLLM 上游状态；若仍可复现且有可维护的最小修复与回归测试，考虑提交异步回载压力信号的上游 issue/PR，范围见 [UPSTREAM_ASYNC_KV_PRESSURE.md](UPSTREAM_ASYNC_KV_PRESSURE.md)。

## 优先顺序

P0/P1 已建立可运行环境，P2 基线仍有参数扫描与更多压力点未完成。P3 已完成 allocator 真值计数消融、需求信号诊断和若干生命周期契约/诊断实验。原生 Connector 的远端 lookup 等待期取消在未修改 server 上三次残留 17 个 CPU 读锁，r3 另有 1 个 prefetch job；可选 server 回收候选在相同受控 L1 路径两次使 17 个对象锁和 job 归零，follow-up 正常。真实 FS L2 在途 prefetch 的受控取消已完成两轮候选及一轮失败对照；END 顺序与短读候选已通过；无 END_SESSION 的进程死亡则在 630 秒后仍残留 job/result，TTL 匿名释放还有所有权风险。永久卡住的 controller 和自然故障尚未完整验证，因此阶段 3B 仍未通过。非法 RETRIEVE underflow 的读锁缺口单独保留。显式抢占的默认异步 reset API 仍返回 500；自然抢占及受控迟到回执资源闭合、自然抢占退出session TTL已验证；真实传输中止、worker层写入失败、DMA期间抢占及内容正确性仍待复核。真实EFBIG仅覆盖L2。`compute_slots` 漏掉异步回载突发，宽泛及 lookup 状态估计又有较多无效报警，当前不启用提前 pin/准入保护。fake worker状态机、真实BlockPool/hash和STORE metadata的CPU契约已推进；待生命周期门槛通过后再进入GPU策略消融。上游 PR 留待项目阶段收尾评估；输出差异和基线参数扫描也仍未完成，暂不需要更贵的 GPU 或完整 SWE-bench。
