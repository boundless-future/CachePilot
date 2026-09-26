# 无日志策略对比与 KV 丢弃诊断

## 本轮问题与实验边界

上一版 AdaptiveConnector 继承 DecisionConnector，会逐步遍历 free queue、计算前缀摘要、写 JSON。其耗时不能直接与无诊断日志的默认策略比较。本轮将策略和观测分离，再追踪 deferred STORE 候选的丢失过程。

- 性能实验：默认 EVICTION_AWARE 与自适应 horizon，各在 4/12 会话下重复三次，共 12 个实验单元、384 条测量请求。相同 trace、引擎初始化、warmup、GPU KV 预算，轮换策略顺序。两种策略均不启用 decision ledger。
- 诊断实验：单独在 12 会话压力负载上运行 DecisionConnector。它会影响时序，因此只解释机制，不作为性能数据。
- 环境沿用本日 [环境记录](environment.json)：RTX 4090 24GB、Qwen3-4B、vLLM 0.30.0、固定 LMCache 源码。GPU KV 人为限制为 2 GiB；不能将结果外推为整张 24GB 卡的自然容量表现。
- 固定输入回放：4 轮、48 输出 tokens，生成文本不进入下一轮输入。它衡量系统行为，不代表真实 Agent 任务成功率。

## 实现

AdaptiveConnector 直接继承上游 LMCacheMPConnector；独立的 `horizon_policy.py` 根据本步新增块数与下一步估计的最大值切换 horizon，阈值 16 blocks，窗口 2.5/5.0。保留上游哈希检查、前缀闭合、pin/unpin、提交和回执流程。

配置仍叫 `adaptive-trace.json` 以保留原有启动入口，但 **adaptive 模式已不含逐步诊断日志**。旧环境变量 `CACHEPILOT_ADAPTIVE_WIDE_HORIZON` / `CACHEPILOT_ADAPTIVE_BLOCK_THRESHOLD` 不再读取，改用 JSON 中的 `cachepilot.wide_horizon` / `cachepilot.block_threshold`。`decision` 模式单独启用诊断。

启动器自动加入项目根目录到 PYTHONPATH。每个实验单元保存配置和脚本 SHA256、模式、KV 预算及诊断开关；完整脚本快照随原始数据归档。哈希中包含未加载的其他模式脚本，分析版本一致性时应区分实际加载路径。

## 无日志对比结果

384 条请求无 HTTP/SSE/长度错误。表中是三次运行各自复用轮 TTFT P95 的均值与范围，不是合并请求的 P95，也不是置信区间。

| 负载 | 默认窗口 2.5 | 自适应窗口 2.5/5.0 |
|---|---:|---:|
| 4 会话，工作集可驻留 GPU | 82.8 ms（79.3–87.6） | 82.3 ms（77.8–85.2） |
| 12 会话，超过 GPU KV 容量 | 3685.5 ms（3576.4–3766.7） | 3694.1 ms（3630.3–3751.0） |

压力负载中，自适应平均慢约 0.23%，远小于本轮重复波动；**没有证据表明当前切换窗口能改善延迟**。两者平均实际 prefill 计算量都为 55,661 tokens，外部命中都为 45,739 tokens。自适应平均 D2H 约 3.36 GiB，默认约 3.19 GiB，反而多搬运约 5.5%；H2D 均约 6.28 GiB。低压力下两者都没有 CPU 搬运。

这些指标只说明本组负载中的表现，三次重复不足以证明两者在所有场景等价。本轮没有重新测原生/立即卸载，不能把前一日的绝对延迟与本轮数据混为同轮排名；也未以输出文本相同作为通过条件。输出正确性证据与限制见本日独立 KV 探针报告。完整逐次结果见 [fair-horizon-analysis.json](fair-horizon-analysis.json)。

## 诊断口径

DecisionConnector 只观察分配，不改变块队列、引用计数或实际分配结果：

1. 在 `allocate_slots` 调用期间记录请求、外部命中 token 数和异步回载标志。
2. 在 `get_new_blocks` 清除哈希前，记录本次将复用的块与待保存候选的交集，核对旧哈希仍然有效。
3. 记录本调度步实际分配总数；与上游传给策略的 `new_blocks_allocated` 分开保存。
4. 丢弃时记录每个请求中第一个失效操作，以及后续操作是否也已变哈希。操作个数不等于独立失效事件数。
5. 用 token 前缀 SHA256 关联后续更长输入的 GPU/CPU 命中范围。只计入已知复用时间之后的 lookup；不将源请求本身或更短输入当作后续复用。

`analyze_decision_timeline.py` 一次只分析一个 scheduler ledger，避免混合进程和请求 ID。缺少 allocator 事件的旧账本只能给出关联，不能证明分配原因；旧账本缺少 first_lost 字段时输出 null。后续 lookup 的缺失范围也不等于独立的 GPU 重算耗时，更不能将多个重叠前缀的缺失 token 简单相加。

上游源码片段及完整文件 SHA256 见 [allocation-source-evidence.json](allocation-source-evidence.json)。`emitted` 按候选操作计数，`submitted_store` 按合并后提交计数，二者不同不代表漏提交。

## 分配诊断结果

单次压力诊断完成 48 条测量请求，均无请求错误；账本另包含 1 条 warmup。最终计数闭合：49 个 admission = 36 个 emitted + 11 个 dropped_evicted + 2 个 pending；36 个 emitted 合并成 19 次 STORE 提交。

11 个丢弃操作来自 6 个请求的首次失效点及其后缀。全部 11 个都有分配前哈希有效、随后实际被复用的证据；后缀本身也都出现了哈希变化，本次没有“自身哈希仍完整、仅因前缀断裂被丢弃”的案例。9 个操作能关联到后续相同前缀的缓存缺失，另外 2 个后续仍命中完整 2048-token 前缀，说明丢弃候选未必损害缓存收益。

| 调度步 | 实际分配 | 策略收到的新增块数 | 观测 |
|---|---:|---:|---|
| 179 | 256 | 128 | 128 个异步回载块未在本步信号中体现；另一请求正常分配 128 块。前一步窗口只有 3 块，候选位于队列第 5/21 位，随后已被复用。 |
| 329 | 512 | 0 | 4 次异步回载各分配 128 块；策略窗口仍为 3，2 个候选丢失。它们的后续前缀仍命中，因此不能据此声称性能损失。 |
| 333 → 346 | 333 步分配 128；346 步分配 6 | 333 步未 drain；346 步报告 134 | 333 步异步回载已复用候选；由于 332–345 步没有模型 tokens，manager 不调用 drain。346 步恢复执行才发现丢失，后续相同前缀存在缓存缺失。 |

详细逐候选时间线见 [allocation-timeline.json](allocation-timeline.json)，精选调度步原始字段见 [allocation-step-evidence.json](allocation-step-evidence.json)。队列 rank 从 0 开始；rank=null 表示候选块当时不在 free queue，不能解释为它一定安全。free block 净变化还受释放、命中占用等影响，不能替代实际分配计数。

结合源码，本轮确认的是 **压力信号与真实分配存在时间错位，以及决策发生在分配之后**：异步回载先占用块，请求等待回载时不进入普通执行列表；后续恢复执行时，先前分配的块又可能出现在新请求 block_ids 中。因此这个信号既可能当步漏记，也可能后续高于当步真实分配，不能简单称为永久漏计。没有 token 的调度步还会跳过 drain。

在这些条件下，仅根据当前信号将 horizon 从 2.5 放大到 5.0，无法保证提前识别下一次突发分配。本轮结果支持继续研究提前获知分配需求，但尚未证明修改信号就一定更快：事后补记 512 块，也救不回已经被覆盖的数据。

诊断运行在请求全部完成、final counters 输出后，API 停机阶段出现 `AsyncLLM output_handler` / `EngineDeadError` 日志；本轮 12 个性能单元未出现 ERROR 日志。该停机异常保留在原始归档中，不把诊断运行表述为所有生命周期路径通过。退出后已检查 GPU 占用及三个服务端口清空。

## 下一阶段：先做分配信号消融，再判断是否前移决策

1. **最小信号修正原型。** 在实际 block allocation 路径只累加计数，按 scheduler step 消费；替换现有计数时避免回载恢复后重复记账。先保持 drain 时机不变，用它检验“更准确的历史压力信号”是否能改善后续决策，并明确它无法挽救同一步已经丢失的 KV。
2. **若事后信号仍不足，再研究提前预算。** 在准入/分配前获得等待请求的 prefill 与回载块需求，提前安排卸载。必须给异步 STORE 留出执行和完成时间，保留 manager 的哈希复核与 pin/unpin，不能在 allocator 回调内盲目 pin 已选中块或直接启动未同步拷贝。是否需要小范围 scheduler hook 要由此阶段的实现验证决定。
3. **有限消融及退出条件。** 默认策略、仅修正信号、提前预算三组先跑同一压力点；同时保留立即卸载和固定 horizon 对照。评估实际 prefill、复用 TTFT P95、D2H/H2D 和排队。丢弃变少但重算/延迟没改善，或小工作集搬运明显增加，就不宣称优化有效。
4. **安全与泛化。** 策略改变生命周期或保护时机之前，补取消、抢占、保存失败和 pin 回收测试。机制有效后再扩展 GPU KV 容量、突发负载与真实结构多会话，避免先扩大参数网格。

编译与 CUDA Graph 的独立消融仍未完成；它属于输出差异归因的另一条工作线，本轮没有因传输探针通过而将其标记为解决。

## 复现

在服务器 cachepilot Conda 环境、项目根目录下执行，每次使用新输出目录，并串行运行，避免争抢单张 GPU：

```bash
python scripts/run_baselines.py --modes eviction adaptive --repeats 3 \
  --output artifacts/fair-horizon-new
python scripts/summarize_baselines.py artifacts/fair-horizon-new \
  --output artifacts/fair-horizon-new/analysis.json
python scripts/run_baselines.py --modes decision --workloads exceeds-gpu --repeats 1 \
  --output artifacts/allocation-diagnostic-new
python scripts/analyze_decision_timeline.py \
  artifacts/allocation-diagnostic-new/exceeds-gpu-decision-r0/decisions/scheduler-<pid>.jsonl \
  --output artifacts/allocation-diagnostic-new/timeline.json
python -m unittest discover -s tests -v
```

诊断模式没有强制保存剩余候选；工作负载结束后尚未保存的候选保留为 pending，不另外发请求人为推动 drain。

## 归档与验收

原始数据、两次实验驱动日志、输入 trace、每单元版本清单、诊断账本、运行脚本/配置/测试及所用上游源码已归档到本地和服务器的 `artifacts/strategy-diagnosis-evidence-2026-09-26.tar.gz`。SHA256 为 `8ed7d99be90acd873b0fdf76b8a070d23867d602c51f885c0fa888cd9585e973`，两端校验一致；详见 [归档清单](strategy-diagnosis-evidence-manifest.json)。性能实验实际加载的策略、启动、输入生成与回放文件逐一匹配运行时 SHA256。

18 项本地测试通过，Python 语法编译和 diff 空白检查通过。GPU 服务结束后显存占用 1 MiB、GPU 利用率 0%，8000/5556/8081 端口均已关闭，服务器保持开机。
