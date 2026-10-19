# 原生 deadline：重算、搬运与端到端净效果

2026-10-19 23:52（北京时间）完成。本轮正式六格全部通过，360 请求、17,280
输出 tokens 完整。相对 deadline=0，原生 0.5 秒 deadline 的三次配对 TTFT P95
变化为 **−17.36%、−12.14%、−8.13%**，P95 均值降低 **12.58%**；computed
prefill 减少 **18.24%**，H2D/D2H 分别增加 **8.62%/11.83%**。

这是单轨迹三重复的描述性结果，支持把该配置作为后续自定义策略的相关基线。
它使用 LMCache 已有能力与 CachePilot 生命周期适配层，不是新保护策略的收益，
也不是未修改上游 LMCache 或原生 vLLM 的完整对照。

## 问题与固定条件

此前已确认当前头部保护没有新增复用价值；source 1 丢失尾部在现整步 reserve
下没有保护窗口，而原生 deadline 已在独立机制格保存完整 4,352-token 前缀，
event 9 实际 CPU 回载 2,688 tokens。由此固定本轮问题：关闭逐步探针后，这项
原生能力是否能用额外搬运换来更少计算及更低 TTFT？机制证据见
[SOURCE1_WINDOW](SOURCE1_WINDOW.md)，它的时延不进入本轮统计。

- RTX 4090 24GB，Qwen3-4B BF16；GPU KV 2 GiB、CPU KV 16 GiB。
- 同步 eager、TP1、单 worker、max-num-seqs=4；horizon=5、max_drain=1、grace=15s。
- 同一个匿名六会话整数 token 压力轨迹，60 请求/格、48 输出 tokens/请求；
  原到达与 prompt 不变，decode 实际执行。trace SHA256：
  `445b05df9898232b97d2a0f7a0fc7633a42ab8a4d712943ee3f70ebb6c2acb83`。
- 两组都是显式 WholeStepConnector，max_chunks=0；唯一配置差异为
  `lmcache.mp.lazy_offload_max_deferral_seconds=0/0.5`。保护提交、pin 峰值、
  prepare 调用均为 0；没有 Source1WindowConnector、逐步 KV/handoff 或稳定 ID 探针。
- 沿用 Python 3.12.14、vLLM 0.30.0、LMCache 0.5.5+g1a4b40b1d、torch 2.13.0+cu130。
  pilot/formal 使用完全相同的 304 项运行时冻结源；外层 307 项源码包另含测试。

主指标预先固定为 TTFT P95。先完成两格 pilot 的资源、generation、计时及日志
准入，才执行 formal；顺序为 r0 zero→deadline、r1 deadline→zero、r2 zero→deadline。
未替换任何重复，未调整 deadline，pilot 不计入正式均值。

## 正式六格结果

| 重复 | 配置 | TTFT P95 ms | computed prefill tokens | H2D bytes | D2H bytes | 抢占 | generation/END |
|---|---|---:|---:|---:|---:|---:|---:|
| r0 | zero | 1,973.069 | 33,677 | 2,189,426,688 | 3,510,632,448 | 1 | 62/62 |
| r0 | deadline 0.5 | 1,630.502 | 27,533 | 2,378,170,368 | 3,925,868,544 | 0 | 61/61 |
| r1 | zero | 1,943.960 | 33,677 | 2,189,426,688 | 3,510,632,448 | 1 | 62/62 |
| r1 | deadline 0.5 | 1,707.888 | 27,533 | 2,378,170,368 | 3,925,868,544 | 0 | 61/61 |
| r2 | zero | 1,925.775 | 33,677 | 2,189,426,688 | 3,510,632,448 | 1 | 62/62 |
| r2 | deadline 0.5 | 1,769.213 | 27,533 | 2,378,170,368 | 3,925,868,544 | 0 | 61/61 |

P95 均值为 1,947.601→1,702.534 ms（−12.583%）；这里是各格 P95 的算术均值，
不是把所有请求混合后重新计算分位数。每格少计算 6,144 prefill tokens，多搬运
188,743,680 H2D bytes 和 415,236,096 D2H bytes。computed prefill 是实际执行
量，包含可能的重算；不能把它全部称为可避免的重复计算。

| 辅助量（三格均值） | zero | deadline 0.5 |
|---|---:|---:|
| 请求平均 TTFT ms | 471.897 | 387.424 |
| turn=0 请求 TTFT P95 ms | 2,218.163 | 2,190.381 |
| turn>0 请求 TTFT P95 ms | 1,730.039 | 1,607.904 |
| 端到端时延 P95 ms | 3,111.880 | 2,787.336 |
| 客户端排队均值 ms | 1.195 | 1.256 |
| 服务端 request queue 均值 ms | 395.392 | 321.837 |
| 服务端 request prefill 均值 ms | 60.742 | 51.555 |
| 服务端 request decode 均值 ms | 1,158.363 | 1,124.429 |

turn 分组只表示轨迹轮次，不代表实际 CPU/GPU 命中分类。以上服务端 request
时间可彼此重叠，不能相加或与传输累计时间相减来分解 TTFT 的因果来源。

## 尾请求与搬运代价

从原始请求时间戳独立重建六格 P95，均与正式审计一致。每格 60 个请求使用线性
插值：P95 都由 event 9（权重 0.95）和 event 8（权重 0.05）构成。event 9 的
TTFT 配对降低 351.234/245.229/165.527 ms；event 10 降低
304.605/191.316/112.773 ms。event 8 在 r2 反而增加 13.759 ms，完整保留。
这加强了与先前目标请求的关联，但没有把无探针 formal 当作新的逐请求 KV 路径证明。
event ID 仅用于事后分析，运行时策略不得读取未来 trace。

原始 LMCache Prometheus 按 direction/phase 标签复算的 CUDA event staging
区间累计均值：H2D 87.044→94.111 ms，D2H 138.363→157.359 ms。两方向
staging bytes 与表中传输量逐格一致。kernel 区间也保存在分析 JSON，均值分别为
H2D 7.411→6.202 ms、D2H 20.139→13.739 ms；其区间包含流调度等待，不应当
解释为纯计算耗时。累计 DMA 区间不是请求关键路径，也不能据此断言额外搬运
已经造成某个请求的竞争或退化。额外 D2H 是否有可安全避免的部分仍待证明。

## 准入、输出差异与 pilot

六格每格 60 请求/2,880 输出 tokens，全部 HTTP 成功，原始计时、输入与源码、
无热路径 JSONL、generation/END、原回执、保护、锁/job/传输/GPU 注册闭合
均通过。六格运行期 fatal 和关闭期 traceback 均为 0；各有一条关闭期 CUDA IPC
warning，原日志保留。关闭错误分类没有排除任何 traceback。

deadline 相对同重复 zero 的输出 SHA 不同请求分别为 6/8/7 条：

- r0：9、11、12、15、16、49。
- r1：9、11、12、15、16、38、42、49。
- r2：9、11、12、15、16、31、49。

长度都为 48，但真实 decode 序列并不保证全等。本轮不声称新增同来源逐层 KV
全等验证、模型语义质量或 Agent 任务质量；此前其他候选的 KV 全等证据不能移用。

pilot 两格也全部通过，P95 1,891.770→1,891.177 ms（几乎持平），prefill
36,477→27,533，抢占 2→0。这一单轮与 formal 的 zero 调度路径不同，说明
收益幅度具有运行波动；原结果保留，未将它丢弃或并入正式均值。三次 formal
同向仍不等于统计显著、跨负载泛化或通用默认值建议。

## 证据、复算与收尾

正式目录：`/root/cachepilot-native-deadline-matrix-20261019/runs/native-deadline-formal-20261019a`。
正式归档含 192 文件，SHA256
`838cdc47881fa23f62d1b845f2b8ae1421510a2bdba08f0821ec44d8c4fe16cd`。
pilot 的 81 文件与外层源码的 307 文件也逐项 SHA 核对；formal/prerequisite
冻结源通过原审计，pilot/formal 签名一致。服务器、当前源码、冻结源码和归档内
independent-summary 的 JSON 全部一致。

提交前另核对本轮 `native-deadline-*` / `source1-*` 共 47 个证据文件：工作区与
Git 暂存内容原始字节一致。专用属性保留原换行符，避免克隆后输入 SHA 改变。

主要证据：

- [正式归档](evidence/native-deadline-formal-20261019a.tar.gz)、[逐文件清单](evidence/native-deadline-formal-20261019a-inventory.json)。
- [正式独立审计](evidence/native-deadline-formal-local-audit.json)、[原始请求与传输分析](evidence/native-deadline-formal-analysis.json)。
- [源码包](evidence/native-deadline-matrix-source.tar.gz)、[源码清单](evidence/native-deadline-matrix-source-inventory.json)、[综合核验](evidence/native-deadline-validation.json)。
- [启动记录](evidence/native-deadline-formal-launch.json)、[driver](evidence/native-deadline-formal-driver.txt)、[服务器定向测试](evidence/native-deadline-matrix-server-tests.txt)。

三个相关离线测试文件本地/服务器各 18 项通过；这不是新一轮全量 CUDA/native
回归，Windows 单独跳过的真实库测试不计作通过。实验工具仍在 scripts，运行时
唯一实现保持在 src/cachepilot；本轮未修改安装的 LMCache 库或策略运行时。

解包正式归档到独立目录并核对清单后，可无 GPU 复算：

```text
python scripts/run_whole_step_efficiency.py audit --directory MATRIX --output AUDIT.json
python docs/experiments/2026-10-19/evidence/analyze_native_deadline.py MATRIX --audit AUDIT.json --output ANALYSIS.json
```

原始审计保留完整准入；后一个事后分析脚本不替代它。冻结源码使用同一 audit
命令，可与当前结果逐 JSON 比较。分析 JSON 保存其输入 SHA。

23:52:59 服务器端口 8000/8080/5555 空闲、无实验进程，GPU 1 MiB/0%；容器
保持开机，见[空闲快照](evidence/native-deadline-final-idle.json)。没有再启动实验。

2026-10-20 00:04:37续接归档时再次通过SSH只读确认：上述端口仍空闲，无实验
进程，GPU仍为1 MiB/0%，容器开机。见[归档后快照](evidence/native-deadline-post-archive-idle.json)。

## 下一条主线与决策门槛

2026-10-20续接已完成一次离线交叉复算（没有新GPU实验）：机制格的event9
RETRIEVE元数据是start=1,536、skip=128、end=4,352，实际外部覆盖
`[1664,4352)`，有原native成功终结。它与source1三段成功STORE的交集如下：

| source1 STORE | 与确认CPU回载的交集 | tokens |
|---|---|---:|
| `[0,2048)` | `[1664,2048)` | 384 |
| `[2048,4096)` | `[2048,4096)` | 2,048 |
| `[4096,4352)` | `[4096,4352)` | 256 |

交集共2,688tokens，恰好覆盖确认外部回载；每段都与它相交，这条样本不能
支持删除任一整段。前三段candidate-add快照中，source1均active且未finished，
所涉128/128/16个blocks均ref_count=1、free_rank=null。仅按请求已结束或
free-queue排名选择，会在这些采样时刻漏掉后来有复用的区间。采样并不证明
所有可能决策时刻均如此；区间关联也不证明回载的唯一生产来源或同来源KV
逐层全等，不能把未实际回载的头部1,664tokens直接称为可删减搬运。

基于已核验机制审计与原始handoff的[可复算脚本](evidence/analyze_source1_deadline_reuse.py)
及[结果](evidence/source1-deadline-reuse-analysis.json)保存了输入SHA；独立于正式计时。
解包机制归档后，可执行：

```text
python docs/experiments/2026-10-19/evidence/analyze_source1_deadline_reuse.py RUN/zero-r0 --audit docs/experiments/2026-10-19/evidence/source1-deadline-local-audit.json --output REUSE.json
```

将 deadline=0.5 固定为后续相关基线，问题转为：**能否在保留其复用收益的同时，
用在线可见信号减少不必要的卸载？** 下一步复用已有完整轨迹的机制记录做
STORE→后继GPU/CPU复用分类，分清直接回载、连续前缀依赖、仅GPU热命中和证据不足。
窗口内没有观察到回载不等于条目可以安全丢弃，更不等于额外 D2H 全部无效。

只有找到可由当时已知的 pending age、free-queue rank、请求状态等信号区分的
候选，才修改统一框架中的一个选择模块，以原 deadline 作对照重新走正确性
准入→匹配 pilot→三重复。无此证据则结束当前选择假设，转固定配置的独立
负载验证；不继续 deadline 搜参，也不恢复已经证明无新增复用的头部 pin。
真正运行时不允许读取 event 9 的未来到达、事后回载标签或 trace ID。
