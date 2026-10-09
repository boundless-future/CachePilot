# 源码与证据时间点

- r1 使用本轮初始保护实现：PREPARED 回滚仅调用 `BlockPool.free_blocks`，80 个短尾，无全部压力前缀回放。该轮失败，不覆盖或重写结果。
- r2 使用队首回滚修复、80 个短尾及压力前缀回放。目标恢复仍为 GPU hit，断言后停止，未执行后续回放。结果保留失败。
- r3 使用队首回滚修复、两个各 160 请求的固定短尾及全部压力前缀回放，两组各 348 请求通过。`runtime-source-sha256.txt` 中核心 connector/adapter/audit 文件与实机 r3 相同；在 GPU 服务停止后采集。
- r3 执行时 `scripts/protection_zero_smoke.py` SHA256 为 `17febbd40af9ed5305c5912397ee91a9992d20f905914b2712997798d5eb620d`。之后仅新增了结束时自动调用 `audit_bounded_kv` 并把严格来源结果写入 result 的两行；没有改变请求或服务路径。已归档 r3 result 保持原状，严格来源结果独立写入 `protected-kv-provenance.json`。
- 完整 CUDA 回归在增加严格来源审计前执行：487 passed、115 subtests。严格来源审计后补 5 项测试独立通过，见 `source-audit-tests.txt`。没有把它们计入前述完整回归数字。
- 随后最终代码完整 CUDA 回归为 492 passed、115 subtests，见 `full-regression-final.txt`；这次包含新增来源审计测试。
- `.log` 原文件只重命名为 `.txt`。JSON/JSONL 包含的是请求、状态、身份和 KV 摘要，不含模型或 KV tensor 二进制。
- r3 是正确性验收，没有以耗时比较来报告收益。较早失败轮和后续通过轮的负载长度不同，不应横比性能。
