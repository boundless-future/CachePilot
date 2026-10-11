# 实际 GPU 保护回执的身份拒绝诊断

固定 4090 24GB / Qwen3-4B BF16 / eager 同步调度 / TP=1，保护预算两个 chunk。r1 通过：347 个完整请求、13 个 STORE（8 个保护），24 次 chunk 回载 / **864 层 bitwise 全等**。七个保护批次、14 chunk / 504 层有严格来源，指定原批次两个 chunk / 72 层完整覆盖。

在真实 scheduler 的 `ProtectionConnector.update_connector_output` 边界做两次受控注入：

1. 原保护 batch 已登记且 32 个 block 被 pin 时，用相同 raw request ID、不同 batch ID 注入错误代际 envelope。真实 HandoffLedger 拒绝；注入前后 ledger、manager/protection 在途标记、block 引用/hash/free 数和原生 `on_store_results` 调用计数完全不变。
2. 原始成功回执正常处理并解除保护后，重放同一批次的成功 envelope。真实 ledger 因“没有原始在途 STORE”拒绝，原生 manager 没有再次调用、引用保持零。

两次注入都由实际验证入口处理；诊断代码只捕获预期 ValueError，非预期异常或错误接收直接失败。真实 future 的成功结果仍是唯一正常释放依据，120 秒回执门超时失败，不伪造终结。

**范围限制：**错误代际 ID 是受控构造，未通过 API 真正重用同一个 raw request ID 创建下一代请求。因此可以说明真实 GPU 保护状态下错误身份拒绝、重复回执不重复释放；不能声称已经覆盖实际 ID 复用后的跨代迟到网络回执。它也不是 DMA 中断实验。

运行期 session/job/controller/transfer/锁/pin 归零；26 个原 reservation 恰好释放、25 个 native terminal 通过；最终 909 free、引擎退出后 GPU registration 清零。目标 external hit 512、GPU hit 0，恢复文本一致。固定尾部流量不证明空闲延迟收尾已修复。

新增 11 项身份审计测试，完整显式 CUDA 回归 **554 passed、115 subtests passed**，121 warnings，16.68 秒。

证据：[r1/result.json](receipt-identity-evidence/r1/result.json)、[独立审计](receipt-identity-evidence/r1/receipt-identity-audit.json)、[回归](receipt-identity-evidence/regression.txt)，同目录保留原始事件、KV 探针、配置、请求和状态。

```bash
python scripts/protection_zero_smoke.py --output artifacts/receipt-identity-new --bounded-workload --modes two --receipt-identity
python -m scripts.audit_protection_receipt_identity artifacts/receipt-identity-new/two --output artifacts/receipt-identity-new/two/audit.json
```
