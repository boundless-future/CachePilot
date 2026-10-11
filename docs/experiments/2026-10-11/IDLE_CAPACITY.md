# 空闲收尾栈 2.5 GiB 容量正式复核

日期：2026-10-11。遵循 [预注册](IDLE_CAPACITY_PREREGISTRATION.md)，pilot 三格与 formal 九格分开保存。新栈的九格共 612 请求通过独立 token、源码/输入、H2D、保护回执、资源及服务日志审计；旧 2.5 GiB 资源失败保留于 [原容量扫描](CAPACITY_SCAN.md)，不改写为通过。

## 配置与结果

4090 24GB、Qwen3-4B/BF16、单卡同步 eager；GPU KV 2.5 GiB、CPU KV 16 GiB。全部使用相同 IdleFinalizeConnector、Owned MP 服务与 15 秒空闲宽限；只改变 zero/one/two 的保护预算。68-request trace 每请求固定 48 decode tokens，规范化 SHA256 为 `079c9507c9585a5e5397affc8750c42603ee7efeeca3127d1cafafc2b1c54eb5`。三次顺序按预注册轮转，未追加压力或延长 drain 门槛。

| 预算 | TTFT P95 均值 ± 样本 SD (ms) | r0 / r1 / r2 (ms) | 实际 prefill 均值 | H2D / D2H 均值 (GiB) | 会话完成 P95 均值 (ms) |
| --- | ---: | --- | ---: | ---: | ---: |
| zero | 7310.12 ± 197.41 | 7102.88 / 7331.54 / 7495.95 | 25444.00 | 1.453 / 2.250 | 31482.04 |
| one | 7085.92 ± 94.84 | 7152.96 / 7127.38 / 6977.41 | 25444.00 | 1.406 / 2.250 | 31329.76 |
| two | 7163.94 ± 183.25 | 7239.15 / 7297.63 / 6955.06 | 25278.67 | 1.523 / 2.227 | 31367.14 |

one/two 的 TTFT P95 均值相对 zero 为 **-3.07% / -2.00%**，但第一组重复都比同期 zero 慢；n=3 不支持显著性或稳定泛化结论。one 的实际 prefill 完全未变；two 均值少 0.65%。H2D 相对 zero 为 one -3.23%、two +4.84%，不能把延迟差异唯一解释为减少重算或减少搬运。

ready-to-first P95 均值分别为 7310.74 / 7086.60 / 7164.90 ms；ready-to-end 为 8436.83 / 8184.89 / 8253.76 ms。服务端平均排队为 3142.56 / 3037.97 / 3079.35 ms。保护方法 CPU 总耗时均值为 one 125.72、two 125.65 ms，wall 总耗时为 128.29 / 128.58 ms；这是整轮方法 instrumentation 总和，不是每请求关键路径或 GPU DMA 时间。

## 门槛与限制

- 每格 68/68 请求、唯一 event_id 及实际 48 completion tokens 均通过；9 格均有真实 H2D，每格 idle drop 恰好 1 次。
- one 的保护提交/原回执为 4/4、3/3、3/3；two 为 3/3、4/4、4/4。峰值 pin/inflight 分别为 16/32 block。zero 无保护提交。
- 运行期与退出后 owner/session/temporary、读写锁、pending/controller/transfer、保护在途资源均清零，退出后 GPU registration 为空。
- 运行期 fatal service error 为零。`zero-r1`、`one-r1` 各有八行既定分类的退出后 output-handler traceback；原始日志及关闭时序保留，不能称为“没有 ERROR 日志”。
- 相对同 repeat zero 的输出 hash 差异：one 为 5/7/5（每次 68 条），two 为 5/7/0。本轮未开逐层 KV probe；请求、长度及资源通过不代表所有输出完全一致。
- 这次同栈对照支持限定负载的可行性与局部性能观察；不证明 15 秒为空闲收尾的最优值，也不覆盖多 worker、异步调度、真实 driver/DMA fault 或真实 Agent 分布。

## 原始证据与复算

- [pilot 独立审计](capacity-evidence/idle-capacity-pilot-independent.json)：3/3 格、204 请求，只用于准入。
- [原始时间戳独立复算](capacity-evidence/idle-capacity-formal-timing-audit.json)：612 请求的 SSE/client 时间戳、输入/token/输出 hash、全部分位数与会话跨度复算通过；未调用原统计 helper。
- [formal 独立统计与逐格检查](capacity-evidence/idle-capacity-formal-2p5g-20261011a-analysis.json)：9/9 格，含每次指标、标准差、输出差异和完整检查项。
- [formal 原始归档](capacity-evidence/idle-capacity-formal-2p5g-20261011a.tar.gz)：服务器与本地 SHA256 一致，为 `e27f1969d119c48b2c0022e7d27823ea92cd20f1525ed7e93e7d5a84a8fd52a8`。含 trace、源码清单、launch config、请求、metrics、日志、资源状态和 runner 结果。

```bash
python scripts/summarize_policy_matrix.py artifacts/idle-capacity-formal-2p5g-20261011a --output docs/experiments/2026-10-11/capacity-evidence/idle-capacity-formal-2p5g-20261011a-analysis.json
```

原正式矩阵完成、GPU 空闲并下载复核后，才上传固定参数 runner。后续按 [固定基线预注册](FIXED_BASELINE_PREREGISTRATION.md) 执行六个 fixed 点和两个同期候选，先 pilot 再 formal，最后锁定固定参数进行新文本验证。不同源码/容量的历史数字不并入这一矩阵。
