# 在途保护占满预算时的真实 GPU 回退

固定 RTX 4090 24GB、Qwen3-4B BF16、vLLM 0.30.0、LMCache 研究提交，eager、同步调度、TP=1，2 GiB GPU KV。保护预算两个 chunk / 32 个物理 block。本轮为正确性专项，不是性能消融。

原保护 STORE 的真实 future 成功后，诊断门暂缓交付原回执。这个批次占有全部 32 个预算 block；在门仍关闭时，另一位已结束请求的 512-token / 32-block 候选进入保护选择。调度器记录 `inflight_budget`：原批次占 32、可用保护预算 0、新候选需 32，新增 pin 为 0。门随后才打开，原成功回执解除 32 个 pin。该被拒候选之后以同 wire identity、hash 和范围由默认 manager 保存并获得原生回执；没有把预算拒绝当作数据丢弃。

r1 整轮通过：347 个完整响应、13 个 STORE（七个新增保护）；24 chunk / **864 层回载 KV bitwise 全等**。原保护批次的两个 chunk / 72 层有严格 STORE→native terminal→H2D 来源；其余保护批次不全具备严格来源覆盖。目标 external hit=512、GPU hit=0，恢复输出与冷请求一致。运行期 session、job/controller、transfer、锁、pin 均归零，26 个原 reservation 恰好释放、25 个 native terminal 审计通过，最终 909 free，引擎退出后 GPU registration 清零。

新审计检查“不同候选、在途预算、零新增 pin、拒绝早于开门、原回执唯一释放、候选后来默认保存”的完整链条；八项针对性负例测试拒绝伪预算耗尽和缺失默认 STORE。完整 CUDA 回归在此版本为 **575 passed、115 subtests passed**，121 warnings。固定尾部流量属于工作负载，不证明空闲延迟收尾已修复。诊断门不模拟实际 STORE 执行故障。

证据：[原结果](inflight-budget-evidence/result.json)、[独立审计](inflight-budget-evidence/inflight-budget-independent-audit.json)、[driver](inflight-budget-evidence/inflight-budget-driver.json)；同目录保留原始事件、KV 探针、请求响应、状态和配置。主轮结果内的内嵌审计先于后加的“默认 manager 后续保存”检查，独立审计是最终严格版本。服务 `.log` 按仓库规则不提交。

```bash
python scripts/protection_zero_smoke.py --output artifacts/protection-inflight-budget-new --bounded-workload --modes two --inflight-budget
python -m scripts.audit_protection_inflight_budget artifacts/protection-inflight-budget-new/two --output artifacts/protection-inflight-budget-new/two/independent-audit.json
```
