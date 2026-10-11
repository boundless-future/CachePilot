# 晚间实验复核入口

## 先读结论与范围

- [固定调优及原验证失败](FIXED_BASELINES.md)：六点开发调优完成；新文本保护验证因零覆盖停止。
- [无保护配置对照](UNPROTECTED_REFERENCES.md)：native、立即卸载、默认 fixed、选中 fixed，各三次。
- [到达时间压缩预注册](ARRIVAL_COMPRESSION_PREREGISTRATION.md)：同样四组，只改计划到达时间。
- [到达时间压缩正式报告](ARRIVAL_COMPRESSION.md)：12/12 格通过，排队上升且缓存组整体未优于 native。
- [一次零覆盖观测预注册](COVERAGE_DIAGNOSTIC_PREREGISTRATION.md)：诊断不能补写原性能通过。
- [零覆盖诊断报告](COVERAGE_DIAGNOSTIC.md)：预算充足，但保守 reserve 导致回退；原 performance gate 仍为 false。
- [长 prefill 失败报告](LONG_PREFILL.md)：第三格身份预检 fatal，停止于 2/4，没有正式均值。
- [晚间机器可读索引](EVENING_INDEX.json)：各矩阵状态、原始归档 SHA256 和审计入口分别保存，未把不同矩阵合并。

所有命令从仓库根目录运行。离线复核不需要 GPU；加载审计器需要 Python 3.12、pytest（测试时）、requests、aiohttp。图表额外需要 matplotlib。GPU 重跑必须使用 [固定实验环境](../../ENVIRONMENT.md) 中实际锁定的软件与模型，不能直接替换 vLLM/LMCache 版本后声称复现。

## 不使用 GPU 复算原始证据

将归档解压到新的 `artifacts/` 子目录，不覆盖已有运行目录，例如 Linux：

```bash
mkdir -p artifacts/review
tar -xzf docs/experiments/2026-10-11/unprotected-reference-evidence/unprotected-references-formal-20261011a.tar.gz -C artifacts/review
python scripts/summarize_reference_matrix.py artifacts/review/unprotected-references-formal-20261011a --output artifacts/review/reference-independent.json
python scripts/audit_replay_measurements.py artifacts/review/unprotected-references-formal-20261011a --output artifacts/review/reference-timing.json
python scripts/summarize_workload_phases.py artifacts/review/unprotected-references-formal-20261011a --output artifacts/review/reference-phases.json --plot artifacts/review/reference-phases.png
```

应得到完整矩阵 12/12 和时间戳复算 12/12。图表是事后阶段描述，不能据此重新挑选最佳配置。检查归档 SHA256 与报告一致；独立审计会重新读取请求、trace、配置和日志，不仅相信 `passed=true`。

```bash
python -m pytest tests/test_reference_baselines.py tests/test_reference_matrix.py tests/test_replay_measurement_audit.py tests/test_arrival_matrix.py tests/test_coverage_diagnostic.py -q
```

这只是新增 runner/审计的 CPU 专项，不代替固定版真实 native/CUDA 生命周期回归。原生组没有 LMCache 资源；相关字段按不适用处理。

若要核对运行源码，把 `arrival-half-evidence/evening-frozen-source-20261011.tar.gz` 解压到单独源码目录，再执行 `python scripts/verify_source_snapshot.py <原始矩阵>/plan.json <源码目录> --output <校验结果.json>`。该包中的字节与 reference 173 项、arrival 174 项 manifest 匹配；不能用新版本脚本覆盖它后声称 hash 不一致是原实验问题。

后续长 prefill 扩展改变了 runner 签名，已经完成的压缩到达 pilot/formal 不能用该新签名续跑。历史包保持原样。最新 closing source 包另含 `native/reservation_lock.cpp`，配合 CPU 扩展 build manifest 和环境快照，见 [覆盖诊断证据](coverage-evidence/evening-runtime-snapshot-20261011.json)；该快照是收尾时采集，不冒充逐格环境采样。

## GPU 重跑的依赖顺序

1. 固定模型/tokenizer、4090、GPU KV 2 GiB、CPU KV 16 GiB、同步 eager、max_num_seqs=4 和全部源码。不要同时运行两个 GPU runner。
2. 固定参数 pilot→formal 产生六点选择。选择只用开发集三个 TTFT P95 均值；不得把 one/two 纳入候选。
3. 原 `scope=all` reference 还要求完整保护 validation。已有 validation 没通过，不能拿它启动该 scope。
4. 独立 `scope=unprotected` 按其预注册用给定 validation trace，pilot→formal。runner 验证原有源码未变，并冻结新增文件清单。
5. 到达压缩 runner 以完整通过独立审计的 unprotected formal 为前置，再 pilot→formal。仅压缩 `arrival_ms`；输入、请求数、token 与配置不变。
6. 诊断独立运行、独立目录。开启逐事件 JSONL 会影响时序，其延迟不得加入性能均值。

通过 `--stop-starting-at` 显式设置带时区的截止时间；每个输出目录只创建一次。失败后保留目录、源码签名和日志，分析原因后另立研究方案。源码变化后不能沿用旧 pilot 的准入签名。当前脚本具有“支持固定实验版本”的校验，尚非通用部署入口。

## 当前研究重点

现有证据同时支持末轮 CPU 回载有收益、早期和总体尾延迟可能退化。下一阶段应把负载结构、排队和搬运成本分开观察，补长 prefill/稳定 decode/真实结构压力，再决定是否值得收紧保守 reserve。不能为了触发保护删去安全预留或把 `compute_slots` 当真实分配 oracle。
