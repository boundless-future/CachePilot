# Lazy-offload 生命周期契约测试

## 范围

本轮直接调用锁定 LMCache 版本中的 `LazyOffloadRequestRegistry` 和 `EvictionAwareStoreQueue`，验证 generation、在途 STORE、迟到回执和 prefix-chain 状态的契约。测试使用 fake metadata 和 fake pool，不启动模型，也不修改 GPU block；它们是上游类的状态测试，不等同于 vLLM 真实取消/抢占端到端回放。

覆盖的 6 个场景：

- preemption reset 会把旧 generation 的在途 batch 标为 orphaned；迟到回执仍能释放其 block，且第二次回执被拒绝；
- 已完成 request-id 被新请求重用时，旧 batch 被 orphaned，失败不会归因到新 generation；
- 一个请求在前一批 receipt 到达前不能注册第二个 STORE batch；
- preemption drop 清除旧的 pending 操作和 broken-prefix 标记，恢复后允许新一代重新入队；
- STORE 失败会丢弃 pending 后缀，并拒绝同一 generation 后续无前缀的 STORE；
- request-id reuse 丢弃旧 pending 状态后允许新 generation 重新接收 STORE。

## 结果

GPU 服务器环境：RTX 4090、Python 3.12.14、LMCache `0.5.5+g1a4b40b1d`。6/6 生命周期契约测试通过；完整项目测试集 38/38 通过。开发机没有安装 LMCache，因此同一测试文件在本地 6 项自动跳过，其他 32 项通过。

这证明当前锁定版本的 registry/policy 层对 reset、reuse、failure 和 late receipt 有明确且可测试的状态语义。它没有证明以下路径已经端到端正确：HTTP 客户端取消如何到达 vLLM、远端 async lookup 中途 abort、实际 GPU block 在取消时何时释放、worker STORE 失败与 scheduler 回执之间的时序、以及真实抢占后重新回载的输出一致性。

## 阶段决定

当前没有发现需要立即修改 CachePilot policy 的生命周期缺陷，因此不进入提前 pin/准入实现。下一轮应使用真实 vLLM/LMCache 服务做小规模取消和抢占回放，采集 request generation、`WAITING_FOR_REMOTE_KVS`、STORE receipt、GPU block 释放和最终请求状态；保存失败可以先通过 fake worker 注入，再决定是否值得做端到端实验。

## 复现

```bash
python -m unittest discover -s tests -p 'test_lifecycle_contract.py' -v
python -m unittest discover -s tests -v
```

GPU 环境需要在 `cachepilot` Conda 环境中执行。测试文件见 [test_lifecycle_contract.py](../../../tests/test_lifecycle_contract.py)。
