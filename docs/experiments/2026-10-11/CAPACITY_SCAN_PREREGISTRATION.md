# 保护预算容量扫描预注册

日期：2026-10-11。本文在启动容量实验前固定 3C/P4 的下一步。目标是确认固定保护实现是否能在不同 GPU KV 容量下完成可审计的 zero/one/two chunk 对照；这不是寻找能产生更好数字的容量点。

## 不变条件

- 保护实现固定为提交 `6e63000` 所引用的代码，服务器关键脚本 SHA 已与本地相同：`run_protection_ablation.py` 为 `cfacf7f36b1d9b8207f6848c85070160db0014a2943bc67efb872d5dc1941646`，`summarize_protection_ablation.py` 为 `354aec0f3c0e9204d9ceb836c7f6c90aad8269b0fd268bb70c2c97e8414b35a6`，`protection_connector.py` 为 `9c62c575c17f2499f5ab061e2e7a9440798aaf30c8b6844a0dee729e39c21e59`。
- RTX 4090 24GB、Qwen3-4B BF16、vLLM 0.30.0、固定 LMCache 研究提交、eager、TP=1、同步调度、16 GiB CPU KV、`max-num-seqs=4` 与现有服务脚本保持不变。
- 使用已公开的 `validation-20261011a` trace：68 请求、相同 prompt/到达/会话结构、same seed 和输出 token 合约。它已经参与前一轮输入复核，因此本扫描不再把它称作未见验证集。
- 每格独立启动 vLLM 和 LMCache，端口仍为 8000/8080/5555，不能与其他服务并行。

## 容量与顺序

先后只测试下列两个点；容量使用二进制字节并由每个 cell 的 `run-manifest.json` 记录：

| 阶段 | GPU KV 容量 | `--kv-cache-bytes` | 初始运行 | 正式运行的前置条件 |
| --- | ---: | ---: | --- | --- |
| A | 1.5 GiB | 1610612736 | zero/one/two 各一次，轮转 zero→one→two | 三格均完成 token 合约、H2D > 0、资源闭合、零 service error，且非零组至少一个 protection commit/receipt |
| B | 2.5 GiB | 2684354560 | zero/one/two 各一次，轮转 zero→one→two | 同上 |

只有某容量点通过其三格可行性门槛，才会在**新的**输出目录内运行 zero/one/two 三次及 `summarize_protection_ablation.py`。正式三次使用 runner 的既定轮转顺序，不从单次数据抽取或丢弃样本。

## 失败与解释规则

- 若模型/引擎无法以该容量启动，或任一 cell 有服务错误、资源残留、token 合约缺失，保留目录和日志；停止该容量点，不修改负载或策略来重跑。
- 若 H2D 为零，或非零预算没有 protection commit/receipt，记为该容量下缺少回载/保护覆盖的边界结果。它不是性能通过，也不放进要求 H2D 与保护覆盖的聚合；可在后续单独设计负载，但不能把该调整后的负载与本扫描混合。
- 若单次可行性通过而三次正式结果退化、方差大或无法复现开发集改善，照实报告。n=3 仍不用于显著性结论。
- 关闭日志继续采用 [关闭日志门槛](SHUTDOWN_LOG_GATE.md)：仅完整请求、drain、主动终止之后的精确八行 vLLM output-handler 竞态可单列，其他 ERROR/traceback 均使 cell 失败。

对照 2 GiB 的已有独立输入结果见 [保护预算独立输入复核](PROTECTION_ABLATION_VALIDATION.md)。长 prefill、稳定 decode、集中恢复、立即卸载、模型/硬件扩展都不在本次容量扫描内。
