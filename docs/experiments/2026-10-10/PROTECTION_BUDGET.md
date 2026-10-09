# 3C：整项超过保护预算时回退

RTX 4090 24GB / Qwen3-4B / vLLM 0.30.0，2 GiB GPU KV、16 GiB L1，eager、同步调度、TP=1。同一代码快照串行运行一个 chunk 预算和零预算，固定请求输入。不是性能实验。

| 项目 | 零预算 | 一个 chunk 预算 |
|---|---:|---:|
| 请求数 | 348 | 348 |
| 默认 STORE 批次 | 14 | 14 |
| 新增保护 pin / STORE | 0 / 0 | 0 / 0 |
| 回载完整 chunk / 层数 | 20 / 720 | 20 / 720 |
| KV bitwise 不同层数 | 0 | 0 |
| 最终 free block | 909 | 909 |

两组逐请求输入、文本和默认 STORE 前缀/范围/次数完全一致。目标恢复外部命中 512 token、GPU 命中 0；运行期 session/job/transfer/读写锁/pin 归零，引擎退出后 GPU 注册归零。

一个 chunk 预算为 16 个物理 block，候选整项需要 32 个。只读 `candidate_exceeds_budget` 事件记录 51 次拒绝、17 个不同候选身份，其中 14 个随后由默认 manager 保存。其余 3 个没有后续 STORE 证据，不将拒绝数量等同保存数量。没有拆分候选或新增引用，默认淘汰仍可发生。

审计按 wire identity、前缀 hash、范围及时间先后关联拒绝与默认 STORE，禁止保护 PREPARED/commit；独立交接及所有权审计检查实际回执和 native terminal。增加跨代、时间倒置、隐藏 pin 等负例。服务器完整 CUDA 回归 **506 passed、115 subtests、121 warnings**。

证据见 [comparison.json](protection-budget-one-evidence/comparison.json) 及同目录的 result、原始 events/probe、请求响应和回归日志。完整服务日志保存在服务器，也已下载到本地，但按仓库 *.log 规则不提交。

## 复现

激活 cachepilot 环境，确认端口空闲，使用新输出目录：

```bash
python scripts/protection_zero_smoke.py --output artifacts/protection-budget-new --bounded-workload --modes zero one
CACHEPILOT_RUN_CUDA_CONTRACT=1 python -m pytest -q
```

本轮分别运行 `artifacts/protection-budget-one-20261010/one` 和 `artifacts/protection-budget-zero-20261010-r1/zero`，随后离线核对全部输入/输出和 STORE signatures。组合入口执行相同的比较。

## 范围与下一组实验

只关闭“512-token 整项大于 1-chunk 配置预算”的回退验收，不覆盖在途保护耗尽预算、容量不足、一个 chunk 实际保护、长 pending 拆分或性能收益。

当前候选必须已结束且无外部引用，不能自然取消或抢占已结束的保护源请求。下一组实验明确如下（尚未执行）：

1. 共享引用取消：在保护 STORE 回执等待期间，新请求命中同一 GPU 前缀，再断开客户端。证明新请求取消仅释放自己的引用，原回执随后恰好释放保护引用，并验证回载 KV 和资源终态。
2. 容量自然抢占：同一等待窗口引入并发压力，要求实际发生 preemption、allocator 不复用受保护 block、恢复 KV 一致。若抢占的是不共享前缀的请求，只能算并存压力测试。
3. 重复/跨代回执：独立诊断 connector 对真实原 batch 注入旧 envelope，要求在 manager 释放引用前拒绝，且当前代引用不变；不写成自然网络故障。

空闲延迟收尾仍未修复，失败轮单独保存；不得用额外流量掩盖空闲验收失败。
