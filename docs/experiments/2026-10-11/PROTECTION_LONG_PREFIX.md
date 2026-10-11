# 大于 512 token 的 pending STORE 前缀拆分

## 目的

验证一个大于保护窗口的 pending STORE 不必整体回退：将 `[0,1024)` 拆成受保护前缀 `[0,512)` 与默认 manager 后续处理的 suffix `[512,1024)`，同时保持 LMCache 的 prefix closure、回执代际和 policy ledger 语义。

## 实现边界

同一个 raw request ID 同时只能有一个 worker STORE 在途。因此 `ProtectionConnector` 只向 worker 提交前缀，suffix 保留在 policy `_pending`：前缀成功回执后由默认 manager drain suffix；前缀失败时调用 `mark_store_failed` 丢弃 suffix。拆分前缀不是一条完整原操作，不能提前增加 policy 的 `emitted` 计数；只有 suffix 被默认 manager 提交或因失败丢弃时，原操作才在账本中闭合。

范围仍限于 eager、TP=1、单 full-attention group、同步调度。它只让“前两个完整 chunk”可从较长 pending 操作中得到保护，并未扩大 32-block 保护预算。

## GPU 结果

4090 24GB、Qwen3-4B、BF16 下运行：

```bash
python scripts/protection_zero_smoke.py --bounded-workload --long-prefix --modes two --output artifacts/protection-long-prefix-r2
```

348 个请求完成，9 个保护 STORE、保护峰值 32 block，external cache 命中 1024 token、GPU 命中 0 token；1800/1800 层回载 KV bitwise 一致。8 条链路均满足：保护 `[0,512)` 成功回执后，默认 manager 才提交 `[512,1024)`，block 范围不重叠且 suffix 无重复提交。成功轮结果为 `long-prefix-evidence/success-result.json`。

受控失败路径：

```bash
python scripts/protection_zero_smoke.py --bounded-workload --long-prefix --server-store-fault --modes two --output artifacts/protection-long-prefix-fault-r1
```

348 个请求完成，10 个保护 STORE；一个真实 CUDA copy enqueue 后的 server host exception 使前缀失败。该条 suffix 被丢弃，其余 8 条拆分链正常完成；1656/1656 层 KV bitwise 一致，909 free block 恢复，资源闭合。完整失败证据已保存于 `long-prefix-evidence/`。

## 验收

`tests/test_bounded_protection.py` 覆盖长 pending 的成功拆分、默认 suffix drain、失败前缀丢弃 suffix 及 ledger；`tests/test_protection_long_prefix_audit.py` 拒绝提前/重复 suffix、缺失或错误 generation receipt 和重叠 block。GPU 审计还验证四个 256-token chunk 都有严格 STORE 到 H2D KV 来源证明。

最新服务器专项测试 26 passed；全套普通回归 603 passed、9 skipped、115 subtests passed（9 项 CUDA opt-in 测试需单独启用），日志见 `long-prefix-evidence/full-tests.txt`。本地因没有 vLLM/LMCache 运行依赖，结果为 6 passed、1 skipped，不能代替服务器契约验收。首次只同步新测试而未同步审计脚本，两个拒绝案例失败；同步完整脚本后全部通过。

这不是性能结论。下一阶段应固定该实现，进行重复的消融与容量/并发扫描，并继续把真实 driver/DMA/PCIe 故障作为独立边界。
