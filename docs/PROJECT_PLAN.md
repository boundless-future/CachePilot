# CachePilot 总体设计与实施方案

更新：2026-10-01

> 本文同时记录原始计划和开发后的修订路线。原始计划假设“自适应卸载窗口”会成为第一版主要机制；实际实验发现更关键的问题是异步 KV 回载的物理 block 分配与 LMCache lazy-offload 压力观测存在时间错位，因此当前主线已经调整为“先建立可验证的压力信号，再决定是否实施准入前保护策略”。

## 1. 项目目标与边界

CachePilot 研究多轮 Agent 推理中的 KV Cache 跨 GPU/CPU 管理策略。它接入 vLLM 与 LMCache，负责在已有 KV 数据通路上做卸载决策；它不是新的推理服务，也不重新实现 vLLM scheduler 或 LMCache 的传输生命周期。

原始计划要回答一个可验证的问题：

> 在相同的多轮 Agent 请求轨迹和资源预算下，基于显存压力、请求优先级、预计复用时间与传输反馈的策略，是否比 LMCache 默认卸载策略更好地平衡重算、KV 搬运和交互延迟？

项目必须先证明默认策略存在可重复的瓶颈，再实现 CachePilot。若基线已经足够好，调整研究问题比强行加入一个策略更合理。

当前版本需要回答的更具体问题是：

> 在异步 KV 回载会占用 GPU block 的多轮 Agent 负载中，能否在准入前得到足够可靠的需求信号，并在不破坏 LMCache 生命周期的情况下，提前保护并卸载合适的 KV，从而减少重算和尾延迟？

当前版本范围：

- 单机单 GPU；
- Qwen3-4B、BF16、8K/16K 上下文；
- vLLM + 外部 LMCache Connector；
- CPU 内存作为第二层 KV 存储；
- 研究分三层：压力信号观测、实际分配计数消融、准入前保护策略原型；
- 自适应卸载窗口保留为已完成的探索性对照，不预先认定为最终机制；
- 离线固定 trace 回放与真实结构 Agent trace 回放；
- 默认策略、调优固定策略、实际分配信号消融、最终保护策略和立即卸载对照。

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

## 3. 当前代码结构与目标结构

原始计划建议建立独立 `cachepilot/` 包。实际开发先采用外部 Connector、脚本和测试快速验证上游边界，当前目录如下：

```text
CachePilot/
  scripts/
    adaptive_connector.py          # 已完成：horizon 探索原型
    allocation_connector.py        # 已完成：实际分配信号消融
    preallocation_observer.py      # 已完成：准入前需求观测，不改变调度
    decision_connector.py           # 已完成：策略决策账本
    horizon_policy.py               # 已完成：自适应窗口规则
  configs/                          # 实验配置和 trace
  tests/                            # 回放、分配审计、生命周期契约
  docs/experiments/                  # 原始证据和阶段报告
```

最终策略接入后再逐步收敛为以下边界，不在证据不足时提前抽象：

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

实际实现顺序已经变为：先完成回放和最小链路，再做基线 profiling；随后用外部 Connector 快速验证 adaptive、allocation 和 preallocation 观测；在生命周期门槛通过后，才实现正式策略工厂和最终保护状态机。这样可以把上游接口问题与策略收益分开，避免把诊断脚本误当成正式策略。

## 4. 分阶段实施计划（按实际开发修订）

### 阶段 0：设计冻结与数据准备（已完成）

完成了 trace schema、合成多轮/高重叠压力轨迹、真实结构缩比会话、开放环/封闭环回放入口、版本记录和证据目录。实际回放固定的是请求输入、工具结果和到达计划，不要求 decode token 逐条相同；输出差异单独作为正确性限制记录。

阶段结论：数据和回放可以支撑 A/B，但自然 Agent 闭环质量不与缓存策略性能混合验收。

### 阶段 1：运行环境与最小链路（已完成，保留限制）

实际环境不是原计划的 CUDA 12.8，而是 RTX 4090 24GB、CUDA 13.0、PyTorch 2.13.0+cu130、vLLM 0.30.0、Qwen3-4B 和锁定的 LMCache 研究提交。已完成 vLLM、外部 Connector、MP server、GPU hit、CPU 保存、GPU miss 后回载、EVICTION_AWARE 分支和 TTL 检查。

保留限制：输出在部分串行多轮序列中出现差异；KV 探针已确认观测到的回载内容 bitwise 一致，但编译/CUDA Graph 与全部取消、抢占路径尚未完全拆分验证。因此“链路可运行”不等于“所有正确性路径通过”。

### 阶段 2：基线与 profiling（已完成初轮，仍需补齐）

已经完成原生 vLLM、立即卸载、LMCache 默认 EVICTION_AWARE、固定 horizon 和自适应 horizon 的初轮对照，并覆盖小工作集和超过 GPU KV 的压力工作集。已保存 TTFT、排队/precompute、命中、D2H/H2D、GPU KV 和策略账本等指标。

仍需完成：固定 horizon/提交上限扫描、更多 GPU KV 容量、长 prefill 突发、稳定 decode、集中恢复、DMA/排队/重算/回载分解，以及编译/CUDA Graph 对输出差异的独立归因。初轮结果只证明存在可研究的压力现象，不证明自适应 horizon 有收益。

### 阶段 3A：策略信号诊断支线（已完成，作为最终策略前置证据）

这条支线是原计划没有预见的，原因是实验发现 LMCache lazy-offload 看到的压力信号与 vLLM 实际物理分配可能错位。按以下顺序完成：

1. 实现 adaptive horizon 原型，确认只改窗口时收益不足；
2. 记录 allocator 真值并实现 allocation signal 消融，确认计数闭合但收益有限；
3. 设计并运行准入前需求观测，比较 `compute_slots`、宽泛异步上界、`lookup_ready` 和 `lookup_inflight`；
4. 在第二类批量到达和高重叠轨迹上重复，并完成逐请求 lookup→allocation 配对和 STORE 回执审计。

阶段结论：`compute_slots` 不是异步回载物理分配上界；lookup 状态有一定区分度但误报仍多，当前不足以直接驱动提前 pin 或准入阻塞。因此保留诊断和 allocation 作为消融，不把它们伪装成最终策略。

### 阶段 3B：生命周期与资源安全门槛（进行中；候选受控 L1/L2 通过，完整资源门槛未通过）

这条支线由策略设计中的风险暴露出来，必须先于保护策略。已完成真实 LMCache registry/policy 的 fake worker 契约测试，覆盖 reset、迟到 receipt、保存失败、request-id 重用、单请求单在途 STORE 和 pending suffix 清理；已完成一次客户端断流 smoke。等待期取消已由诊断 Connector 验证 block 与完成通知闭合，见 [等待期取消](experiments/2026-09-27/ASYNC_RETRIEVE_CANCELLATION.md)。显式抢占在关闭异步调度的对照下通过；默认异步调度的 reset API 返回 500，但请求恢复和资源清理通过，见 [显式抢占](experiments/2026-09-27/EXPLICIT_PREEMPTION.md)。成功回载后模拟 worker 失败结果的诊断确认错误 block、scheduler 重算、输出和资源清理闭合，见 [异步回载失败](experiments/2026-09-27/ASYNC_RETRIEVE_FAILURE.md)。成功 STORE 后模拟失败回执的诊断确认 worker/scheduler 回执、pin/unpin 和资源闭合，见 [STORE 失败回执](experiments/2026-09-27/STORE_FAILURE.md)。进一步以 block ID 不足触发 MP server 拒绝 STORE，验证了原始 `false` future、失败回执和重启后的零外部命中，见 [server 拒绝 STORE](experiments/2026-09-27/SERVER_REJECTED_STORE.md)。协议拒绝仍是注入故障，不覆盖自然传输/写入失败、部分写入、所有生产路径或抢占期间的在途 STORE。

随后两次让 MP server 拒绝 RETRIEVE，均得到原始 `false` future：272 个 GPU block 被标错并重算，目标生成和 GPU block 回收通过；但第二次的 CPU 读锁从注入前 0 增至 17，后续请求和 vLLM 退出后仍未释放，故端到端资源验收为 `passed=false`，见 [server 拒绝 RETRIEVE](experiments/2026-09-27/SERVER_REJECTED_RETRIEVE.md)。诊断 Connector 故意提交空 block ID 列表以触发 underflow；没有证据表明正常 Connector 会提交这种输入。这是上游候选缺口，不直接否定保护策略设计，也不能算作自然 I/O 故障验证。

进一步使用未修改的 `LMCacheMPConnector` 三次复现实验：在 MP server 受控暂停造成的 lookup 等待期断开客户端，vLLM 的 deferred 请求清零、正常 follow-up 命中并生成相同输出，但三次均留下 17 个 CPU 读锁；r3 另观察到 1 个未移除的 prefetch job，vLLM 退出后仍在。资源验收均为 `passed=false`，见 [原生 lookup 取消](experiments/2026-09-27/REMOTE_LOOKUP_CANCELLATION.md)。客户端与 server 逐请求事件确认 LOOKUP 注册 job 后 END_SESSION 没有消费它。固定版真实 `LookupModule` 的可选回收候选在原生 Connector 的两轮受控 L1 取消复测中逐对象释放 17 个锁，job 和 controller result 归零，follow-up 正常，见 [候选复测](experiments/2026-09-27/LOOKUP_RECLAIM_CANDIDATE.md)。这是加 wrapper 的 server，不是未修改 server 或已验证的上游补丁；实验也不是自然网络故障。

2026-09-30 补充了三条证据：客户端恢复 ack/status→END 等待顺序的组合实验通过，但同步等待不是生产策略；真实 FS 短读候选释放前 8 个对象，关闭候选对照残留 8 锁，见 [顺序与短读](experiments/2026-09-30/ORDERED_END_AND_SHORT_READ.md)；无 END_SESSION 的进程死亡实验在 630 秒后 session 已清除而 job/result、17 个 temporary 对象与旧 GPU 注册仍在，见 [客户端死亡](experiments/2026-09-30/CLIENT_DEATH_NO_END.md)。原生锁特征测试还确认旧 key/count unlock 可能误减 TTL 后新读者的锁，见 [所有权边界](experiments/2026-09-30/TTL_OWNERSHIP_BOUNDARY.md)。因此不能将简单 TTL 强制释放当完整补丁。

注册宽限期 120 秒的对照确认 worker reaper 能清除旧 GPU 注册，但不清除 lookup job；默认 grace=3,600 秒，不能把630秒观察中的注册直接称泄漏。关闭 load 屏障实验确认 controller.stop 可释放已预留的17对象/612MiB，但候选 bookkeeping 与不可取消 I/O 仍未闭合，见 [关闭边界](experiments/2026-09-30/SHUTDOWN_BOUNDARY.md)。真实 EFBIG 部分写入已验证 L2 清理和重启恢复，见 [L2 写入失败](experiments/2026-09-30/L2_WRITE_FAILURE.md)；它发生在 GPU→L1 成功后，不代替 worker STORE 失败路径。

23:00汇报后经用户继续授权，补充逐对象释放检查：可选候选直接读取固定版真实L1Manager结果，保留成功集、失败集和通知异常，不再把“调用返回”当所有对象释放成功。部分成功或不确定异常保留job且不自动重试，避免重复减少新reader引用；测试和实机证据见 [逐对象释放](experiments/2026-09-30/CHECKED_RELEASE.md)。这是所有权改造的前置步骤，仍使用匿名key/count，不关闭无END死亡或TTL后旧锁释放的缺口，阶段3B保持进行中。

随后实现独立C++ reservation锁和真实L1Manager方法的CPU契约：共享TTL epoch、不可复用reader token、重复释放和同名对象重建隔离通过；新原生锁11测试、L1契约12测试，完整服务器回归206通过。见 [reservation接口原型](experiments/2026-09-30/RESERVATION_EPOCH.md)。这是3B所有权支线，未替换运行栈；下一步必须在L1命中和L2写转读的获取点保存token，并贯穿裁剪/完成结果/QUERY或回收的唯一移交，不能只改finish_read或给bitmap事后补身份。

2026-10-01进一步将token贯穿真实PrefetchController方法的CPU契约，包括获取、L2写转读、裁剪、完成发布与破坏性消费。新增19测试和50轮QUERY/abandon竞争通过；完整服务器回归225通过，见 [controller唯一移交](experiments/2026-10-01/OWNED_PREFETCH.md)。adapter I/O和load plan仍是测试输入，后台loop/实际服务未接入。下一步覆盖StorageManager的初始L1前缀和L2索引合并、纯L1一次消费，再接LookupModule/RETRIEVE；这仍属于3B支线，不是3C策略或性能收益。

同日继续完成StorageManager的初始L1获取/裁剪、L2局部→原始索引合并及纯L1结果的一次移交CPU契约。新增29测试、11子测试及50轮QUERY/abandon竞争通过；完整服务器回归254通过，见 [StorageManager所有权合并](experiments/2026-10-01/OWNED_STORAGE.md)。合并失败保留初始和下层原token，不自动重试成功释放项；纯L1的-1也有独立job身份。下一步对接LookupModule与RETRIEVE的每worker reader slot及数据访问生命期，再补实际服务失联恢复。此处仍是3B支线，安装栈和GPU策略未改变，3B未通过。

同日接入真实LookupModule的结果接收、全局fold及显式每worker reader slot CPU契约：30测试、9子测试通过，QUERY/END和CLAIM/END各30轮竞争闭合；另修正owned StorageManager对真实IPC编码rank的校验并补1测试，完整服务器285通过。见 [Lookup引用槽](experiments/2026-10-01/OWNED_LOOKUP.md)。END回收offered槽，running槽等模拟terminal ack，错误保留原token和证据。尚未读取真实buffer或执行DMA，不能据此证明内存安全；下一步落实token校验与buffer lease，再对接真实RETRIEVE/wire、失联lease及shutdown。3B仍未通过，3C GPU策略未接入。

尚需使用真实 vLLM/LMCache 服务补齐：

- 原生 Connector 异步 lookup 等待期取消的 server 侧时序及受控 L1/FS L2 回收候选已经验证（L2 两轮通过、一轮关闭候选失败对照，见 [L2 报告](experiments/2026-09-30/L2_PREFETCH_CANCELLATION.md)）；仍需 END_SESSION/LOOKUP 乱序、无 END_SESSION、永久不完成的 controller、关闭时 unresolved job 的修复与期望不变量回归，不能以受控 L1/L2 结果替代整体验收；
- 自然容量抢占与受控迟到STORE回执已完成多轮资源审计及退出TTL观察，见 [自然抢占](experiments/2026-09-30/NATURAL_PREEMPTION.md)；取消持有回执两轮通过且退出TTL归零，见 [取消迟到回执](experiments/2026-09-30/CANCEL_HELD_STORE.md)。自然抢占后实际回载首轮1440层比较全部相等，严格关联1个无中间STORE的orphan源chunk，见 [KV内容](experiments/2026-09-30/PREEMPTION_KV_INTEGRITY.md)。仍需DMA执行期间抢占、更广decode KV与输出正确性验证；
- 异步调度下 reset API 与 deferred block free 的时序限制；
- 真正的远端异步 lookup/传输中止；受控暂停不等同于自然 I/O 故障；
- 未修改 Connector 下的远端回载失败和资源释放；非法 block ID underflow 已发现读锁残留，不能作为这项验收通过的证据；
- 自然 I/O 故障或部分写入下的 worker 保存失败及其 scheduler 回执；成功写入后的模拟失败回执和 server 协议拒绝的原始失败 future 已分别验证。

只有这些路径的 generation、GPU block 释放、CPU 锁/job、pin/unpin、STORE receipt 和最终请求状态可解释，才进入阶段 3C 的 GPU 接入与消融；不接 GPU 的 fake worker 状态机可独立推进。

### 阶段 3C：最终 CachePilot 策略原型（CPU 状态机已开始；GPU 未接入）

首版纯 Python 状态机已完成预算回退、前缀闭合、身份复核和取消/回执所有权测试，见 [PROTECTION_STATE_MACHINE.md](experiments/2026-09-30/PROTECTION_STATE_MACHINE.md)。除简化模型外，已新增 16:1 物理 block/chunk 分组模型和七项测试。它们使用需求与身份oracle；后续已接真实库的CPU对象契约，但尚无运行在真实scheduler里的adapter，不能代替GPU接入或收益验证。

随后增加真实 vLLM BlockPool 的 CPU 元数据桥接八项测试和同 request-id 跨代单在途模式；这未安装进 scheduler，也不包含 CUDA tensor。随后增加真实token ledger的两条hash链证明（6项测试）及实际LMCache STORE metadata的CPU交接契约（7项测试）。接入所需的hash来源、零计算步回滚、回执身份和部分保存边界已写入 [adapter 契约](PROTECTION_ADAPTER_CONTRACT.md)。

这是当前项目的核心策略实现，不等同于已经完成的 adaptive horizon。先做不接 GPU 的 fake worker/state machine；阶段 3B 资源门槛通过后，再接入外部 Connector 做可回滚消融，步骤固定为：

1. 定义准入前需求预算和保护预算，避免把所有 waiting 请求都当成近期风险；
2. 选择候选 KV，执行 hash revalidation 和 prefix closure；
3. 在合法计算继续前 pin 候选，保护预算不足时回退默认策略，不能让 scheduler 饥饿；
4. 将 STORE action 交给 worker，等待 receipt 后再 unpin；
5. 处理取消、抢占、部分保存、保存失败、迟到回执和 request-id generation 隔离；
6. 增加正式策略工厂和配置开关，但默认路径保持不变。

阶段 3C 的验收不是“预测命中率高”，而是：生命周期状态可证明闭合、没有重复 STORE/泄漏/死锁，并在受控 GPU 消融中同时报告实际 prefill、TTFT P95、D2H/H2D、排队和保护代价。

### 阶段 4：对照、复核与展示（未开始）

在阶段 3C 通过后，固定相同 trace、版本、资源和日志设置，对比原生 vLLM、立即卸载、默认 EVICTION_AWARE、调优固定策略、allocation signal 消融和最终策略；每个关键配置至少三次重复，保留退化和失败案例。然后使用 Qwen3-8B + RTX 5090 32GB 做扩展复核，单独分析模型规模和显存变化的影响。

### 阶段 5：项目收尾与上游评估（未开始）

整理环境、源码快照、原始 trace、运行命令、结果图表和个人贡献；重新核查 LMCache/vLLM 上游状态。只有在问题仍可复现、存在最小可维护修复并有回归测试时，才提交 issue/PR，不把上游贡献作为当前实验前置条件。

## 5. 开发过程中新增的支线记录

| 支线 | 触发原因 | 已解决/结论 | 对主线的影响 |
|---|---|---|---|
| 环境兼容性 | 计划中的 CUDA/vLLM 组合与租用环境不一致 | 固定为 CUDA 13.0、vLLM 0.30.0、LMCache 研究提交并完成接入 | 后续结果必须绑定实际版本，不能套用原计划兼容表 |
| 输出差异与 KV 正确性 | 多轮回放出现少量输出不同 | KV 回载探针 bitwise 一致；编译/CUDA Graph 影响仍需拆分 | 性能结论不能用文本相同替代正确性结论 |
| 自适应 horizon | 原计划预期它是第一版机制 | 三次重复未证明收益，保留为探索性对照 | 最终策略改为准入前保护研究 |
| 异步分配与压力信号错位 | `dropped_evicted` 与 allocator 分配时序不一致 | 确认是 policy 观测语义错位，不是 vLLM 漏分配；allocation 仅作消融 | 新增信号诊断和上游候选问题记录 |
| 准入前需求观测 | 事后修正信号无法挽救已被覆盖的 KV | `compute_slots` 低估，lookup 信号误报较多，暂不保护 | 增加阶段 3A，阻止过早实现 pin |
| 生命周期与取消 | 保护策略会改变在途 STORE 和资源释放时序 | registry 契约及若干诊断路径通过；非法 RETRIEVE 输入留下 17 个读锁，原生 Connector 受控 lookup 取消也三次残留 17 个读锁、r3 另有 1 个 job；自然 I/O/部分写入等仍待补 | 增加阶段 3B；GPU 保护门槛未通过，fake worker 状态机可独立推进 |
| RETRIEVE underflow 读锁 | 诊断 Connector 故意提交空 block ID 列表，MP server 返回原始 `false` | 两次复现，第二次确认注入前 0、目标后及 vLLM 退出后均为 17；正常 Connector 是否可能触发尚无证据 | 独立保存上游候选问题，项目收尾时核查版本、最小修复和回归测试；不外推至自然故障 |
| 原生 lookup 取消后资源残留 | 受控暂停 MP server 时断开等待 lookup 的客户端 | 三次未修改 server 复现 17 个读锁，r3 有 1 个 job；两轮候选 server 的受控 L1 复测逐对象释放 17 个锁且 job 清零 | 真实 FS L2 受控取消两轮候选通过、一轮对照失败；继续验证协议乱序、无 END_SESSION、自然故障和最小补丁；GPU 保护仍受 3B 门槛约束 |
| 上游修复评估 | 发现可能有可复现的压力信号缺口 | 已单独记录复现、影响和 PR 条件 | 延后到阶段 5，不阻塞项目主线 |

## 6. 实验模型与数据

### 6.1 模型与硬件矩阵

| 阶段 | GPU | 模型 | 目的 |
|---|---|---|---|
| 主实验 | RTX 4090 24GB | Qwen3-4B，BF16 | 低成本跑通链路、参数扫描和策略迭代 |
| 扩展复核 | RTX 5090 32GB 或 4090 48GB | Qwen3-8B，BF16 | 检查模型规模和显存压力变化后的趋势 |
| 暂不要求 | A800 | 14B 及以上 | 只有在研究问题需要更大容量或带宽时再加入 |

上下文从 8K 起步，之后扩展到 16K。输出长度、并发和 GPU KV 预算必须在每个实验组内固定。

### 6.2 数据与 workload

推理实验的对象不是训练数据集，而是带时间语义的请求 trace。

第一优先级是合成固定 Agent trace：

- 多轮对话与工具调用；
- 可控制复用间隔、上下文长度、并发、优先级和到达时间；
- 覆盖短间隔恢复、长间隔恢复、一次性长上下文和突发并发。

第二优先级是公开 Agent/工具调用数据的离线结构回放，例如 τ-bench、ToolBench/ToolTalk 类轨迹。外部工具结果要预先固定，不能在性能实验期间访问网络。

第三优先级是 LongBench 等长上下文样本，用来制造 prefill 和 KV 容量压力，不把它们宣称为完整 Agent workload。

建议首批规模：50–200 个 session，每个 session 5–20 轮；开发集和验证集按 session 划分，避免同一会话同时出现在调参和最终报告中。

### 6.3 回放模式

- **封闭环**：上一轮完成后再发送下一轮，用于隔离 KV 命中、重算和搬运效果。
- **开放环**：按固定 arrival time 发送，用于评估排队、吞吐和尾延迟。

这里的“回放”是重复发送预先记录的请求事件，不是要求模型重新演出完全一样的 Agent 行为。Trace 保存每轮的完整输入消息、固定工具返回、请求到达时间和生成参数。下一轮输入使用 trace 中记录的 assistant/tool 消息，而不是接上本次测试刚生成的 assistant 输出。因此不同策略即使生成文本略有差异，也不会改变后续请求的输入前缀。

这能保证 A/B 运行拿到相同的请求内容和计划到达时间，但**不能单靠回放保证生成 token 完全一致**。temperature 设为 0、固定 seed、模型和 tokenizer revision，有助于减少差异；不同 batch 组合、kernel 路径或运行时仍可能带来非确定性。`max_tokens` 是上限，模型也可能提前输出 EOS，所以它本身不保证每条请求生成相同数量的 token。

实验分两种模式并明确标注：

- **缓存策略隔离实验**：尽量固定每个请求的 decode token 数。先验证选定 vLLM 版本是否支持可靠的固定长度生成控制；若支持，固定生成长度并校验实际输出 token 数。若不支持，就记录实际生成 token 数，并把 prefill/cache 指标与 decode 负载分开分析。
- **端到端 Agent 负载实验**：按自然停止条件生成，固定回放输入和工具结果，但允许输出长度变化；多次重复并报告实际生成 token 数及其分布。它评估真实服务负载，不能把输出差异误判为缓存策略收益。

两类结果都固定请求内容、工具结果和开放环到达计划。封闭环按上一轮完成后再推进，因而轮间等待时间会随策略变化；应记录这个差异，并同时报告请求级延迟与会话总耗时。开放环严格按 trace 到达计划发送，延迟增加会体现为排队，而不会推迟 trace 后续请求。

Trace 回放评估的是推理系统在同一请求负载下的行为，不评估 Agent 是否做出了正确决策。若要评估真实 Agent 闭环质量，应另做一组端到端实验，让模型输出决定工具调用和后续输入，并把任务成功率与服务性能分开报告。

## 7. 指标与对照设计

主要指标：TTFT、每 token 间隔、端到端延迟、吞吐、P50/P95/P99、GPU 显存峰值、CPU KV 占用、KV 命中率、重算 token 数、GPU/CPU 搬运字节数和策略决策耗时。

每个结果必须附带：模型 revision、软件版本、GPU、GPU KV 预算、CPU KV 预算、trace revision、并发、输出长度和重复次数。

不能只比较一次请求，也不能把论文中的加速比、模拟命中率或单独的 GPU kernel 时间直接当作项目收益。最终结论应说明收益出现在哪类 workload，同时报告没有收益或退化的配置。

## 8. 当前剩余工作与决策门槛

原计划中的环境确认已经完成并固化在 `docs/ENVIRONMENT.md`。当前剩余事项按阻塞关系排列：

1. 针对原生 Connector 的受控 lookup 取消资源失败，客户端/server 时序、上游范围对照和纯 Python 模型已完成；固定版真实 `LookupModule` 的可选回收候选在两轮原生 Connector + 候选 server 的受控 L1 复测中使 17 个对象锁和 job 归零，见 [LOOKUP_RECLAIM_CANDIDATE.md](experiments/2026-09-27/LOOKUP_RECLAIM_CANDIDATE.md)。真实 FS L2 在途 prefetch 已完成两轮候选通过及一轮关闭候选失败对照，见 [L2 报告](experiments/2026-09-30/L2_PREFETCH_CANCELLATION.md)。当前已补顺序等待/短读、无END死亡对照、可取消controller关闭、L2真实EFBIG、自然抢占/迟到回执和session TTL；仍以无END死亡的job/result所有权为主要缺口，详见 [协议约束](LOOKUP_OWNERSHIP_PROTOCOL.md)。下一步实现可维护的最小所有权补丁，另补真实传输中止、worker层写入失败、DMA执行期抢占与内容正确性。候选受控 L1/L2 通过不等于阶段 3B 通过；RETRIEVE underflow 的读锁残留保留为独立候选问题，异步 reset API 与 session TTL 单列复核；
2. 继续补齐 P2 的长 prefill、稳定 decode、容量扫描和 DMA/排队/重算分解；
3. 与资源排查并行，阶段3C已完成fake worker状态机、真实BlockPool/hash及STORE metadata的CPU契约；继续核对真实scheduler/worker交接边界；只有生命周期门槛通过后才进行小规模 GPU 消融；
4. 固定最终对照矩阵和验证 trace，重复运行并保留退化案例；
5. 使用 Qwen3-8B + RTX 5090 32GB 做扩展复核；
6. 独立拆分编译/CUDA Graph 对输出差异的影响，更新正确性边界；
7. 项目收尾时重新检查上游状态，再决定是否提交 issue/PR。

以下约束在后续每轮实验中继续有效：固定模型和 tokenizer revision、GPU KV/CPU KV 预算、trace 和到达计划；记录真实 allocator 分配与策略信号；诊断日志运行不得直接作为性能对照；不把 HTTP 成功、计数闭合或预测命中率单独当作策略收益。

## 9. 项目成功标准

项目达到可展示状态需要同时满足：

1. 基线和最终策略使用同一套固定 trace 可重复运行；
2. 外部 LMCache Connector 的实际加载路径、版本和启动参数有日志证据；
3. 最终策略的取消、抢占、部分保存、失败、迟到回执和资源回收语义有测试证据；
4. CachePilot 不绕过 LMCache 的 hash、prefix closure、pin/unpin 和 generation 生命周期；
5. 至少一个 workload 上能解释性地改善目标指标，或明确证明保护策略不值得启用；
6. 提供默认、固定调优、allocation 消融、最终策略、立即卸载和退化案例；
7. 代码结构能说明 vLLM、LMCache 和自研策略的职责边界，并可由第三方复现至少一组对照。
