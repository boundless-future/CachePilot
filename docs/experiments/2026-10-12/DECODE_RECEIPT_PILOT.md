# decode192 新立即卸载候选：四配置 pilot 完成

2026-10-12 14:40 前，`generation-decode192-receipt-pilot-20261012b` 四格通过，272 个请求、52,224 个输出 tokens，HTTP 错误为零。服务器报告、本地当前收集器及冻结源收集器的独立复算完全一致。它是 formal 准入检查，尚未完成三次重复，不能给出稳定收益结论。

## 冻结配置与结果

预注册见 [DECODE_RECEIPT_PREREGISTRATION.md](DECODE_RECEIPT_PREREGISTRATION.md)。模型 Qwen3-4B BF16、4090 24GB、2 GiB GPU KV；trace 保持原输入和到达，仅每请求输出预算为192。所有配置零保护；立即卸载使用独立 ImmediateReceiptConnector，lazy 使用 GenerationLedgerConnector。二者与旧 connector 的数据不得混合。

| 配置 | 完整请求 | TTFT P95（秒，仅单次观测） | 对 native 输出 hash 不同 |
|---|---:|---:|---:|
| native | 68 | 48.185 | — |
| immediate receipt | 68 | 50.664 | 9/68 |
| default h2.5/d64 | 68 | 51.748 | 6/68 |
| selected h5.0/d1 | 68 | 51.765 | 6/68 |

三套缓存配置均有真实 H2D，退出前后 exact session/job/transfer 为空；两套 lazy 的逐代账本均闭合，保护提交为零。四格均通过原 token/资源/服务日志门槛，没有被单列的 shutdown traceback。关闭热路径日志，pilot 不含逐 reservation 或逐 BlockPool 诊断，详细身份与物理资源证明来自前置独立诊断，不能混写成本轮直接测量。

该单次结果中 native 最低，与长 prefill 的正式观察不同。暂不根据这一轮改参数、换负载或选择赢家。固定 token/seed 不保证跨配置输出相同，差异完整保留；也不能仅凭 max_tokens=192 认定存在稳定 decode 并发平台。

## 来源及复查

- 源包 `generation-matrix-evidence/generation-decode-receipt-source-20261012.tar.gz`，SHA256 `45fc064635651cbf1be435e66cea14c97beed6f9a8fa33e856e11b3967848e94`；205 项源字节逐项核验通过。
- 原始目录 `generation-matrix-evidence/generation-decode192-receipt-pilot-20261012b/`：请求/SSE、metrics、启动命令、配置、资源快照、原日志、generation summary。
- 54份服务器原始文件与本地副本逐字节SHA/长度一致（5,228,256 bytes），见 `decode-receipt-server-file-inventory.json` 与 `decode-receipt-archive-byte-verification.json`。
- 本地复算 `decode-receipt-pilot-local-independent.json`、冻结源复算 `decode-receipt-pilot-frozen-independent.json` 与服务器 `independent-summary.json` 完全相同。
- 原立即卸载 session 残留失败、只读诊断失败、首次新矩阵 prerequisite 路径笔误均保留；没有用本轮成功覆盖。

```powershell
python scripts/summarize_generation_matrix.py docs/experiments/2026-10-12/generation-matrix-evidence/generation-decode192-receipt-pilot-20261012b --output audit.json
python scripts/verify_source_snapshot.py docs/experiments/2026-10-12/generation-matrix-evidence/generation-decode192-receipt-pilot-20261012b/plan.json EXTRACTED_SOURCE_ROOT --output source-audit.json
```

## 下个工作窗口

优先在同一冻结源码与依赖、同一 trace 下运行 formal 十二格（每配置三次，沿用预注册轮换顺序）。服务器当前源码保留为205项 pilot 版本，后续 CPU 结构工具未覆盖它；formal 前重新核对 manifest。若更新 scripts/configs，包括新增文件，都会改变严格源签名，不能跳过同签名 pilot 门槛。需要使用独立冻结工作副本，或明确新签名后重新完成 pilot，不可忽略差异。

接着进行真实匿名结构六会话负载的独立 GPU 试点，先审计实际压力、资源及完整 H2D，再决定是否正式重复。此次15:00截止前只完成 pilot、审计和后续 CPU 准备，不启动预计不能完整结束的 formal。
