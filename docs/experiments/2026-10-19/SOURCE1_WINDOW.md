# source 1 尾部保存：窗口与原生 deadline 对照

2026-10-19。主线只回答：event 1 已计算但未保存的连续 4,352-token
前缀，能否在 GPU block 哈希失效前保存，并被 event 9 实际复用。
本轮是机制诊断，不进行延迟比较，不并入先前性能矩阵。

## 已完成的只读窗口诊断

`source1-window-20261019b` 完成原匿名轨迹 60 请求、2,880 输出 tokens，
16 项独立资源、身份、generation、冻结源与输入审计通过。
source 1 的 pending 依次加入 `[0,2048)`、`[2048,4096)`、`[4096,4352)`。
254 次 pending 快照中，`free >= whole_step_reserve + 16` 满足次数为 **0**；
容量余量最佳仍为 −649 blocks。这里的 reserve 是现候选的整步全请求包络，
不是原生当前 allocate_slots 的需求。

最后完好快照 253（schedule_before）：请求已完成、没有 STORE 在途，
free=302、reserve=2,081；272 块均 ref=0、哈希完好。头部 `[0,2048)`
在 free rank 174–301，中段在 46–173，最后 256 tokens 在 30–45。
下一快照 254（policy_drain_before）：free=181，中段 75 块和最后 16 块
已经被复用、哈希改变；头部仍完好。普通 STORE 最终只成功 `[0,2048)`。

因此当前 reserve 下没有额外保护窗口；仅把选择目标从头部改为尾部不能
解决容量准入。观察器会改变时序，本轮 event 9 的重入与先前轮次不同，
不能把这些差异归因于策略，也不能把首次匹配缺口当实际执行重算量。
`actionable_current_front` 只是候选诊断，不能替代 CPU 连续驻留证明。

首次 a 因隔离目录缺 `cachepilot_reservation_native` 启动失败；只补同源
已构建扩展的目录链接后，b 完成，a/b 原始证据分别保留。

## 下一单格对照的预注册

在启动前固定：`source1-deadline`，CachePilot 保护上限为 0，只把 LMCache
已有的 `lmcache.mp.lazy_offload_max_deferral_seconds` 从 0 改为 **0.5 秒**。
0.5 是单点机制探测值，没有最佳参数或性能调优含义。继续使用相同只读
source 1 观察器；新阶段、新冻结源码和独立输出目录，不覆盖原窗口证据。

其余条件保持：Qwen3-4B BF16、4090 24GB、2 GiB GPU KV、16 GiB CPU KV、
horizon=5、max_drain=1、idle grace=15 秒、同步 eager、TP1、max seq=4，
原 60 请求到达与 prompt、每请求 48 个输出 tokens。策略不读取未来 trace；
event 1/9 仅用于只读观察和离线核算。不上新性能矩阵，不放宽 reserve。

判定顺序：

1. 请求与 tokens 完整；原 generation、native transfer、锁、job、pin、
   GPU 注册等闭合，保护提交必须为 0。
2. 每段 source 1 STORE 必须有同 raw/wire/batch 原回执、相同物理块和
   sequence 的 native terminal 与成功退休；连续区间才可合并为完整保存。
3. 独立查看 event 9 的每个 generation，跳过未解析 lookup，区分 GPU
   命中、已确认 CPU RETRIEVE 和剩余缺口。STORE 成功史不等于后续驻留；
   本对照不宣称同来源逐层 KV bitwise 正确性或性能收益。
4. 若原生 deadline 保存并复用了尾部，承认它是原生基线能力；后续策略
   的问题变为相对该基线保住复用且减少无效 D2H/开销。若未解决，保存
   精确失败链条，再决定是否需要新准入或触发位置，不连续调参数追通过。

## 原生 deadline 对照结果

`source1-deadline-20261019a` 完成 60 请求、2,880 输出 tokens，17 项独立
审计通过，保护提交与保护 pin 峰值均为 0，运行期 fatal 和关闭期 traceback
均为 0。69 次 pending 观察没有记录到 source 1 哈希失效；其保护 reserve
仍无合格窗口。三段普通 STORE 全部获得原批次回执与 native 成功终结：

| 普通 STORE 区间 | 原批次成功完成 monotonic_ns |
|---|---:|
| `[0,2048)` | 12712086389680 |
| `[2048,4096)` | 12712188903589 |
| `[4096,4352)` | 12712307769777 |

event 9 首次已解析 cache-match 观察为 12714787763079，GPU=1,664、
external=2,688，完整 4,352-token 目标没有未覆盖部分；相应 RETRIEVE
有同 wire/物理块的 native terminal 与成功退休。只有一个 generation。
这证明原生 deadline 在本格保存并复用了目标；不宣称本格完成逐层 KV
全等验证，也不把带观察器的结果用于延迟比较。

因此先采用原生 deadline 作为新的相关基线，停止为这一缺口直接设计
自定义尾部 pin。后续自定义策略的门槛是相对这个基线的净价值。

## 关闭诊断后的匹配实验预注册

继续复用 `run_whole_step_efficiency.py`，增加显式 `native-deadline`
comparison：`zero` 与 `native-deadline` 两格均为 WholeStepConnector、
max_chunks=0，唯一配置差异是原生 max_deferral 从 0 变为 0.5 秒；
没有 Source1WindowConnector、逐步 handoff/KV 探针或稳定 ID 观察器。

先运行同源两格 pilot，全部请求、资源、generation、原始计时和日志门槛
通过后，才允许同一冻结源码的三重复正式六格。pilot 不进入正式均值。
顺序预定为 zero→deadline、deadline→zero、zero→deadline；保留所有
轮次，不重跑筛选。主指标 TTFT P95，同时报告 prefill、H2D/D2H、抢占、
逐条输出 SHA 差异。三重复仅描述性结论，单轨迹不代表泛化，原生配置
收益也不能记作 CachePilot 自定义策略收益。

遇到失败则保存原格并停止扩展，仅处理该失败；无一致信号则报告混合或
负结果，不新增 deadline 搜参矩阵。这一实验完成后再决定调度策略的
具体目标，不同时扩大模型、数据、并发模式或硬件。

## 匹配 pilot 与 formal 已完成

`native-deadline-pilot-20261019a` 两格均通过，120 请求/5,760 输出 tokens，
保护提交及 pin 峰值均为零，generation、资源、计时和日志门槛全部通过。
zero/deadline 的 TTFT P95 为 1,891.770/1,891.177 ms，computed prefill
为 36,477/27,533 tokens，H2D 为 2,000,683,008/2,340,421,632 bytes，
D2H 为 3,661,627,392/3,963,617,280 bytes，抢占为 2/0。单轮computed prefill减少
但 P95 几乎持平，不能据此认定性能收益或将计算差全部视为重算。81 项证据 SHA 全部核对，
本地与服务器独立审计 JSON 一致。

formal `native-deadline-formal-20261019a` 在两格全部通过及源码逐项核对后启动，并于23:52前完成6/6，driver PID14268已退出。正式360请求/17,280输出tokens，TTFT P95三次配对−17.36%/−12.14%/−8.13%，各格均值−12.58%；computed prefill−18.24%，H2D/D2H+8.62%/+11.83%。192项归档SHA、server/current/frozen/archived审计均一致。详情与输出差异见[正式报告](NATIVE_DEADLINE.md)。服务器空闲、容器开机；不要重复启动。

续接时另核实了源码清单：机制与 pilot 的运行时冻结清单各为 304 项，
变化精确为 `scripts/run_whole_step_efficiency.py`，现有前置检查正确通过。
外层源码交付包为 306/307 项，额外包含测试，新增的是
`tests/test_native_deadline_comparison.py`；它们不属于运行时清单。
因此不需要放宽前置检查，也没有为此修改正在运行的源码或重跑 pilot。
相关三文件离线测试本地和服务器各 18 项通过；真实库开销测试在 Windows
单独跳过，不把它计入 18 项或算成真实栈通过。
