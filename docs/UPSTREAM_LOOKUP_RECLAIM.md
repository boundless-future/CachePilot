# LMCache MP lookup reclamation: upstream comparison

记录日期：2026-09-27。本文只记录上游范围和本项目的修复约束，不修改已安装的 LMCache，也不把诊断 wrapper 当作正式补丁。

2026-09-30 再次读取 GitHub API：[状态快照](experiments/2026-09-30/upstream-status.json)。#5339 仍 open；两条评论确认路径并提到 #5363 仅处理客户端 `_returned_finished` 集合，不处理 sidecar prefetch 所有权。#5008 仍 open、`merged_at=null`。本次仅核查这些记录与评论，未声称完整扫描了全部上游提交或已验证最新 dev。

## 直接相关的问题

[LMCache issue #5339](https://github.com/LMCache/LMCache/issues/5339)（`MP mode: remaining unbounded request-state retention in vLLM client and sidecar prefetch bookkeeping`）描述了与本项目原生 lookup 取消复现相同的生命周期缺口：sidecar 的 `_prefetch_jobs` 主要在 `QUERY_PREFETCH_STATUS` 或 `WAIT_PREFETCH_STATUS` 消费结果时移除；客户端取消、RPC 超时或健康状态变化后可能不再 polling，而 `END_SESSION` 本身不保证消费 controller completion state。该 issue 在本项目核查时仍为 open。

本项目在固定的 vLLM 0.30.0、LMCache `1a4b40b1d79b0e76244f127f96ee0982f8bd270f` 上做了三次原生 `LMCacheMPConnector` 受控 lookup 延迟取消：每次 follow-up 都能正常回载，但观察窗口内残留 17 个 CPU read lock；第三次另残留 1 个 prefetch job。server 事件显示 LOOKUP 注册 job 后，END_SESSION 没有查询该 job。这个证据把问题从“客户端请求已结束”进一步收敛为 server-owned prefetch bookkeeping 没有终结路径。

## 范围不同的工作

[LMCache PR #5008](https://github.com/LMCache/LMCache/pull/5008)（`Clean up aborted async lookups without future traffic`）处理的是 `enable_async_loading` worker-side async lookup 的取消清理。其 pending cleanup、完成回调和 exactly-once 释放思路可借鉴，但它不是 MP `LookupModule._prefetch_jobs`、completion bitmap 和 CPU read lock 的直接修复，不能作为本项目问题已经解决的证据。

## 最小正式修复应满足的约束

server 侧应拥有清理责任，客户端是否继续 polling 不能决定资源最终是否释放：

1. `LOOKUP` 注册时生成不可混淆的 request generation，保留 job、lookup key 和锁元数据。
2. `END_SESSION` 原子地把 job 标记为 abandoned。已完成或失败的 job 立即消费 completion result 并释放 read lock；未完成的 job 进入 deferred cleanup。
3. prefetch 完成、失败、超时或 server shutdown 都能触发 deferred cleanup；释放入口必须 exactly once。
4. 正常 `QUERY_PREFETCH_STATUS` + `FREE_LOOKUP_LOCKS` 后，迟到的 `END_SESSION`、重复 completion 和重复 cleanup 都是幂等操作。
5. request-id 重用不能让旧 job 的迟到 completion 释放新 session 的锁，也不能让旧 job 永久保留。

项目新增的 [纯 Python 生命周期模型](../scripts/prefetch_reclaim_model.py) 和 [测试](../tests/test_prefetch_reclaim_model.py) 先验证这些顺序约束。它不是 LMCache 代码的替代实现；GPU 端正式补丁仍需在服务器上针对真实 `LookupModule` 做候选修改和原生 Connector 回归。

后续已加入[源码固定的可选 server 候选](../scripts/lookup_server_reclaim.py)及真实 `LookupModule` 契约测试；两轮原生 Connector + 候选 server 的受控 L1 取消复测逐对象释放 17 个读锁，活动 job 与 controller result 归零，见[实验报告](experiments/2026-09-27/LOOKUP_RECLAIM_CANDIDATE.md)。这仍是研究 wrapper，不是上游正式补丁。2026-09-30 又完成真实 FS L2 在途 prefetch 的受控取消：两轮候选释放 17 个对象并清空 job/result，关闭候选的对照仍残留，见 [L2 报告](experiments/2026-09-30/L2_PREFETCH_CANCELLATION.md)。协议乱序、客户端死亡但无 END_SESSION、永久未完成 controller、自然故障等边界尚未通过端到端验证。

## 对主线的影响

2026-09-30 进一步完成 END 顺序与真实短读候选/对照，见 [实验](experiments/2026-09-30/ORDERED_END_AND_SHORT_READ.md)。无 END_SESSION 的进程死亡观察 630 秒后，读锁及 session TTL 清除但 job/result 仍残留，见 [死亡路径](experiments/2026-09-30/CLIENT_DEATH_NO_END.md)。原生 TTLLock 的旧 key/count 释放会误减 TTL 后的新读者锁，见 [所有权边界](experiments/2026-09-30/TTL_OWNERSHIP_BOUNDARY.md)。正式方案必须把“停止底层读写”“消费 completion”“释放带身份的 reservation”分开；timeout 本身不是终结证明，也不能用短 TTL tombstone 模拟 generation。

这项上游缺口阻塞阶段 3B 的资源安全验收，因此在正式 server 修复或明确的上游版本修复前，不启用 GPU 上的提前 pin/准入保护。它不阻塞 fake worker/state machine、基线整理和生命周期模型开发。阶段 5 再根据当时上游状态、最小补丁和回归证据决定是否提交 issue/PR。
