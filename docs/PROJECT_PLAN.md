# CachePilot 总体设计与实施方案

更新：2026-10-12

> 本文同时记录原始计划和开发后的修订路线。原始计划假设“自适应卸载窗口”会成为第一版主要机制；实际实验发现更关键的问题是异步 KV 回载的物理 block 分配与 LMCache lazy-offload 压力观测存在时间错位，因此当前主线已经调整为“先建立可验证的压力信号，再决定是否实施准入前保护策略”。

## 1. 项目目标与边界

CachePilot 研究多轮 Agent 推理中的 KV Cache 跨 GPU/CPU 管理策略。它接入 vLLM 与 LMCache，负责在已有 KV 数据通路上做卸载决策。策略本身不拥有传输生命周期；开发中另增加 opt-in 所有权适配器，作为阶段 3B 的实验前置支线，复用真实 LMCache allocator/controller/native copy，并约束获取、终结和关闭。该适配器只绑定固定版本，尚不是上游最小生产补丁。

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

策略输出是候选集合或预算参数，不直接调用底层 GPU kernel。3C 的 integration adapter 必须在 scheduler owner 串行边界执行候选复核、合法 BlockPool pin 和 metadata 交接；已经交给 worker 的 pin 必须等原批次回执才能解除。不能把 CPU harness 直接装进 allocator callback，也不能绕过 manager 的引用和完成语义。所有权适配器与策略独立开关，对照双方使用相同数据通路。

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

保留限制：输出在部分串行多轮序列中出现差异；KV 探针已确认观测到的回载内容 bitwise 一致。编译与 CUDA Graph 已独立消融：前者在本 trace 出现 1/16 差异，后者 0/16；仍未定位唯一根因，也未覆盖全部取消、抢占及异步 decode 路径。见 [正确性复核](experiments/2026-10-01/NATIVE_TRANSFER_AND_DECODE.md)。“链路可运行”不等于“所有正确性路径通过”。

### 阶段 2：基线与 profiling（已完成初轮，仍需补齐）

已经完成原生 vLLM、立即卸载、LMCache 默认 EVICTION_AWARE、固定 horizon 和自适应 horizon 的初轮对照，并覆盖小工作集和超过 GPU KV 的压力工作集。已保存 TTFT、排队/precompute、命中、D2H/H2D、GPU KV 和策略账本等指标。

仍需完成：固定 horizon/提交上限扫描、更多 GPU KV 容量、长 prefill 突发、稳定 decode、集中恢复、DMA/排队/重算/回载分解，以及编译相关输出差异的根因定位。编译/CUDA Graph 的独立消融和非异步串行 decode KV 逐层复核已完成，但不代替这些性能负载与异步正确性验证。初轮结果只证明存在可研究的压力现象，不证明自适应 horizon 有收益。

### 阶段 3A：策略信号诊断支线（已完成，作为最终策略前置证据）

这条支线是原计划没有预见的，原因是实验发现 LMCache lazy-offload 看到的压力信号与 vLLM 实际物理分配可能错位。按以下顺序完成：

1. 实现 adaptive horizon 原型，确认只改窗口时收益不足；
2. 记录 allocator 真值并实现 allocation signal 消融，确认计数闭合但收益有限；
3. 设计并运行准入前需求观测，比较 `compute_slots`、宽泛异步上界、`lookup_ready` 和 `lookup_inflight`；
4. 在第二类批量到达和高重叠轨迹上重复，并完成逐请求 lookup→allocation 配对和 STORE 回执审计。

阶段结论：`compute_slots` 不是异步回载物理分配上界；lookup 状态有一定区分度但误报仍多，当前不足以直接驱动提前 pin 或准入阻塞。因此保留诊断和 allocation 作为消融，不把它们伪装成最终策略。

### 阶段 3B：生命周期与资源安全门槛（受控 3C 准入通过；生产完整门槛仍开放）

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

同日完成独立C++ token校验/pin与真实L1方法外围的活动buffer lease CPU契约：新增7项native和19项L1测试、9子测试，完整服务器311通过。见 [buffer lease](experiments/2026-10-01/BUFFER_LEASE.md)。TTL/reset使旧reservation失效，但活动pin保留至显式消费终结；可复用CPU字节buffer验证普通delete/clear/write/eviction不能释放在用内存。异常和部分释放保留证据，不重试成功项；force/free和close拒绝。当时Lookup running槽与lease尚未连接；writer、失联恢复和shutdown仍待完成。仍是3B支线，未修改安装栈或执行GPU实验。

随后连接Lookup reader槽与L1 buffer lease，完成claim→读取→消费者明确停止→unpin→原reservation处置的CPU契约。新增23测试、8子测试，READ/END与READ/失败terminal各30轮竞争，完整服务器334通过，见 [Lookup数据访问租约](experiments/2026-10-01/LEASED_LOOKUP.md)。END与TTL不提前释放活动buffer；旧temporary对象已清理时检查保留的原core，stale单独记过期。一个worker终结失败不会阻止其他已运行worker独立终结，失败证据仍保留且禁止重试。下一步核查真实RETRIEVE获取/提交/future/DMA终结及wire接入点；当前terminal仍是调用方承诺，未接真实服务或CUDA，3B未通过。

继续完成固定安装版LMCache-driven RETRIEVE的Python只读审计，见 [接入边界](experiments/2026-10-01/RETRIEVE_INTEGRATION_AUDIT.md)。RPC返回、设备event完成与server callback清理必须分开；异常退出context manager的立即释放不能作为在途传输停止证明，现有callback和wire缺少原ticket。下一步先做独立transfer提交/event完成适配契约，再版本化wire与带ticket completion，最后真实pinned CPU→CUDA验证及服务接入；native执行错误、writer、失联与shutdown仍未验证。不修改安装栈，不将审计结论视作新的GPU缺陷复现。

2026-10-01继续完成独立transfer身份/完成适配CPU契约，见 [TRANSFER_COMPLETION.md](experiments/2026-10-01/TRANSFER_COMPLETION.md)。真实future类和受控event分离RPC、设备状态和资源清理；原身份server callback与submit返回都满足后才一次性终结lease，部分失败保留证据。新增22测试、4子测试、30轮竞争，完整服务器356通过。下一步绑定原ticket的range/group/buffer提交计划，再接native completion及版本化wire，然后真实CUDA与Connector服务验证；当前callback仍是CPU fixture承诺，没有有界tombstone/失联恢复或shutdown，3B未通过。

同日完成原ticket绑定的transfer plan CPU契约，见 [TRANSFER_PLAN.md](experiments/2026-10-01/TRANSFER_PLAN.md)。服务端登记布局、Lookup key/chunk快照、chunk对齐命中后缀、object/kernel分组、目标block数量/容量及原buffer identity在提交前校验；部分范围保守持有整个shard。19新增测试、42子测试、30轮竞争，完整服务器375通过。登记历史禁止身份复用；提交后登记变化或END不释放在途buffer。首版只支持零起点Lookup/full attention，不证明GPU block所有权、shape/dtype或实际DMA停止。下一步核查native completion payload/失败终结，再接版本化wire、真实CUDA和Connector；原有writer/失联/shutdown门槛继续保留，3B未通过。

随后在独立实验 endpoint 中连接原 ticket、版本化 ZMQ QUERY/RETRIEVE、LMCache native completion dispatcher、真实 pinned CPU→CUDA kernel 与独立 target arena；增加原 writer 身份和 D2H marker 后发布/失败丢弃，以及显式断联/迟到请求/shutdown 契约。服务器完整回归398通过。另完成编译/Graph 分离输出消融和 eager/compile-only/graph-only 的188个 decode slot逐层 KV 比较，见 [原生传输和正确性复核](experiments/2026-10-01/NATIVE_TRANSFER_AND_DECODE.md)。这是3B的独立原型，不是正式 MP server/Connector/BlockPool 集成；失联自动发现、自然DMA抢占、真实传输中止、永久阻塞controller关闭及正式writer路径仍未闭合，3B未通过。

随后把 request generation、实际 LOOKUP/RETRIEVE 身份与范围预检、同一 CUDA stream 的独立 native marker、BlockPool 快照接到 opt-in 正式 MP server/Connector。真实正常回载 1536 tokens，非法短 block 在 copy 前拒绝并由 vLLM 重算 272 block；两轮结束后 BlockPool 都回到 909 free、无 tracked ref/deferred free，LMCache 读锁/job 和退出后的 GPU 注册归零；显式 CUDA 完整回归405通过，见 [正式服务桥接](experiments/2026-10-01/OWNED_MP_INTEGRATION.md)。这是正式链路的局部验证，尚未用独立原型的原 reservation/lease/ticket 替换匿名锁和 key-only callback，也未证明所有故障和失联路径。阶段3B继续进行，3C GPU策略消融仍受完整资源门槛约束。

上述段落是各历史检查点的结论。最新进展：已将获取点的原 reservation/pin、L2 controller、版本化 transfer terminal、无 END 客户端心跳过期及关闭门槛接入实际 MP 服务。八个受控矩阵场景在 r2/r3 分别通过，缺 STORE/RETRIEVE terminal 的负例保留原 buffer 并拒绝关闭；自然抢占 r8/r9 各 4 次、其中各 2 次落在指定 STORE 原生终结等待窗口内，各 432 层回载 KV bitwise 相同，原 token、job、terminal 和最终 BlockPool 审计通过。最终源码 CUDA 回归 438 passed、115 subtests；证据和失败轮见 [实际服务报告](experiments/2026-10-01/OWNED_SERVICE.md)。

**本轮明确修订准入范围**：此前把全部生产故障路径作为任何 GPU 策略接入的前提；现在允许在 eager、TP=1、单 full-attention group、隔离实例和逐轮资源审计条件下开始 3C。每轮必须验证 KV 内容和资源终态；未知终结、资源残留或关闭被拒绝时停止该轮，完整实例重建，不能强行释放。准入只允许先实施零保护预算交接，然后最多两个完整 chunk 的小预算验证，不能直接进入长期运行或性能结论。

30 秒回执延迟的自然抢占 r6 仍失败：GPU pin 已清零，但两个空 LOOKUP owner 直到服务关闭才回收。固定 manager 的零 token 步不 drain、pending suffix 会阻止 END，与此现象相容，尚未唯一确定根因。r8/r9 的 5 秒延迟通过不消除该失败；长延迟/空闲收尾属于未支持压力边界，3C 每轮须检查 owner/job 而非只看 free block。完整生产门槛继续开放，尚需补齐：

- opt-in 栈的无 END 死亡已由心跳过期回收；乱序 sequence 保守拒绝，缺 terminal 或长时间持有的 controller 拒绝关闭。仍需永久 I/O 的自动恢复、重启边界以外的恢复能力，以及保守拒绝对长期合法乱序请求的影响，不能以受控场景替代生产整体验收；
- 自然容量抢占与受控迟到STORE回执已完成多轮资源审计及退出TTL观察，见 [自然抢占](experiments/2026-09-30/NATURAL_PREEMPTION.md)；取消持有回执两轮通过且退出TTL归零，见 [取消迟到回执](experiments/2026-09-30/CANCEL_HELD_STORE.md)。自然抢占后实际回载首轮1440层比较全部相等，严格关联1个无中间STORE的orphan源chunk，见 [KV内容](experiments/2026-09-30/PREEMPTION_KV_INTEGRITY.md)。仍需DMA执行期间抢占、更广decode KV与输出正确性验证；
- 异步调度下 reset API 与 deferred block free 的时序限制；
- 真正的远端异步 lookup/传输中止；受控暂停不等同于自然 I/O 故障；
- 未修改 Connector 下的远端回载失败和资源释放；非法 block ID underflow 已发现读锁残留，不能作为这项验收通过的证据；
- 自然 I/O 故障或部分写入下的 worker 保存失败及其 scheduler 回执；成功写入后的模拟失败回执和 server 协议拒绝的原始失败 future 已分别验证。

上述未覆盖项仍是扩大运行范围与生产恢复的前置条件。它们不再阻塞限定范围的 3C 零预算接入；已通过的基础服务验证不能替代 3C 新增保护 pin、metadata 和 receipt 的正确性验收。自然终结等待窗口不等于硬件 DMA 执行期中断。

### 阶段 3C：最终 CachePilot 策略原型（首轮消融完成；独立输入未稳定复现收益）

2026-10-11 在途保护预算耗尽 GPU 专项通过：原成功 future 回执被诊断门延迟、占满 32-block 预算；不同请求的 32-block 候选零 pin 回退，原回执随后释放预算，候选之后由默认 manager 保存。347 请求、864 层 KV 全等，原保护两个 chunk/72 层严格来源，运行期资源闭合；见 [在途预算报告](experiments/2026-10-11/INFLIGHT_BUDGET.md)。这只验证预算耗尽，不验证执行故障。

同日完成保护 STORE 的受控 server 执行故障 r3：服务仅锁定一个真实受保护 batch，在 LMCache 的实际 CUDA copy enqueue 后抛出 host exception；未改写的 worker future 收到原始 `false`。LMCache 内层捕获该异常，服务记录 native terminal 并以 `transfer_retired.succeeded=false` 交付失败。worker、protection 与 scheduler receipt 全部失败，32 个保护 pin 恰好释放。整轮 348 请求、864 层回载 KV bitwise 全等、26 个 native terminal 与 909 free block 闭合。此项不覆盖 CUDA driver fault、DMA/PCIe 中断或自然硬件故障，见 [server STORE 故障报告](experiments/2026-10-11/PROTECTION_SERVER_STORE_FAULT.md)。

同日物理容量不足 GPU 专项通过：1.25 GiB KV 池中，合格 32-block 候选仍有全部预算且无在途 pin，但 free=439 小于 reserve+required=452，零 pin 回退后由默认 manager 保存。347 请求、936 层 KV 全等，14 chunk/504 层具备严格保护来源，运行期资源闭合；见 [容量报告](experiments/2026-10-11/CAPACITY_FALLBACK.md)。这是受控压力下的回退验证，不是性能结论。

2026-10-11 一个 chunk 的实际 GPU 保护对照通过：零预算/一个 chunk 预算各 348 请求、输入输出及 14 个默认 STORE 范围一致；单 chunk 组七个新增保护 STORE、峰值 16 block，468 层 KV 全等，其中六个保护批次/216 层严格 STORE→H2D 来源。两组运行期资源闭合、909 free 恢复。此前 512-token 整项的一个 chunk 预算只证明拒绝，本轮 320-token 工作负载证明实际保护。见 [单 chunk 报告](experiments/2026-10-11/PROTECTION_ONE_CHUNK.md)。

2026-10-11 共享受保护前缀请求的自然容量抢占 r2 通过：真实 scheduler 抢占共享请求，32 个保护 block 的引用 2→1→0，保护窗口 39 次分配未复用；347 个完整响应、504 层回载 KV 全等，原批次两个 chunk/72 层严格 STORE→H2D 来源，运行期资源闭合。r1 工作负载成功但审计入口导入失败，原轮保留；修复后 r2 整轮通过。见 [共享抢占报告](experiments/2026-10-11/SHARED_PREEMPTION.md)。受保护源请求已结束，诊断门仅延迟真实成功回执，不覆盖源请求抢占或 DMA 中断。

同日完成无共享前缀 victim 自然抢占 r2 和构造错误代际/重复回执的真实 GPU 拒绝 r1；前者有七次保护期间分配、504 层 KV 全等，后者 864 层 KV 全等，均资源闭合。随后以关闭 vLLM 默认 ID 随机化的隔离诊断补齐真实 HTTP raw ID 重用：两代请求使用同一 raw ID 但不同 wire generation，旧回执不会改变第二代 pending prefix，四种旧 envelope 均被拒绝。正常旧回执和“实际 STORE 成功、报告失败”的旧回执各有一轮通过，第二代两段 KV 共 72 层实际 H2D 且 bitwise 全等。该诊断不是 STORE/DMA 执行故障实验。见 [自然抢占报告](experiments/2026-10-11/PROTECTED_PREEMPTION.md)、[身份报告](experiments/2026-10-11/RECEIPT_IDENTITY.md)和[raw ID 复用报告](experiments/2026-10-11/RAW_ID_REUSE.md)。

2026-10-10 共享受保护前缀的新请求取消专项 r3 通过：347 个完整响应和 1 个取消请求，同一组 32 block 引用按 2→1→0 变化，原回执释放保护引用；936 层 KV 全等，原批次两个 chunk/72 层及全部六批/12 chunk/432 层有无中间 STORE 的严格来源证明，资源清零、909 free 恢复。完整 CUDA 回归 525 passed、115 subtests。新增实验支线 r1 提前回载破坏命中 checkpoint、r2 重复预热产生中间 STORE，使来源验收失败；通过调整专项工作负载解决，不改变保护算法，失败轮保留。见 [共享取消报告](experiments/2026-10-10/PROTECTION_SHARED_CANCEL.md)。该结果不覆盖自然抢占、GPU 回执身份注入、DMA 中断或空闲延迟收尾。

2026-10-10 整项超预算 GPU 回退对照通过：零预算/一个 chunk 各 348 请求，14 个默认 STORE 范围/次数及文本一致，各 720 层 KV 全等、909 free 恢复；17 个候选有 51 次预算拒绝，其中 14 个由默认 manager 保存，无保护 pin/STORE。完整 CUDA 回归 506 passed、115 subtests。见 [预算报告及下一组实验设计](experiments/2026-10-10/PROTECTION_BUDGET.md)。

2026-10-09 继续完成最多两个 chunk 的真实分配前保护。零预算/两 chunk 各 348 请求、输出一致；保护组 9 个保护 STORE、峰值 32 block、19 次零步回滚，864 层回载 KV 全等，其中 7 个保护批次的 14 chunk/504 层有无中间 STORE 的严格来源证明。两组运行期资源归零、BlockPool 恢复 909。首版只选已结束、无外部引用、从 0 开始的一到两个完整 chunk，同步模型调度，保守按全活请求 footprint 留余量；尚不支持较大 STORE 拆分。见 [两 chunk 报告](experiments/2026-10-09/PROTECTION_TWO.md)。新增支线 r1：PREPARED 零步 unpin 刷新 LRU 导致 8 个 owner 残留，已修复并记录；r2 因 GPU 命中而无 H2D 覆盖失败，统一增加固定淘汰流量后 r3 通过。GPU 取消/失败/延迟专项已经完成；首轮性能消融见下一段。

2026-10-11 完成隔离、无诊断日志的正式性能消融：zero、one、two chunk 均跑三次，按 repeat 轮转服务启动顺序。固定 68 请求的两阶段 trace 先建立 12 个可复用 session 的 STORE，再用 8 个独立 pressure session 驱逐，最后回访以产生 H2D。九个 cell 都有 68/68 完整响应、固定 token 合约、1,811,939,328 B H2D、资源闭合和零服务错误；非零组都有保护 commit/receipt。one chunk 的 TTFT P95 均值为 6979.65 ms，较 zero 的 7442.47 ms 低 6.22%；two chunk 为 7185.36 ms，低 3.45%。所有组 prefill、GPU/external hit 和 H2D 相同，one/two 的 D2H 分别低 3.07%/14.03%。输出哈希仍有跨配置差异，三次重复不支持统计显著性或泛化结论。完整表格、原始归档与限制见 [保护预算消融](experiments/2026-10-11/PROTECTION_ABLATION.md)。这满足受控 3C 首轮的性能交付，但不关闭独立验证和生产资源边界。

2026-10-10 完成新增保护 STORE 的回执专项。对第一批真实保护 future 分别注入 5 秒延迟和“真实成功后报告失败”：延迟加固定恢复流量时 508 请求、720 层 KV bitwise 全等，pin 在回执前保持；失败回执时 worker/scheduler 失败状态一致、pin 正确释放，864 层 KV 全等。无后续流量的延迟轮仍在空闲收尾留下 pending owner，失败证据保留。两种注入只覆盖回执交付诊断，不是 DMA 中断或真实 STORE 执行失败。见 [保护回执专项](experiments/2026-10-10/PROTECTED_RECEIPTS.md)。

2026-10-09 完成 `ProtectionConnector` 的零预算真实 metadata/receipt 交接。默认 EVICTION_AWARE、保护关闭、开启但预算为零三组各 96 请求，默认 STORE 范围/次数及文本一致，各 6 chunk/216 层回载 KV 全等、14 批交接和资源闭合。完整 CUDA 回归 460 passed、115 subtests，见 [报告](experiments/2026-10-09/PROTECTION_ZERO.md)。本轮未增加保护 pin/STORE，使用固定短尾流量推动默认 pending 收尾，不覆盖旧 r6 的空闲边界。实际保护与性能结论仍待后续两步。

前期首版纯 Python 状态机已完成预算回退、前缀闭合、身份复核和取消/回执所有权测试，见 [PROTECTION_STATE_MACHINE.md](experiments/2026-09-30/PROTECTION_STATE_MACHINE.md)。除简化模型外，已新增 16:1 物理 block/chunk 分组模型和七项测试。它们使用需求与身份oracle，当时只有CPU对象契约；本轮真实 scheduler adapter 是独立实现，旧模型本身不能代替GPU接入或收益验证。

随后增加真实 vLLM BlockPool 的 CPU 元数据桥接八项测试和同 request-id 跨代单在途模式；这未安装进 scheduler，也不包含 CUDA tensor。随后增加真实token ledger的两条hash链证明（6项测试）及实际LMCache STORE metadata的CPU交接契约（7项测试）。接入所需的hash来源、零计算步回滚、回执身份和部分保存边界已写入 [adapter 契约](PROTECTION_ADAPTER_CONTRACT.md)。

这是当前项目的核心策略实现，不等同于已经完成的 adaptive horizon。按 [3C 开工顺序](3C_GPU_ENTRY.md)，先实现零保护预算的外部 ProtectionConnector，使用同一所有权栈上的默认 EVICTION_AWARE 作对照，确认真实交接且无额外 pin/STORE；之后最多两个 chunk 的小预算验收，正确性通过才关闭诊断日志做重复消融。保护实现步骤为：

1. 定义准入前需求预算和保护预算，避免把所有 waiting 请求都当成近期风险；
2. 选择候选 KV，执行 hash revalidation 和 prefix closure；
3. 在合法计算继续前 pin 候选，保护预算不足时回退默认策略，不能让 scheduler 饥饿；
4. 将 STORE action 交给 worker，等待 receipt 后再 unpin；
5. 处理取消、抢占、部分保存、保存失败、迟到回执和 request-id generation 隔离；
6. 增加正式策略工厂和配置开关，但默认路径保持不变。

阶段 3C 的验收不是“预测命中率高”，而是：生命周期状态可证明闭合、没有重复 STORE/泄漏/死锁，并在受控 GPU 消融中同时报告实际 prefill、TTFT P95、D2H/H2D、排队和保护代价。

### 阶段 4：对照、复核与展示（首个独立输入复核完成，容量与负载扩展待完成）

2026-10-11 固定策略后完成独立文本 namespace 的 zero/one/two 各三次复核：68 条 prompt 与开发集 SHA 交集为零，结构/到达/输出 token 合约一致，输入 token 总数从 74,652 变为 75,604。九格 612 请求通过运行期和退出后资源门槛；三格已主动关闭后的精确 vLLM traceback 按新门槛单列，历史失败不改写。TTFT P95 均值为 zero 7148.41、one 7107.42、two 7170.89 ms，相对变化 -0.57%/+0.31%，开发集 -6.22%/-3.45% 的改善没有稳定复现。三组 prefill、hit 和 H2D 相同，不证明减少重算或回载，也不证明策略普遍无效。输出差异、原始重复和完整限制见 [独立输入复核](experiments/2026-10-11/PROTECTION_ABLATION_VALIDATION.md)。这是同生成结构的输入复核，不是真实 Agent 泛化；当前不选生产默认预算。

随后按预注册方案完成 1.5 GiB GPU KV 容量扫描：zero/one/two 试点各一次及三次正式重复，共 12 格、816 请求全部通过 token/H2D/资源/日志门槛。正式 TTFT P95 相对 zero 为 one -5.09%、two -3.99%，但 H2D 增加 8.92%/6.49%，one 波动更大，n=3 不作显著性或生产默认结论。2.5 GiB zero 试点完成 68/68 请求且无 service error，但运行期和退出后各留下 1 个 owned job 与 1 个 active session，资源门槛失败；按规则停止 one/two 和正式重复。独立诊断两轮定位到最后完成请求的 pending `[0,1024)` lazy STORE 未提交，空闲零 token 步不会推进；追加三个压力请求后观察到 STORE/terminal/END/reclaim 归零。证据与限制见 [容量扫描](experiments/2026-10-11/CAPACITY_SCAN.md) 和 [owner 诊断](experiments/2026-10-11/CAPACITY_OWNER_DIAGNOSIS.md)。这不是 GPU 传输仍在执行的证明，也不能直接归因于保护策略。

在上述诊断基础上预注册了同栈 2.5 GiB 空闲收尾容量试点：固定 15 秒宽限和同一 68-request trace，zero/one/two 仅改变保护预算；额外要求运行期与退出后的 `active_sessions=0`、`temporary_count=0`，三格 pilot 全通过后才允许九格 formal。pilot/formal 使用独立目录，源代码 SHA 和 trace SHA 在每格冻结校验；任何一格失败即停止，不把历史失败或诊断数据并入性能均值。pilot 三格及 formal 九格均已通过独立复算；formal 612 请求、每格 idle drop=1，one/two TTFT P95 均值相对 zero 为 -3.07%/-2.00%，但首组重复退化。两格退出 traceback 和输出 hash 差异保留，见 [正式报告](experiments/2026-10-11/IDLE_CAPACITY.md)。

针对该边界实现了默认关闭的 opt-in 空闲 pending STORE 收尾候选：仅在同步单卡 text-only、scheduler 真正 quiescent、无 manager/保护在途时处理已结束且 wire identity 一致的 pending 条目；使用原 policy `drop_request()`，不释放 GPU block、不修改已提交 batch，随后清理 session 并发送 END。立即收尾的 68-request 轮以 H2D=0 失败，保留为过早丢弃反例；15 秒宽限轮 68/68 完成、只丢弃 1 个条目、H2D=1,509,949,440 B 且资源归零。zero 重入和 two-chunk 重入均完成首轮及重复轮，job/session/transfer/read pin 归零；重复输出 hash 差异分别保留 5/68 和 6/68。该候选只完成固定单卡初步验证，诊断结果不并入容量性能聚合；多进程/异步调度、共享前缀、取消/抢占、失败回执和真实 driver fault 仍开放。详见 [空闲收尾候选](experiments/2026-10-11/IDLE_FINALIZE.md)。

在阶段 3C 通过后，固定相同 trace、版本、资源和日志设置，对比原生 vLLM、立即卸载、默认 EVICTION_AWARE、调优固定策略、allocation signal 消融和最终策略；每个关键配置至少三次重复，保留退化和失败案例。然后使用 Qwen3-8B + RTX 5090 32GB 做扩展复核，单独分析模型规模和显存变化的影响。

空闲收尾候选进一步通过 2.5 GiB two/15 秒宽限的共同覆盖诊断：首轮＋重入 136 请求、9 次保护提交/原回执、2 次 idle drop，137 job/280 reservation/84 transfer 全部闭合。两种动作的 wire identity 不相交，只证明同实例兼容；重复文本 hash 8/68 差异保留，不作性能或 KV bitwise 结论。首次 SSH PATH 缺少 vllm 的启动失败 a 归档，修正环境后的 b 通过。完整显式 CUDA 回归 686 passed、115 subtests。下一门槛仍是实际 raw ID 复用/取消与失败回执交互；这组诊断不恢复或改写原 2.5 GiB 性能扫描。

补充检查点：已完成空闲收尾后 68 对实际 raw ID 的两代回放，136 请求中每个旧 owner 都在新 lookup 前回收，含实际 idle-drop 后重入；9 保护提交/回执及 137 job/282 reservation/91 transfer 闭合。文本 hash 差异 8/68 原样保留，完整显式 CUDA 回归 703 passed、115 subtests。随后完成原回执延迟/失败及共享取消组合矩阵：失败轮和 idle drop=0 的兼容性轮均原样保留，不能概括为矩阵全通过，见 [组合矩阵](experiments/2026-10-11/IDLE_RECEIPT_MATRIX.md)。上述冻结后的 2.5 GiB pilot 三格和 formal 九格已通过；当前按预注册开始六个固定参数点与 one/two 同期对照的 pilot，随后三次 formal 与新文本留出验证。这仍属于阶段 4 的对照验证，阶段 5 未开始。

### 阶段 5：项目收尾与上游评估（未开始）

整理环境、源码快照、原始 trace、运行命令、结果图表和个人贡献；重新核查 LMCache/vLLM 上游状态。只有在问题仍可复现、存在最小可维护修复并有回归测试时，才提交 issue/PR，不把上游贡献作为当前实验前置条件。

## 5. 开发过程中新增的支线记录

| 支线 | 触发原因 | 已解决/结论 | 对主线的影响 |
|---|---|---|---|
| 环境兼容性 | 计划中的 CUDA/vLLM 组合与租用环境不一致 | 固定为 CUDA 13.0、vLLM 0.30.0、LMCache 研究提交并完成接入 | 后续结果必须绑定实际版本，不能套用原计划兼容表 |
| 输出差异与 KV 正确性 | 多轮回放出现少量输出不同 | 回载 KV bitwise 相同；独立编译 1/16、独立 Graph 0/16 差异；非异步成对 decode 6768/6768 层相同，根因未定位 | 性能结论不能用文本相同替代正确性结论 |
| 自适应 horizon | 原计划预期它是第一版机制 | 三次重复未证明收益，保留为探索性对照 | 最终策略改为准入前保护研究 |
| 异步分配与压力信号错位 | `dropped_evicted` 与 allocator 分配时序不一致 | 确认是 policy 观测语义错位，不是 vLLM 漏分配；allocation 仅作消融 | 新增信号诊断和上游候选问题记录 |
| 准入前需求观测 | 事后修正信号无法挽救已被覆盖的 KV | `compute_slots` 低估，lookup 信号误报较多，暂不保护 | 增加阶段 3A，阻止过早实现 pin |
| 生命周期与取消 | 保护策略会改变在途 STORE 和资源释放时序 | 历史原生路径失败保留；opt-in 原 token/native terminal 实际服务八场景、自然抢占两轮及 KV 审计通过 | 受控单卡 3C 可开工；生产完整门槛、长延迟空闲收尾仍开放 |
| RETRIEVE underflow 读锁 | 诊断 Connector 故意提交空 block ID 列表，MP server 返回原始 `false` | 两次复现，第二次确认注入前 0、目标后及 vLLM 退出后均为 17；正常 Connector 是否可能触发尚无证据 | 独立保存上游候选问题，项目收尾时核查版本、最小修复和回归测试；不外推至自然故障 |
| 原生 lookup 取消后资源残留 | 受控暂停 MP server 时断开等待 lookup 的客户端 | 三次未修改 server 复现 17 个读锁，r3 有 1 个 job；两轮候选 server 的受控 L1 复测逐对象释放 17 个锁且 job 清零 | 真实 FS L2 受控取消两轮候选通过、一轮对照失败；继续验证协议乱序、无 END_SESSION、自然故障和最小补丁；GPU 保护仍受 3B 门槛约束 |
| 上游修复评估 | 发现可能有可复现的压力信号缺口 | 已单独记录复现、影响和 PR 条件 | 延后到阶段 5，不阻塞项目主线 |
| STORE 合并丢身份 | 新 wire 协议的多 chunk lazy STORE 得到 HTTP 500 | opt-in coalesce 校验并保留获取时 configs，六项协议测试及两轮抢占通过 | 新协议兼容问题，不能外推为原生 LMCache 缺陷 |
| 回执长延迟与空闲收尾 | r6 在 30 秒受控回执延迟下留下两个空 owner | pin/transfer 清零、关闭时回收；根因待进一步确认，失败证据保留 | 隔离实例逐轮 owner/job 审计；扩大长期运行范围前必须处理 |
| 空闲 pending STORE 收尾 | 2.5 GiB zero 在请求完成后保留未提交 `[0,1024)` pending STORE，零 token 步不推进 | 新增默认关闭、版本锁定的 opt-in 收尾候选；立即收尾 H2D=0 失败，15 秒宽限和两次重入初步通过 | 只允许固定单卡同步 text-only；候选边界实验和生产故障恢复仍需补齐 |
| 空闲收尾容量准入 | 初始 2.5 GiB zero 因 owned job/session 残留停止，无法直接比较保护预算 | 预注册同栈 2.5 GiB pilot→formal：固定 trace/源码签名，zero/one/two 仅改预算，额外检查 active session 与 temporary object 清零 | pilot 三格全通过才进入 formal；任何失败停止，不能作为生产默认或泛化性能结论 |

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

1. 开发集和首个独立文本验证 trace 已固定，验证中整体 TTFT 改善未稳定复现。先冻结策略；按 [容量扫描结果](experiments/2026-10-11/CAPACITY_SCAN.md)，1.5 GiB 试点及三次重复已完成，2.5 GiB zero 试点因 owned job/session 残留停止。独立诊断已定位空闲未提交 pending STORE；opt-in 收尾候选已通过 15 秒宽限与 zero/two 重入初步验证，立即收尾 H2D=0 反例保留。候选输入队列/关闭与资源事件审计、实际 drop 与保护的同实例交互，以及另行预注册的新栈容量复测已完成。六点固定参数 formal 24 格已完成，选中 h5.0/d1；保护 one/two 对选中固定基线为 +0.88%/-0.19%，没有稳定优势。原新文本验证在 one 零覆盖处停止。独立无保护 reference 12 格完成，native 整体 TTFT P95 最低，但缓存组末轮回访更快，阶段拆分另标为事后分析。压缩到达 formal 12 格已完成，native 仍最低；一次独立零覆盖诊断复现 9 个容量回退，空闲预算无法解除保守 reserve 约束。长 prefill pilot 在第三格默认 fixed 的空闲 wire identity 检查处失败，剩 1 owned job/11 sessions，矩阵停于 2/4；它成为新的边界支线。下一步先做只观察身份的独立诊断，再补最小重现与修复门槛，之后另立新栈 pilot/formal、稳定 decode 和真实结构压力。保留无保护/无回载覆盖、退化和失败点，不为通过门槛挑选结果；若调整策略，另建未见验证集合，不据现有结果选生产默认值；
2. 使用 Qwen3-8B + RTX 5090 32GB 做扩展复核，并分开分析模型规模、显存和 PCIe 链路差异；
3. 继续补齐 P2 的 DMA/排队/重算分解，避免把 D2H 或服务端队列变化误认作唯一原因；
4. 继续记录 3B 支线限制：30 秒回执延迟的空闲 owner 收尾、真实 driver fault、永久 I/O 的自动恢复、L2 不确定提交异常的实机注入及可维护上游最小补丁。限定隔离研究结论不扩大为生产安全结论；
5. 在已拆分编译/CUDA Graph 的基础上定位剩余输出差异，扩展异步 decode 正确性边界；
6. 项目收尾时重新检查上游状态，再决定是否提交 issue/PR。

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


## 2026-10-11 22:54 身份支线更新

长 prefill 独立时序定位了我们适配层的缺口：真实抢占重建 tracker 生成新 generation，底层 PREEMPTED 路径跳过 lookup，因此 lookup-only mapping 未更新；新代 STORE 与旧代 END 分离。两项真实 Connector/manager CPU 复现通过，完整 CUDA/native 746 passed；两个 GPU 诊断资源仍失败，尚未修复。下一步先分离 lookup 路由与所有 session 代际终结登记，验证迟到旧 STORE/回执、连续抢占、raw ID 复用、取消及 END 部分失败，再逐身份实机验收。此项是所有权适配支线，不算保护策略改进；通过后另立新栈长 prefill pilot，不能改写原失败或直接启动 formal。详见 [报告](experiments/2026-10-11/PREEMPTED_IDENTITY.md)。


## 2026-10-12 generation 接线支线

独立 opt-in 零保护 Connector 已实现 acquisition-time ticket、真实 pending admission/drop、跨进程原 batch receipt、lookup 路由与 current STORE 代际分离、owner-thread future polling 及 producer seal。真实库 callback 契约和完整显式 CUDA/native 805 项回归通过，详见 [接线报告](experiments/2026-10-12/GENERATION_BRIDGE.md)。下一门槛是固定原 long-prefill trace 的独立 GPU pilot：逐 wire 服务端 END handler return、session/job 快照及 native terminal/资源闭合必须同时通过。通过前不进入 formal；通过后 lazy 对照使用同一新栈，immediate 保留原 connector 并明确为整套配置比较，诊断日志关闭另行冻结，原失败与旧性能不改写。此项属于所有权适配支线，不作为保护策略收益。

2026-10-12 13:21：独立 GPU 诊断 b/c 已通过，c 增加实际 reservation/native terminal 与 BlockPool 闭合，78 代际全部逐身份 END。启动失败 a、原 pilot 和旧两个诊断仍失败。下一门槛已推进到 [新性能预注册](experiments/2026-10-12/GENERATION_MATRIX_PREREGISTRATION.md)：四配置 pilot 后三次正式重复，零保护限定，不将所有权修复当作策略收益。完整 CUDA/native 回归 824 passed、115 subtests。
