# 待办与阶段验收

更新：2026-09-24。未勾选项均未完整完成；历史设计与当前实测环境以 ENVIRONMENT.md 及对应日期的记录区分。

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
- [x] 增加 EVICTION_AWARE decision ledger，记录 admission、danger depth、emitted、dropped_evicted、提交和前缀摘要。
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

交付：可启用和禁用的实现、测试、消融及反例。性能门槛由基线噪声与实际需求决定，不先填加速目标。

## P4：复核与展示

- [ ] 固定开发/验证会话集合，多次重复，保留退化配置。
- [ ] 用 Qwen3-8B＋5090 复核；如要区分模型/硬件效应，需要额外控制变量实验。
- [ ] 统计客户端就绪到响应及会话总完成时间，不将等待隐藏到入口外。
- [ ] 整理复现文档、版本清单、原始数据、图表和个人贡献。
- [ ] 视结果决定独立项目发布或小范围上游贡献。

## 优先顺序

先完成 P0 → P1 → P2，再决定 P3。初始基线表明压力负载中立即卸载优于默认延迟卸载，而小工作集原生路径更快。下一步先解释输出差异，再追踪 dropped_evicted 与后续重算的联系；两点 horizon 试验不代替完整参数扫描。尚不需要更贵的 GPU 或完整 SWE-bench。
