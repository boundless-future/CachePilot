# 当前实验主线（2026-10-20）

## 要回答的问题

本轮已回答：在固定匿名六会话多轮压力轨迹上，LMCache 已有的原生 deadline 能否补上丢失尾部的复用缺口，关闭诊断后相对 deadline=0 的重算、搬运和 TTFT 净效果如何。三次配对 TTFT P95 均下降，均值降低12.58%；computed prefill减少18.24%，H2D/D2H分别增加8.62%/11.83%。这是 LMCache 原生配置在 CachePilot 生命周期适配层上的描述性基线，不是自定义策略收益。完整逐格证据、原始数据分析和限制见[正式报告](experiments/2026-10-19/NATIVE_DEADLINE.md)。

整步保护已完成接线、同来源 KV 正确性准入及三重复比较，但稳定性能收益未成立；下文保留这条证据链。当前不再扩大无新增复用价值的头部保护候选。

当前是 **3C：策略端到端验证**。2026-10-19 整步候选的真实服务覆盖已完成 one/two/zero 三格，180 请求及 8,640 输出 tokens 完整，两保护组各有一批实际成功 STORE，峰值16/32 blocks，所有资源与 generation 闭合。严格同来源KV准入one/two两格通过，分别1/2 chunks、36/72层比较全等；服务器及本地均复算通过。无逐步探针pilot 3/3及同源正式矩阵9/9已通过，正式540请求/25,920输出tokens。one三轮都退化、TTFT P95均值+2.97%；two均值−2.46%但一轮退化+5.05%，稳定收益未成立。详见[正式报告](experiments/2026-10-19/WHOLE_STEP_FORMAL.md)。原匿名旧策略 pilot 仅 3/4 通过、双块零提交的历史失败保持有效。

随后聚焦净开销：真实栈42项通过，无候选早退的zero/legacy-two/filtered-two pilot 3/3及formal 9/9。filtered相对legacy每轮prepare wall降低73.67–74.78%，TTFT P95配对每轮快0.90–1.19%，三轮同向但仅单轨迹描述性结果。覆盖、重算和传输量有轮次变化；没有把延迟差归因于早退。571文件证据的服务器/current/frozen报告和SHA一致。完整记录见[NET_EFFECT](experiments/2026-10-19/NET_EFFECT.md)。

## 执行顺序与验收

1. **真实服务覆盖实验**：显式 `WholeStepConnector`，先 `one`，通过才跑 `two`、`zero`。复用原始 60 请求匿名轨迹，Qwen3-4B、4090 24GB、BF16、2 GiB GPU KV、16 GiB CPU KV；同步 eager、单 worker；horizon=5、drain=1。冻结代码、输入和 tokenizer，不换轨迹或降低预留以追求通过。
2. **机制正确性准入**：第一步必须请求及 token 数完整，保护组有实际提交及成功回执，pin 峰值不超过 16/32，generation 全终结，锁、job、传输、GPU 注册回收，服务无运行期错误。第一步仅证明保护提交与整体 H2D 均发生；不能据此宣称同一保护 chunk 被回载。后续对实际保护来源做稀疏 STORE→RETRIEVE KV 全等审计；已有上游/旧策略正确性证据不能替代本候选。
3. **同源码匹配 pilot → formal**：机制准入后冻结新计划，zero/one/two 匹配 pilot 全通过才运行轮转三重复正式比较；计时关闭逐步探针。主指标为 TTFT P95，联合分析重算 prefill tokens、H2D/D2H、保护覆盖和抢占，保留每次结果及输出差异；同一重复对比 zero，并展示全部重复及均值，不筛掉退化结果。正确性通过、覆盖增加、性能收益分开判定。三重复只支持描述性结论，不预设必须得到正收益。

## 本阶段的完成标准与后续分支

| 关键问题 | 本轮证据 | 完成后采取的动作 |
|---|---|---|
| 策略是否实际进入数据通路 | 原轨迹中的保护STORE提交、服务成功退休、原批次回执；资源闭合 | 已通过，继续正确性及效果比较 |
| 保护来源的KV能否正确恢复 | 相同STORE来源到后续RETRIEVE的逐层bitwise比较，排除中间STORE | 已通过；该覆盖有限，不能外推全部异常路径 |
| 保护相对同参数零保护的净效果如何 | 无逐步探针、同源码输入、轮转三重复TTFT P95；同时列prefill、搬运、抢占与每次结果 | 全部完成后给出正/负/混合结果，不以测试通过代替优化成立 |
| 是否值得作为实际优化继续投入 | 上述收益与代价，以及未覆盖的工作负载/原生与默认基线 | 有一致信号再固定候选做独立负载和完整基线；混合或退化则先分析机制，停止盲目扩大实验 |

正式矩阵的zero与one/two仅改变保护上限，统一h5/d1和WholeStepConnector；zero并非原生vLLM或未调参的LMCache默认配置。本阶段不能单凭这三组宣称胜过原生/默认，也不能宣称真实Agent任务质量或泛化收益。匿名整数token轨迹固定请求和到达计划，decode仍实际执行；输出差异逐条归档。

## 阻断处理规则

一个实验失败就停止向后扩展，保存失败目录、日志、配置、源码和判定。只开展能解释该阻断的最小诊断，修复后重跑同一输入与条件；源码变化则建立新冻结版本，不把前后版本拼成同一组正式结果。若无保护提交，先判定触发/容量局限；若正确但无收益，则报告无收益并分析原因，不无限调参。

本阶段暂不扩大模型、数据集、硬件、异步、多 worker，也不以补齐所有边界测试代替主实验。确实影响当前主线的异常需修复，其他问题进 backlog。

## 交付

一份可以复算的主实验报告：冻结条件、逐格结果、成功/失败证据、适用边界、相对基线的效果与成本。运行记录见 [TASKS](TASKS.md)，实验入口为 `scripts/run_whole_step_mainline.py`；运行时仍统一在 `src/cachepilot`。

## 身份诊断已完成：停止扩大当前头部保护候选

稳定trace ID诊断zero/legacy-two/filtered-two共180请求、8,640输出tokens完整，3/3通过。两保护格均只保护event 8的`[0,512)`；后继11/14/17/19/24/25在三格全部GPU命中，长度逐条相同且external=0。没有观察到保护新增CPU回载或避免重算的价值，按上面的无收益分支停止扩大本候选性能实验。先前无候选早退的开销证据单独保留。详细结果、关闭期错误及补充v3审计见[身份诊断](experiments/2026-10-19/IDENTITY_DIAGNOSTIC.md)。

## 已完成：丢失尾部的保存窗口与原生能力定位

只聚焦同会话event 1→9：完整共享4,352tokens，zero首次已解析cache-match只覆盖GPU1,792/external0；source 1普通STORE成功区间仅`[0,1792)`。自然抢占后命中会变化，这个2,560token缺口只是机会定位，不能当实际执行重算量。两保护格在source 1都记录free302/reserve2,081的容量拒绝；当前从0截取头部的候选不能直接保护任意尾部offset。

只读窗口诊断已完成：254 次 pending 观察没有满足当前整步 reserve 加一个 chunk 的容量窗口；最佳余量仍为 −649 blocks。最后完好状态 free=302、reserve=2,081，随后中尾部哈希失效，普通 STORE 仅连续保存 `[0,2048)`。仅把保护目标改成尾部无法绕过当前容量准入。观察器改变时序，不能与旧轮比较性能。

随后单格机制对照只将已有 `lmcache.mp.lazy_offload_max_deferral_seconds` 从 0 设为 0.5 秒，保护为零。60 请求/2,880 输出 tokens 完整，原回执、native 终结和资源审计通过。source 1 三段普通 STORE 连续保存完整 4,352 tokens，event 9 从 CPU 回载 2,688 tokens、GPU 命中 1,664；没有未覆盖部分。这是原生能力证据，不能算自定义策略收益，也不等于逐层 KV 全等。失败启动和原窗口轮分别保留。详见 [SOURCE1_WINDOW](experiments/2026-10-19/SOURCE1_WINDOW.md)。

## 已完成：关闭诊断的原生 deadline 匹配比较

复用 `scripts/run_whole_step_efficiency.py --comparison native-deadline`。zero/deadline 两组均为显式 WholeStepConnector、max_chunks=0，唯一配置差异为 deadline 0/0.5 秒；原模型、输入、到达、h5/d1/grace15 和资源预算不变，关闭逐步观察器。pilot 2/2、同源三重复 formal 6/6 全部通过，顺序 zero→deadline、deadline→zero、zero→deadline。

deadline 0.5 三轮的 TTFT P95 配对降幅为17.36%、12.14%、8.13%；六格请求、generation、服务端资源及 transfer 终结全部通过。pilot 单轮 P95 几乎持平的结果保留且不并入 formal 均值。证据 SHA、服务器/本地/冻结源码复算一致，正式归档 SHA 为 `838cdc47881fa23f62d1b845f2b8ae1421510a2bdba08f0821ec44d8c4fe16cd`。服务器端口8000/8080/5555空闲，GPU1MiB/0%，容器保持开机。详见[NATIVE_DEADLINE](experiments/2026-10-19/NATIVE_DEADLINE.md)。

## 下一条主线：找出可在线判断的选择信号

已完成source1原生deadline记录的区间交叉复算：event9有效CPU回载为`[1664,4352)`（RETRIEVE start1536/skip128），与三段成功STORE相交384/2,048/256tokens，恰好覆盖全部2,688tokens。三个candidate-add快照均active、未finished、block ref1/无free-rank；没有依据从这条样本直接删掉任一段，也没有依据仅按finished/free-rank筛选。采样不是每个决策瞬间的证明，逻辑区间相交不等于唯一生产来源或逐层KV全等。见[区间证据](experiments/2026-10-19/evidence/source1-deadline-reuse-analysis.json)。

下一唯一事项：用已有全轨迹成功STORE与后继GPU/CPU路径做有边界的离线分类，优先否决会漏掉已确认复用的规则；找得到当时已知的pending age、请求状态或压力等信号，才改统一框架的一个选择模块，真实栈正确性准入后与deadline=0.5做匹配pilot/formal。没有完整证据就结束当前筛选假设，转固定配置的独立负载验证。未观测到回载不等于可安全删除；不把全局搬运差额硬分摊到单次STORE，不放宽reserve。

该阶段先不扩大模型、轨迹、硬件或 deadline 参数扫描。正式报告和运行日志见 [TASKS](TASKS.md)，原始分析为 [native deadline report](experiments/2026-10-19/NATIVE_DEADLINE.md)；event ID 仅供离线核对，策略始终不得读取未来 trace。
