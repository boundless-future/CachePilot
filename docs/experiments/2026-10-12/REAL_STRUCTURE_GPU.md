# 六会话匿名结构：GPU观测准入通过

2026-10-12 16:39完成全部四格，240请求/11,520输出tokens；原60请求、209,661 prompt tokens每格不变，未追加压力或padding。实际资源、generation、token、H2D、源码及采样门槛通过，原日志无运行期错误或关闭traceback。服务器210项源码已冻结归档，服务器与本地逐格独立审计一致。

来源和变换见 [CPU方案](REAL_STRUCTURE_PLAN.md) 与 [GPU预注册](REAL_STRUCTURE_GPU_PREREGISTRATION.md)：固定上游commit编号21–100的全部六条eligible session，token/8、会话内部到达/10、首到达对齐、48输出、open replay。它是匿名前缀身份结构，不含原文本、真实tool或生成反馈，不能称为live Agent任务质量评估。

## 实际观察

每格在回放期间0.5秒一次metrics采样，各173次、0错误。采样峰值仅为下界，非逐block allocator证据；抢占来自前后counter。

| 配置 | KV采样峰值 | running峰值 | waiting峰值 | 抢占 | 实际prefill | GPU hit | external hit |
|---|---:|---:|---:|---:|---:|---:|---:|
| native | 95.27% | 4 | 5 | 0 | 42,893 | 166,768 | 0 |
| immediate receipt | 94.94% | 4 | 6 | 0 | 27,757 | 165,344 | 16,560 |
| ledger default | 99.67% | 4 | 6 | 2 | 36,477 | 159,744 | 13,440 |
| ledger selected | 94.94% | 4 | 6 | 2 | 36,477 | 159,744 | 13,440 |

此结构确实出现高占用、等待、CPU回载，lazy两组发生自然抢占；不能再仅称为CPU潜在复用机会。selected采样峰值低于default但同有2次抢占，说明0.5秒采样无法捕获所有瞬时容量情况，不能据此否认抢占。immediate保持0抢占，未越过候选的限制。

缓存三组H2D/D2H分别2.391/3.691、1.863/3.199、1.863/3.375 GiB。default/selected的idle drop分别25/16，逐代和exact server sets终结闭合。零保护提交、pin峰值均为0；这不是非零保护触发实验，也不是保护策略收益证明。

单次观测TTFT P95分别1.989/1.940/2.226/2.170秒，只作工作负载描述，不选择赢家。观测API有额外成本，结果不计入正式性能。相对native输出hash差异为7/5/5（每组60），全部保留，未证明语义等价。

## 证据与后续

`real-structure-evidence/real-structure-pilot-20261012a/` 含全部六原session/catalogue/built trace、请求/SSE、原metrics采样、配置/源码manifest、服务资源及日志。`real-structure-observed-inventory.json` 给出原始字节；本地独立审计 `real-structure-observed-local-audit.json` 与服务器逐行结果相同。新收集器增加无采样阶段scope描述，因此不要求顶层说明文案逐字相同，来源、门槛及所有rows相同。

`real-structure-observed-source-20261012.tar.gz` 保留本次210源；不能用后续无采样runner覆盖历史源码。离线入口 `audit_real_structure_pilot.py` 会从原采样复算并检查来源和资源，无部分性能均值。

16:40进入 [关闭额外采样的pilot](REAL_STRUCTURE_PERFORMANCE_PREREGISTRATION.md)：原运行栈保持不变，实验入口新增阶段选择并重新冻结。先四格完整准入，正式三次重复留到下一足够长的窗口，不混入本次观测样本。
