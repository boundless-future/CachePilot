# 物理容量不足时的保护回退

单 RTX 4090 24GB、Qwen3-4B BF16、vLLM 0.30.0、固定 LMCache 研究版本，eager、同步调度、TP=1。GPU KV 池为 1.25 GiB / 567 个可用 block，保护预算两个 chunk / 32 block。两条并发的 1600-token 输入制造真实物理容量需求；观测到回退后关闭流。该轮是正确性专项，不是性能对照。

r3 中一个已结束请求的 512-token / 32-block 完整候选满足预算（可用 32、在途 0），但当时空闲 439 block，小于保守保留 420 加候选 32。调度器记录 `capacity` 回退、零新增 pin。之后同 wire identity、前缀 hash 和范围的候选由默认 manager 保存；这与在途保护预算耗尽的回退是两个不同门槛。

整轮 347 个完整响应、15 个 STORE（七个新增保护）、26 chunk / **936 层回载 KV bitwise 全等**。七批保护 STORE 的 14 chunk / 504 层具备无中间 STORE 的实际 transfer→H2D 严格来源。目标恢复 external hit=512、GPU hit=0，输出与冷请求一致。运行期 session、job/controller、transfer、锁和 pin 均归零；28 个原 reservation 恰好释放、28 个 native terminal 通过，最终 567 free，退出后 GPU registration 清零。完整 CUDA 回归 **585 passed、115 subtests passed**（普通测试 576 passed、9 skipped）。

r1 启动失败：未激活 Conda，LMCache 脚本找不到 `python`。r2 因 1 GiB KV 小于模型 8192 最大长度所需约 1.12 GiB 而在 vLLM 初始化失败。两轮均未产生有效策略结果。r3 的 1.25 GiB 配置通过；这些失败属于实验配置，不支持算法结论。

证据：[r3 结果](capacity-evidence/result.json)、[独立审计](capacity-evidence/independent-audit.json)、[触发事件](capacity-evidence/capacity-driver.json)。同目录保存事件、KV 探针、响应、状态及配置；服务日志按仓库规则不提交。该轮不覆盖长前缀拆分、实际 STORE 执行故障或空闲延迟收尾，也不证明性能收益。

```bash
python scripts/protection_zero_smoke.py --output artifacts/protection-capacity-new --bounded-workload --modes two --capacity-fallback --kv-cache-bytes 1342177280
python -m scripts.audit_protection_capacity artifacts/protection-capacity-new/two --output artifacts/protection-capacity-new/two/independent-audit.json
```
