# 2.5 GiB 空闲 owner 残留诊断

日期：2026-10-11。延续 [容量扫描](CAPACITY_SCAN.md) 的失败点。三轮独立诊断支持：最后一个已完成请求的 lazy STORE 尚未提交，因而 scheduler 暂缓 END_SESSION；空闲零 token 步不能推进该 STORE。不是本轮发现的在途 DMA/读锁泄漏，也不是非零保护 pin 引发的残留。原容量失败保持不变，尚未交付空闲回收修复。

## 证据链

沿用 RTX 4090、Qwen3-4B、eager/同步调度、2.5 GiB GPU KV、16 GiB CPU KV、相同 68-request trace 和零保护预算。未修改安装包或原性能 runner。新增 `diagnose_capacity_owner.py` 独立启动入口、服务端 LOOKUP/QUERY/END 事件及只读 owner 快照；b/c 另用 `CapacityOwnerConnector` 记录 scheduler pending/finished/inflight 与 wire identity。这些记录会改变时序，三轮均不进入性能统计。

| 轮次 | 干预 | 原 68 请求 | 空闲 45 秒后 | 引擎退出后/干预后 | gate |
| --- | --- | --- | --- | --- | --- |
| a | server 日志与快照 | 68/68 token 合约 | 1 owner/session | 引擎退出后仍为 1；server close 最终回收 | false |
| b | 再加 scheduler 账本 | 68/68 token 合约 | 1 owner/session | 引擎退出后仍为 1；server close 最终回收 | false |
| c | b 的记录；原 drain 后追加 3 个固定压力请求 | 68/68 token 合约，另 3/3 请求各 8 token | 干预前仍为 1 | 干预后 owner/session 均归零，退出资源闭合 | 干预后 true，不是原试点通过 |

a/b/c 的残留 owner 均为 `consumed=true, abandoned=false, claimed=false, hit_chunks=0, reads=0, writes=0, error=null`。它已消费零命中的 QUERY，没有 RETRIEVE claim，也没有还在执行的 owner transfer。b/c 将 request prompt token SHA 与 trace 重新 tokenize 后的摘要对应，唯一匹配 **event_id=67、session=s0011、turn=4**（最后一个回访请求）。a 没有 scheduler 身份账本，不能同样声称已映射到 event 67。

b 的同 raw request 在 `request_finished` 后和 connector shutdown 时均位于 finished 集合，policy 保留 `[0,1024)` 的一个 STORE 条目（64 个物理 block ID），inflight 集合为空。客户端仍保留其 wire identity。server 没收到该 owner 的 END_SESSION。server 自身 close 时主动 abandon/reclaim，出现 `owned_shutdown_drained`；这不能代替运行期/引擎退出后门槛。

c 在相同原 trace 完成并等待 45 秒后，固定追加三个 7168-context 生成器请求（独立记录 `resume-trace.json`，每个实际生成 8 token，顺序发送）。旧 owner 的事件顺序为：

```
LOOKUP → QUERY(0) → 原请求完成 / 空闲未结束
→ 恢复压力流量
→ STORE submitted → native terminal seen → transfer retired
→ END_SESSION enter → abandoned → reclaimed → END_SESSION return
```

旧 owner 的 STORE 提交时间为 Unix 1791712468.521958，terminal 为 1791712468.6179895，END 为 1791712468.743204。之后全部 owner/session 清零。没有手工清字典、强制释放锁或改写 receipt；恢复请求实际改变了 GPU 压力和 scheduler 步，因此只作为机制干预，不可混入原 benchmark。

## 代码与契约

固定安装版 `LazyOffloadManager.on_request_finished` 在 policy 有 pending 时不返回 END；`on_scheduler_step` 遇到零 scheduled token 直接返回空动作。`has_inflight_store_work` 明确不把未提交条目视为 in-flight，以免 no-forward 步丢弃 STORE metadata 或引擎空转。是否设置 deferral deadline 不改变这一零步约束。

新增 `test_idle_pending_contract.py` 在真实安装版 manager/policy 上验证：deadline=0 及已经到期的 0.001 秒 deadline 两种配置，finished+pending、无 in-flight 时，连续 100 个零 token 步都不产生 STORE/END，也不自称有 in-flight work。使用轻量 CPU pool fixture，不执行 CUDA，属于安装版行为刻画。诊断包装器另有两项返回/异常保持与快照不变性测试。

服务器专项加既有 lifecycle 测试 **10 passed**；本地仅诊断两项通过，LMCache 契约按缺依赖跳过。未修改生产路径，未重跑全套 CUDA 单元测试。实际 GPU 验证为以上三轮、207 个请求。

b 关闭时有八行 vLLM output-handler/EngineDeadError。因原运行期 drain 已失败，不满足既定关闭分类前提，仍记为 **8 条 service_errors**，未放宽门槛或改为 benign。a/c 无 service_errors。b 的请求完成和 pending/owner 对应发生在该 traceback 之前；该诊断轮整体仍失败。

## 对项目的影响与下一步

这次把一个资源失败点定位成了**未提交 lazy STORE 的空闲生命周期问题**。之前 30 秒迟到 receipt 的 owner 问题属于已提交工作，不能用本次结果将它关闭。原始无日志失败轮只有计数，不能证明它的具体 raw request 或全部内部时序与诊断轮相同；两次受观测复现与一次干预支持同类机制解释。

零预算仍是本项目 owned-service 栈，尚未做本 trace 的原生 Connector 对照，不能据此发布“上游泄漏已复现”结论。被延迟的 owner 没有 buffer pin；也不能把元数据滞留等同于 GPU 内存泄漏。

下一步设计显式空闲收尾：在无 active request、无在途 STORE/RETRIEVE 时，对**已结束且尚未提交**的 STORE 明确选择保留或丢弃。候选应优先验证有界丢弃并发 END 的策略：记录丢弃范围/原因，允许后续请求重算，绝不丢弃已提交批次或释放其 pin。它会改变缓存覆盖，必须作为新 opt-in 变体，补请求重入、共享前缀、跨代身份、在途回执隔离及后续压力/回载测试。不能仅延长 drain timeout、添加保活假请求或忽略 owned_jobs 来让旧试点通过。

先完成 CPU 契约与真实 scheduler 接入审计，再运行独立 GPU 回归；通过后重新登记新的性能实验。当前保留 zero/one/two 原实现和历史容量结果。

## 归档与复现

- [逐 owner 审计](capacity-evidence/capacity-owner-diagnostic-audit.json)：目标状态、请求对应、待卸载范围、原事件及错误行。
- [三轮原始归档](capacity-evidence/capacity-owner-diagnostic-20261011.tar.gz)：日志、status 时间序列、完整请求、trace、manifest、各轮诊断源码及干预前后快照。
- SHA256：`8f3bf58531a3fa49e708c1b5c9df000793cca12bfa4393421c1d615a11509533`，本地/服务器一致。

```bash
python scripts/diagnose_capacity_owner.py --output artifacts/capacity-owner-diagnostic-20261011a --trace artifacts/capacity-pilot-2p5g-20261011a/trace.json
# b 增加 --observe-scheduler，c 再增加 --resume-pressure，分别使用新输出目录。
python -m pytest tests/test_capacity_owner_diagnostic.py tests/test_idle_pending_contract.py tests/test_lifecycle_contract.py -q
```

c 的 `result.json passed=true` 描述追加压力后的终态；其 `before-resume.json` 明确保留原 45 秒门槛失败，不能改写为 2.5 GiB 原试点通过。
