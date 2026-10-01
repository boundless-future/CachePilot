# RETRIEVE 原所有权提交计划

日期：2026-10-01。前置[完成适配](TRANSFER_COMPLETION.md)、[接入审计](RETRIEVE_INTEGRATION_AUDIT.md)和[Lookup lease](LEASED_LOOKUP.md)。这是独立 CPU 契约，**未接真实 CUDA、生产 wire 或安装版 LMCache，3B 仍未通过**。

## 实现与工作流

新增 `scripts/transfer_plan_contract.py`，提供服务端可信布局快照 `RegisteredLayout`、不可变提交元数据 `TransferPlan` 和 `PlannedTransferHarness`。布局记录 worker incarnation/rank、不可复用的登记身份、model/world/chunk、各 object group 字节数，以及 kernel group 到 object group 的映射、block 大小和容量。

```text
原 LOOKUP 捕获 key/chunk/descriptor/keys
    → QUERY 原 ReaderTicket
    → prepare_plan(ticket, request key, block IDs, registration, APC skip)
        校验原请求、范围、可信登记与目标 block 元数据
        → claim 整个 worker shard → 原 reservation 验证/pin → 原 buffer
        → 从原 chunk-major keys 构建按 object group 分组的源 buffer 计划
    → enqueue_plan
        在 transfer/Lookup gate 内复核登记、原 entry/buffer/lease
        → submitting → gate 外交给 submitter(plan, bound callback)
    → submit 返回且原身份可信 server callback 确认全部消费停止
        → 一次性 unpin → 原 reservation 处置 → 删除活动 plan
```

客户端只能指定范围、目标 block IDs、登记标识和 APC skip，不能指定源 buffer 或补造 reservation。`LookupJob` 在提交时保存 key 和 chunk 大小快照；后续不从可能变化的 session/context 反推授权。IPC key 的相等比较忽略 `request_id`，因此显式核对请求身份、worker rank、model、salt、world size、reader 数和原 token 序列。

首版仅接受 **start=0 的原 Lookup、full attention、非空且 chunk 对齐的命中后缀**，请求 end 必须等于 Lookup 命中终点。APC skip 必须小于请求范围，且对每个 kernel 的 block 大小对齐。每组 block 数精确匹配范围，block ID 必须为非 bool 的整数、在容量内且组内不重复。支持多个 kernel group 共用一个 object group，不假设两者一一对应。

选中的源 key 从原 Lookup 捕获的 chunk-major keys 按编码 TP rank、object group 和范围取得，确认属于原 slot，再从原 acquired buffer tuple 建计划。L1 元数据锁内复核原 entry、lease、buffer 对象身份和字节数。即使只复制后缀，也保守持有整个原 worker shard；本轮不实现子范围独立释放或多次 split RETRIEVE。

非法客户端元数据在 claim 前抛出 `ValueError`，原 slot 仍是 offered，可由合法重试领取或 END 回收。获取后的 buffer 检查失败则记录错误并按未提交终结；enqueue 前重新登记或 buffer 身份变化也拒绝提交。提交开始后登记变化、END、reset 和 TTL 均不释放在途源；部分提交异常保留整个 lease/plan，直到可信回调终结。原完成适配中的清理失败不自动重试规则不变。

## 验证与证据

- 新增 **19 项测试、42 个子测试**；8 线程重复准备只有一次 claim，30 轮 callback/END/submit 返回竞争通过。
- 覆盖身份伪造、空/越界/非后缀范围、block 数不足或多余、容量/类型/别名、APC 对齐、登记切换及历史身份重用、源字节数和 buffer identity 变化。
- 两 object groups / 三 kernel groups 验证 chunk/group 顺序；TP=2 验证编码 rank 分片与独立终结。以上使用真实 Lookup/storage/controller/L1 方法和独立 native pin，源数据为可复用 64 字节 CPU buffer。
- 定向运行本轮与上一轮完成适配：**41 passed、46 subtests、80 warnings，2.69s**，首轮通过。
- 服务器完整回归：**375 passed、109 subtests、117 warnings，14.89s**。本地 **122 passed、253 skipped、26 subtests，1.48s**；本地跳过源于缺少安装版 LMCache/native 依赖，不能代替服务器结果。
- 首次全量命令的 PATH 拼接导致 `tee` 找不到，命令退出 1，未保留下可解释的 pytest 结果。修正为显式 PATH 和绝对路径 tee 后完成上述全量回归；该命令故障不记作测试或项目设计失败。

完整日志见 [full-pytest.txt](transfer-plan-contract/full-pytest.txt)，源码、依赖与 native hash 见 [evidence.json](transfer-plan-contract/evidence.json)。有效全量结果和日志来自同一次 tee/pipefail 执行。历史 manifest 保持原始快照，本轮明确记录 `owned_lookup_contract.py` 和 `transfer_completion_contract.py` 的预期变化；未改安装版 LMCache 或 native 源码/二进制。

## 适用边界与下一步

可信布局是 CPU fixture 的服务端登记快照，尚非生产 cache context 或 wire 认证。block 校验只验证整数、容量、数量和组内重复，**不能证明 GPU allocator 所有权、真实 shape/dtype/stride 或跨请求 block 冲突**。不可变 dataclass 固定元数据，buffer 仍是受 lease 保护的可变内存引用；恶意替换内存只作为拒绝路径测试，不是支持的生产操作。

Sliding-window、recurrent/aux、非零起点 Lookup、非 chunk 对齐范围、split RETRIEVE 均拒绝，不仿造 native 下采样或窗口布局。APC skip 仅保存和验证；CPU submitter 不执行真实 skip/copy。登记历史、完成/失败状态保留用于审计，尚无有界 tombstone 回收或重启恢复。

下一步核查 native completion dispatcher 的 payload/queue、stream callback 注册与失败路径，明确如何绑定原 transfer 身份及多 stream 的停止证据。随后接版本化 QUERY/RETRIEVE metadata 和实际 worker 布局，进行真实 pinned CPU→CUDA 内容、callback/event 顺序与 allocator 复用验证，再接 Connector/vLLM。writer 所有权、无 END 失联、永久不完成及安全 shutdown 仍属于 3B 未关闭条件；本轮通过不代表保护策略收益或 3C GPU 接入。
