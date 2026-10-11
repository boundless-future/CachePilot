# 一个 chunk 的实际 GPU 保护对照

固定 RTX 4090 24GB、Qwen3-4B BF16、vLLM 0.30.0、LMCache 研究提交，eager、同步调度、TP=1，2 GiB GPU KV 和 16 GiB L1。将源和压力前缀缩为 320 tokens，使 pending STORE 恰好包含一个完整 256-token chunk。串行运行零预算与一个 chunk 预算；这不是性能消融。

两组各 348 个请求，逐请求输入/输出一致。默认 STORE 前缀、范围与次数一致，都是 14 个；零预算无新增保护，单 chunk 组新增七个保护 STORE、峰值 pin 为 16 个物理 block，另有 14 次零 token 步回滚。单 chunk 组 13 chunk / **468 层回载 KV bitwise 全等**，其中六个保护批次各一个 chunk / 合计 216 层有严格 STORE→native terminal→H2D 来源；第七个保护批次未获得严格来源覆盖，不纳入该结论。零预算组 11 chunk / 396 层全等。

两组目标恢复 external hit=256、GPU hit=0，文本一致。运行期 session、job/controller、transfer、锁、pin 归零；原 reservation 分别 12/14 个恰好释放，最终均为 909 free，引擎退出后 GPU registration 清零。固定短尾流量属于本轮工作负载，不证明空闲延迟收尾已修复。

完整显式 CUDA 回归在本轮脚本后为 **567 passed、115 subtests passed**，121 warnings。该结果首次覆盖一个 chunk **实际被 pin、提交并回载**；此前 512-token 整项超过一个 chunk 预算的对照只覆盖回退。

证据：[比较](one-chunk-evidence/comparison.json)、[零预算](one-chunk-evidence/zero/result.json)、[一个 chunk](one-chunk-evidence/one/result.json)，同目录有请求响应、原始事件、KV 探针、状态和配置。服务 `.log` 按仓库规则不提交。

```bash
python scripts/protection_zero_smoke.py --output artifacts/protection-one-chunk-new --bounded-workload --modes zero one --one-chunk
```
