# CachePilot 总体设计与实施方案

更新：2026-09-23

## 1. 项目目标与边界

CachePilot 研究多轮 Agent 推理中的 KV Cache 跨 GPU/CPU 管理策略。它接入 vLLM 与 LMCache，负责在已有 KV 数据通路上做卸载决策；它不是新的推理服务，也不重新实现 vLLM scheduler 或 LMCache 的传输生命周期。

第一版要回答一个可验证的问题：

> 在相同的多轮 Agent 请求轨迹和资源预算下，基于显存压力、请求优先级、预计复用时间与传输反馈的策略，是否比 LMCache 默认卸载策略更好地平衡重算、KV 搬运和交互延迟？

项目必须先证明默认策略存在可重复的瓶颈，再实现 CachePilot。若基线已经足够好，调整研究问题比强行加入一个策略更合理。

第一版范围：

- 单机单 GPU；
- Qwen3-4B、BF16、8K/16K 上下文；
- vLLM + 外部 LMCache Connector；
- CPU 内存作为第二层 KV 存储；
- 一个主要机制：自适应卸载窗口与搬运预算；
- 离线固定 trace 回放与真实结构 Agent trace 回放；
- 默认策略、调优固定策略、CachePilot 三者对照。

暂不纳入：跨节点 KV 路由、PD 分离、KV 量化、远端对象存储、多 GPU 调度、复杂预测模型、完整 Agent 产品和 Kubernetes 部署。

## 2. 系统总体框架

### 2.1 组件关系

```text
固定 Trace / Agent 回放客户端
              |
              | OpenAI-compatible HTTP requests
              v
        vLLM API Server
              |
              v
        vLLM Scheduler
              |
              | external connector API
              v
       LMCacheMPConnector
              |
              v
       LazyOffloadManager
          /           \
         /             \
  CachePilotPolicy     LMCache transfer/lifecycle
  (decision only)      (pin, hash, async store,
                         retrieve, callback, reset)
                              |
                 GPU KV <----+----> CPU KV / LMCache MP server
```

职责边界如下：

| 组件 | 负责内容 |
|---|---|
| 回放客户端 | 按固定 trace 发送请求，记录请求级时间戳和响应 |
| vLLM scheduler | 请求排队、token 调度、GPU block 使用 |
| LMCache connector/manager | KV block 生命周期、哈希、pin/unpin、异步 store/retrieve、失败回执 |
| CachePilot policy | 选择候选 KV、决定提交时机、窗口和每步搬运预算 |
| LMCache MP server | CPU/其他存储层的 KV 保存和传输 |
| 指标采集模块 | 合并 vLLM、LMCache、客户端和 GPU 观测，生成实验结果 |

CachePilot 不直接操作底层 GPU block，也不绕过 manager 的引用和完成语义。策略输出应是 manager 能执行的候选集合或预算参数。

### 2.2 接入方式

基线通过 vLLM 的外部 Connector 配置接入：

```json
{
  "kv_connector": "LMCacheMPConnector",
  "kv_connector_module_path": "lmcache.integration.vllm.lmcache_mp_connector",
  "kv_role": "kv_both"
}
```

默认策略与 CachePilot 策略必须能够通过配置切换。具体配置字段、vLLM hook 名称和 LMCache commit 在 GPU 环境验证后冻结，不能提前把尚未实现的 `CACHEPILOT` 当作可用启动参数。

## 3. 预计代码结构

代码目录在环境和接口确认后建立，建议保持以下边界：

```text
CachePilot/
  cachepilot/
    policy/
      base.py              # 策略输入输出协议，不拥有 KV 生命周期
      fixed.py              # 固定 horizon/预算基线
      adaptive.py           # CachePilot 自适应策略
      factory.py            # 显式创建策略
    signals/
      pressure.py           # GPU/CPU 容量和压力信号
      transfer.py           # 在途字节、完成时间、传输带宽反馈
      reuse.py              # 会话年龄、预计复用间隔等信号
    integration/
      lmcache_adapter.py    # 对 LMCache manager 的最小适配层
    telemetry/
      events.py             # 统一事件模型
      collector.py          # 日志、指标和 trace 合并
    replay/
      schema.py             # trace 数据结构与校验
      runner.py             # 开放环/封闭环回放
    analysis/
      metrics.py            # TTFT、P95、重算量、搬运量等
      report.py             # 表格和图表输出
  configs/
  scripts/
  tests/
  docs/
```

第一版实现顺序：先写 `replay/schema.py` 和指标采集，再写固定策略适配，最后才写 `adaptive.py`。这样可以先得到可复现的基线，避免策略代码和实验脚本同时变化。

## 4. 分阶段实施计划

### 阶段 0：设计冻结与数据准备

目标是把研究变量变成可回放的输入。

- 确定模型、上下文、输出长度、请求格式和资源预算。
- 设计统一 trace schema：session、turn、messages、tool result、到达时间、预期下一轮间隔、优先级。
- 生成可控合成 workload，并清洗少量公开 Agent/工具调用轨迹。
- 写 trace 校验器和脱离 GPU 的回放时间模拟器。
- 固定随机种子、版本记录方式和结果目录结构。

验收：同一 trace 在本地模拟器中重复运行，事件顺序和统计结果一致。

### 阶段 1：运行环境与最小链路

目标是确认 vLLM、LMCache 和模型能在同一台机器上正确协作。

- RTX 4090 24GB、Ubuntu 22.04、CUDA 12.8 起步。
- 锁定实际 vLLM、PyTorch、LMCache、CUDA runtime、模型 revision。
- 单独验证 vLLM 生成。
- 验证外部 `LMCacheMPConnector` 的实际模块路径。
- 验证冷请求、GPU 热命中、CPU 保存、GPU miss 后回载和输出一致性。
- 保存启动参数、日志、GPU KV 容量和环境记录。

验收：完整 smoke 链路通过；仅 import 成功不算通过。

### 阶段 2：基线与 profiling

目标是确认问题是否值得做，以及瓶颈属于容量、搬运还是请求调度。

固定同一模型、硬件、GPU KV 预算和 trace，依次测试：

1. vLLM 原生 Prefix Cache；
2. vLLM + LMCache 默认策略；
3. LMCache 延迟卸载的固定 horizon/预算扫描。

覆盖三种工作集：小于 GPU KV 容量、超过 GPU 容量、超过 GPU+CPU 总容量；同时覆盖短间隔恢复、长间隔恢复、长 prefill 突发和稳定 decode。

验收：得到原始事件、请求级指标和瓶颈结论。若默认策略在目标 workload 上没有明显问题，暂停策略开发并重新选择研究切入点。

### 阶段 3：CachePilot 策略实现

目标是只实现一个可以解释的机制。

候选第一版机制：

- 根据 GPU 压力和在途字节动态调整卸载 horizon；
- 对每个调度步设置搬运预算，避免卸载占满传输资源；
- 对交互请求和后台 Agent 使用不同优先级；
- 用近期复用间隔和会话状态作为准入信号。

实现约束：

- 保留默认策略作为可切换对照；
- 不改 manager 的 pin/unpin、哈希、失败、reset、ID 重用语义；
- 明确区分 scheduler 回执时间、GPU DMA 时间和客户端观察时间；
- 所有策略决策记录原因、输入信号和输出预算。

验收：单元测试覆盖取消、抢占、部分保存、失败和资源回收；策略能被开关控制且不改变默认路径。

### 阶段 4：消融、复核与展示

- 与默认策略、调优固定策略比较；
- 消融压力信号、复用信号、传输预算和优先级；
- 开放环和封闭环分别报告；
- 每个配置至少重复 3 次，报告平均值与 P50/P95/P99；
- 在 Qwen3-8B + 5090 32GB 上做一次扩展复核；
- 保留退化 workload 和失败案例，不只展示最佳结果；
- 整理复现命令、环境 digest、原始数据、图表和个人贡献。

验收：第三方可以按文档复现至少一组基线和一组 CachePilot 对照。

## 5. 实验模型与数据

### 5.1 模型与硬件矩阵

| 阶段 | GPU | 模型 | 目的 |
|---|---|---|---|
| 主实验 | RTX 4090 24GB | Qwen3-4B，BF16 | 低成本跑通链路、参数扫描和策略迭代 |
| 扩展复核 | RTX 5090 32GB 或 4090 48GB | Qwen3-8B，BF16 | 检查模型规模和显存压力变化后的趋势 |
| 暂不要求 | A800 | 14B 及以上 | 只有在研究问题需要更大容量或带宽时再加入 |

上下文从 8K 起步，之后扩展到 16K。输出长度、并发和 GPU KV 预算必须在每个实验组内固定。

### 5.2 数据与 workload

推理实验的对象不是训练数据集，而是带时间语义的请求 trace。

第一优先级是合成固定 Agent trace：

- 多轮对话与工具调用；
- 可控制复用间隔、上下文长度、并发、优先级和到达时间；
- 覆盖短间隔恢复、长间隔恢复、一次性长上下文和突发并发。

第二优先级是公开 Agent/工具调用数据的离线结构回放，例如 τ-bench、ToolBench/ToolTalk 类轨迹。外部工具结果要预先固定，不能在性能实验期间访问网络。

第三优先级是 LongBench 等长上下文样本，用来制造 prefill 和 KV 容量压力，不把它们宣称为完整 Agent workload。

建议首批规模：50–200 个 session，每个 session 5–20 轮；开发集和验证集按 session 划分，避免同一会话同时出现在调参和最终报告中。

### 5.3 回放模式

- **封闭环**：上一轮完成后再发送下一轮，用于隔离 KV 命中、重算和搬运效果。
- **开放环**：按固定 arrival time 发送，用于评估排队、吞吐和尾延迟。

这里的“回放”是重复发送预先记录的请求事件，不是要求模型重新演出完全一样的 Agent 行为。Trace 保存每轮的完整输入消息、固定工具返回、请求到达时间和生成参数。下一轮输入使用 trace 中记录的 assistant/tool 消息，而不是接上本次测试刚生成的 assistant 输出。因此不同策略即使生成文本略有差异，也不会改变后续请求的输入前缀。

这能保证 A/B 运行拿到相同的请求内容和计划到达时间，但**不能单靠回放保证生成 token 完全一致**。temperature 设为 0、固定 seed、模型和 tokenizer revision，有助于减少差异；不同 batch 组合、kernel 路径或运行时仍可能带来非确定性。`max_tokens` 是上限，模型也可能提前输出 EOS，所以它本身不保证每条请求生成相同数量的 token。

实验分两种模式并明确标注：

- **缓存策略隔离实验**：尽量固定每个请求的 decode token 数。先验证选定 vLLM 版本是否支持可靠的固定长度生成控制；若支持，固定生成长度并校验实际输出 token 数。若不支持，就记录实际生成 token 数，并把 prefill/cache 指标与 decode 负载分开分析。
- **端到端 Agent 负载实验**：按自然停止条件生成，固定回放输入和工具结果，但允许输出长度变化；多次重复并报告实际生成 token 数及其分布。它评估真实服务负载，不能把输出差异误判为缓存策略收益。

两类结果都固定请求内容、工具结果和开放环到达计划。封闭环按上一轮完成后再推进，因而轮间等待时间会随策略变化；应记录这个差异，并同时报告请求级延迟与会话总耗时。开放环严格按 trace 到达计划发送，延迟增加会体现为排队，而不会推迟 trace 后续请求。

Trace 回放评估的是推理系统在同一请求负载下的行为，不评估 Agent 是否做出了正确决策。若要评估真实 Agent 闭环质量，应另做一组端到端实验，让模型输出决定工具调用和后续输入，并把任务成功率与服务性能分开报告。

## 6. 指标与对照设计

主要指标：TTFT、每 token 间隔、端到端延迟、吞吐、P50/P95/P99、GPU 显存峰值、CPU KV 占用、KV 命中率、重算 token 数、GPU/CPU 搬运字节数和策略决策耗时。

每个结果必须附带：模型 revision、软件版本、GPU、GPU KV 预算、CPU KV 预算、trace revision、并发、输出长度和重复次数。

不能只比较一次请求，也不能把论文中的加速比、模拟命中率或单独的 GPU kernel 时间直接当作项目收益。最终结论应说明收益出现在哪类 workload，同时报告没有收益或退化的配置。

## 7. 需要补充和提前确认的事项

开工前还需要确认以下信息：

- 租机实例的实际显存、主存、CPU、磁盘和 GPU PCIe 链路；
- 选定镜像中 vLLM 版本是否支持外部 module path；
- LMCache 研究 commit 与该 vLLM 版本的实际 hook 兼容性；
- `lmcache server` 的可用启动参数和 IPC/共享内存限制；
- vLLM 实际分配的 GPU KV 容量；
- 公开 Agent trace 的许可证和可再发布范围；
- 每个实验的停止条件、小时预算和日志保留位置。

这些事项没有确认前，不冻结 requirements lock，也不对性能提升写预期数字。

## 8. 项目成功标准

项目达到可展示状态需要同时满足：

1. 基线和 CachePilot 使用同一套固定 trace 可重复运行；
2. 外部 LMCache Connector 的实际加载路径有日志证据；
3. CachePilot 不破坏 KV 生命周期和输出正确性；
4. 至少一个 workload 上能解释性地改善目标指标，或明确证明某个假设不成立；
5. 提供基线、消融、退化案例和完整环境记录；
6. 代码结构能说明 vLLM、LMCache 和自研策略的职责边界。
