# 共享前缀候选：历史输入 pilot

2026-10-13，用户授权“推进下一阶段”。本轮四格全部通过预注册覆盖、计时和资源门槛，允许同源码正式三重复；pilot 本身不作为稳定收益结论。

## 配置与结果

使用 [预注册](SHARED_STRATEGY_PREREGISTRATION.md) 的原68请求历史合成输入，75,808 prompt tokens/格、48输出tokens/请求；没有增加诊断尾段。Qwen3-4B BF16、4090 24GB、GPU KV2GiB、CPU KV16GiB、同步eager/TP1，idle15秒。所有组共用SharedPrefixConnector/generation，zero绕过保护。

| 配置 | horizon/drain | 保护预算blocks | 提交/回执 | TTFT P95 ms | D2H bytes |
|---|---|---:|---|---:|---:|
| default-zero | 2.5/64 | 0 | 0/0 | 9268.82 | 2,868,903,936 |
| selected-zero | 5/1 | 0 | 0/0 | 9112.74 | 2,717,908,992 |
| shared-one | 5/1 | 16 | 4/4 | 9154.03 | 2,755,657,728 |
| shared-two | 5/1 | 32 | 3/3 | 9093.16 | 2,717,908,992 |

共272请求/13,056输出tokens。四格各69代END闭合，无pending/inflight/pin及服务session/job/transfer残留；非零预算峰值16/32。各格prefill24,240、H2D1,811,939,328 bytes完全相同。capacity fallback32/33、inflight-budget fallback2/3分别属于one/two，不能将fallback数量直接换算成收益或损失。

这次证明修正共享前缀准入后原历史反例输入可以实际触发保护；未改reserve/输入争取通过。它不证明被保护的每一批随后都回载，也没有独立“先共享再保护”逐步探针；该正确性范围引用上一阶段独立诊断。

## 输出与日志限制

相对selected-zero，default-zero与shared-two的event50/51输出hash各有两项不同；shared-one全部相同。两项输出的hash在event50/51间互换，这只是观察到的模式，尚不能归因为调度、数值或缓存机制。不将HTTP/长度通过视为逐条输出一致。

四格无未分类fatal，独立审计的shutdown teardown error列表为空；原日志中的退出CUDA IPC共享张量警告仍完整保留。原历史失败pilot与同步探针零覆盖反例没有替换。

入口第一次pytest由于远端缺少选择性同步的历史测试文件而失败，未启动任何服务。补齐文件及旧zero原始fixture后远端27项通过；本地补充“伪造result.passed不能掩盖零保护”回归后37项通过。两组测试范围不同，不合并为同一次全套结果。

## 复现与证据

- [原始包](evidence/shared-strategy-pilot-evidence.tar.gz)：70文件，4,316,528原始bytes；包SHA256 `84de4a291ea66d8d566a37d8636ee4376f25226fe60e62babfc0825c923d3b87`。
- [完整审计](evidence/shared-strategy-pilot-audit.json)、[字节/三处复算](evidence/shared-strategy-pilot-integrity.json)：服务器、本地、冻结源报告逐字段相等，240项冻结源逐字节核对。
- 包内保留初次入口失败/远端27测试日志、原始输入/计时/输出/配置/日志与各检查，以及服务启动前生成的frozen-source.tar.gz。
- 离线复核：`python docs/experiments/2026-10-13/verify_shared_strategy_evidence.py <archive> <new-output-directory>`。

正式实验沿用相同240项源码、参数和输入，四组×三次按预注册轮转；任何格失败即停止、保留未运行格，不将pilot并入formal。
