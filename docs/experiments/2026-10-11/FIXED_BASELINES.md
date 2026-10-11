# 固定参数调优与新文本验证

2026-10-11。预注册见 [FIXED_BASELINE_PREREGISTRATION](FIXED_BASELINE_PREREGISTRATION.md)。开发 formal 完成，新文本 validation 在第三格因无保护覆盖停止；矩阵未完成。

## 结果范围

RTX 4090 24GB、Qwen3-4B/BF16、vLLM 0.30.0、锁定 LMCache、同步 eager；GPU KV 2 GiB、CPU KV 16 GiB、15 秒 idle grace，所有组使用同一 OwnedService/IdleFinalize 数据通路。68 请求/格，每请求 48 decode token。六个零保护固定基线与 one/two 同期轮转，各三次；pilot 八格只做准入，不纳入正式均值。

正式 24 格、1,632 请求全部通过输入/源码签名、token、实际 H2D、原回执与资源门槛。原始 SSE 时间戳独立重算 24/24 通过。选择只使用六个固定基线的三次 TTFT P95 均值；选中 **h5.0/d1**，不改变保护候选的 h2.5/d64，也不将这次选择写成生产默认值。

## 开发集全部点

TTFT P95 单位 ms；变化为相对选中固定基线的均值，负数表示更低。SD 为三次重复的样本标准差，并非置信区间。

| 配置 | r0 | r1 | r2 | 均值 | SD | 相对 h5.0/d1 |
|---|---:|---:|---:|---:|---:|---:|
| h1.25/d1 | 7381.74 | 7312.07 | 7477.45 | 7390.42 | 83.03 | +3.80% |
| h1.25/d64 | 7537.29 | 7243.31 | 7450.47 | 7410.36 | 151.04 | +4.08% |
| h2.5/d1 | 7476.45 | 7021.14 | 7037.96 | 7178.52 | 258.15 | +0.83% |
| h2.5/d64（默认） | 7607.47 | 6876.73 | 7152.19 | 7212.13 | 369.04 | +1.30% |
| h5.0/d1（选中） | 7182.29 | 7169.76 | 7007.02 | 7119.69 | 97.77 | 0 |
| h5.0/d64 | 7236.31 | 7253.76 | 7188.43 | 7226.17 | 33.83 | +1.50% |
| protect-one | 7161.04 | 7522.28 | 6864.31 | 7182.54 | 329.51 | +0.88% |
| protect-two | 7431.86 | 7085.70 | 6800.90 | 7106.15 | 315.97 | -0.19% |

调优固定基线相对默认均值低 1.28%。保护候选与选中固定基线的差异很小，且重复间方向不一致；尚未证明保护优于合理调参。h2.5/d1、h2.5/d64、h5.0/d1、h5.0/d64、one、two 的实际 prefill 均为 24,156 tokens/格；因此不能把这些配置间的 TTFT 波动直接归因为少算了 prefill。

## 成本与退化

| 配置 | ready-to-first P95 ms | 会话跨度 P95 ms | prefill tokens | H2D bytes 均值 | D2H bytes 均值 |
|---|---:|---:|---:|---:|---:|
| h1.25/d1 | 7391.40 | 31523.62 | 26716 | 981467136 | 2025848832 |
| h1.25/d64 | 7410.71 | 31410.10 | 26204 | 1509949440 | 2415919104 |
| h2.5/d1 | 7179.45 | 31485.40 | 24156 | 1610612736 | 2629828608 |
| h2.5/d64 | 7212.99 | 31411.34 | 24156 | 1811939328 | 2868903936 |
| h5.0/d1 | 7120.27 | 31343.31 | 24156 | 1811939328 | 2856321024 |
| h5.0/d64 | 7226.84 | 31435.35 | 24156 | 1811939328 | 2566914048 |
| protect-one | 7183.20 | 31294.70 | 24156 | 1811939328 | 2667577344 |
| protect-two | 7107.16 | 31425.59 | 24156 | 1811939328 | 2390753280 |

只报告这套冻结 trace、版本和资源预算下的观察。不同日期或容量矩阵不并入本次均值。三次重复不代表统计显著性或真实 Agent 泛化；会话跨度包含固定计划等待，不能直接等同于纯服务执行时间。

## 日志、正确性与证据

`h5p0-d1-r0`、`h5p0-d64-r2` 各保留 8 行精确分类的已知退出 output-handler traceback；运行期无 fatal error。这些日志未删除。与同期默认组相比，输出 hash 每格存在 0–7/68 差异；token 数固定不意味着 decode 完全确定，性能轮也不含逐层 KV probe。

- 原始归档：[formal](fixed-baseline-evidence/fixed-baseline-formal-20261011a.tar.gz)，SHA256 `ba9e66d1a8097e77d5b3f9dc647ae5563202e732bc5028acceb7d99f8a956e52`，远程与本地一致。
- [独立资源/配置审计](fixed-baseline-evidence/fixed-baseline-formal-independent.json)、[时间戳/token/hash 复算](fixed-baseline-evidence/fixed-baseline-formal-timing-audit.json)、[固定选择](fixed-baseline-evidence/fixed-baseline-selection.json)。
- 预先生成的新文本 validation 原计划共 12 格（默认、选中、one、two 各三次），使用相同源码与资源签名；不得在此重新调参。

## 新文本 validation 的覆盖失败

前两格默认/选中 fixed 通过；第三格 `protect-one-r0` 的 68 请求、3,264 输出 tokens、H2D=1,811,939,328 B 及全部资源回收通过，运行期和退出无错误，但 8 次保护候选均因物理容量回退，保护提交/回执为 0/0。原规则要求非零预算有真实保护覆盖，因此 `passed=false`，后续 9 格未启动。没有重跑筛选、额外压力或改门槛；不能报告验证整体均值或声称调优/保护泛化成功。

这是策略触发范围的反例，不是请求失败或已观察到的 KV 损坏。保护预留约束拒绝了候选，普通 STORE 仍有 42 次提交/完成；独立原始时间戳复算三个已有 cell 都通过。新文本虽然到达结构相同，其总 prompt tokens 比开发集多 1,156，且输入 token 边界不同；目前只能说原保护机制没有在此运行获得覆盖，不能直接将差异全部归因于文本长度。

原矩阵审计为 `passed_cells=2/12`、`aggregates={}`；第三格没有生成最终 fixed-validation，因为原 runner 在覆盖断言处终止。单格补充审计单独保留，不伪造该文件。

- [部分矩阵原始归档](fixed-baseline-evidence/fixed-baseline-validation-20261011a-partial.tar.gz)，SHA256 `554e4e371737625f2f78d7739f77864de0a55ee37193135ca82100238ba010be`。
- [部分矩阵审计](fixed-baseline-evidence/fixed-baseline-validation-partial-independent.json)、[原始测量复算](fixed-baseline-evidence/fixed-baseline-validation-partial-timing.json)、[无保护单格审计](fixed-baseline-evidence/fixed-baseline-no-protection-audit.json)。

原 reference 六组方案以 validation 完整通过为前置，当前不具备准入。后续可独立预注册原生/立即卸载/固定基线的对照，回答不依赖保护覆盖的问题；必须与本失败矩阵分开，不能由此补写原验证通过。
