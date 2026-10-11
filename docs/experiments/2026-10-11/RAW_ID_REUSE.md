# 实际 raw request ID 复用与跨代回执隔离

固定 RTX 4090 24GB、Qwen3-4B BF16、vLLM 0.30.0、固定 LMCache 研究版本；eager、同步调度、TP=1、2 GiB GPU KV、两个 256-token chunk 的保护预算。本专项验证生命周期语义，不是性能对照。

vLLM 0.30.0 默认会把内部 raw request ID 变为 `external_id-random_suffix`。单纯重用 OpenAI API 的 `request_id` 不能令 scheduler/LMCache 看见同一个 raw ID。因此此隔离诊断显式设定 `VLLM_DISABLE_REQUEST_ID_RANDOMIZATION=1`，随后对两个真实 `/v1/completions` 请求传入同一个 API ID。这个开关只用于构造可审计的跨代边界，不能把结果外推为默认 vLLM 配置下的行为。

第一代 `cmpl-reuse-cold-0` 的保护 STORE future 被 gate 暂缓回执。第二个 HTTP 请求完成时，scheduler 复用了同一个 raw ID，但 `ProtectionConnector` 为它创建新的 wire identity：client 相同、sequence 与 generation nonce 均不同。旧 batch 被标记为 orphan；第二代的 pending prefix 保持独立。释放旧回执后，旧 batch 的 32 个 pin 从 1 释放为 0，而第二代 pending metadata 与 prefix health 不变。第二代再提交成功后，旧代 success、旧代 failure、重复旧代 success，以及旧代终结后的重复 envelope 均被拒绝，不能影响新 batch。

两个真实 GPU 轮都通过，每轮 348 个请求、14 个 STORE。第二代的两个 chunk 都被实际 H2D 回载，分别 36 层 KV bitwise 全等，整轮为 936/936 层。28 个 reservation 均释放，27 个 native terminal 均通过，最终 BlockPool 恢复 909 free，engine 退出后 GPU registration 为空。

| 轮次 | 旧 STORE 的实际结果 | 交给 worker 的旧回执 | 跨代隔离 | 第二代 KV 回载 |
| --- | --- | --- | --- | --- |
| success-r1 | 成功 | 成功 | 4 个旧 envelope 被拒绝 | 2 chunk / 72 层全等 |
| old-reported-failure-r2 | 成功 | 故意报告失败 | 4 个旧 envelope 被拒绝 | 2 chunk / 72 层全等 |

`old-reported-failure-r2` 只改变 worker receipt 的语义，实际 LMCache STORE future 仍然成功；它不是 GPU、driver、DMA 或 STORE 执行故障。两轮均不覆盖多 worker、网络重连、真正的 DMA fault，亦不能修复或覆盖 30 秒延迟回执后的空闲 owner 收尾问题。

证据：[正常旧回执结果](reuse-evidence/success-r1/result.json)、[正常轮的 API 请求与 generation 记录](reuse-evidence/success-r1/reuse-driver.json)、[报告失败旧回执结果](reuse-evidence/old-reported-failure-r2/result.json)、[报告失败轮的 API 请求与 generation 记录](reuse-evidence/old-reported-failure-r2/reuse-driver.json)。`result.json` 内嵌 `reuse_audit`；同目录保留事件、KV probe、请求响应、状态和配置，服务日志按仓库规则忽略。

```bash
python scripts/protection_zero_smoke.py \
  --output artifacts/protection-reuse-success \
  --bounded-workload --modes two --reuse-id

python scripts/protection_zero_smoke.py \
  --output artifacts/protection-reuse-reported-failure \
  --bounded-workload --modes two --reuse-id --reuse-old-failure
```
