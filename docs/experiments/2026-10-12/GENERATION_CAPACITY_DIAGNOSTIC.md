# 零覆盖诊断：共享首块引用阻止前缀保护

2026-10-12。开发/新文本两个GPU诊断完成，各68请求，共136测量请求/6,528输出tokens。开发仍提交4批保护，新文本仍0批。两格资源、原回执、generation、token和原始计时门槛通过，服务器/本地/冻结源独立报告一致。**新文本零覆盖不是始终容量不足：容量满足后，共享首个物理块的引用检查仍阻止保护。** 原新文本性能pilot继续判失败，本诊断不构成性能收益。

## 实现与隔离

保持h5/d1、one chunk（16 blocks）、原reserve、模型/输入/到达/资源不变。新增独立CapacityDiagnosticConnector、DiagnosticBoundedProtection和runner/auditor四项，原226项源逐字节不变，新快照230项。诊断prepare是原方法的显式副本；测试删除诊断语句后，两方法的决策AST与原prepare/capacity fallback相同，防止观察时顺带修改策略。

owner线程只读记录live token/computed、free/watermark/reserve、pending wire/range/block状态、排序/预算/引用/hash/proof检查阶段；调用原metadata/回执记录，关闭额外owned逐步日志。记录会产生I/O开销，不能声称完全无扰动，所有耗时不进入正式比较。无新bitwise或完整物理BlockPool快照探针。

启动前真实manager/BlockPool及诊断30项通过；追加generation prefix/suffix成功/失败回执四项后34项通过，66条依赖弃用警告。另本地5项审计/结构检查通过，覆盖真实反例、修改reserve及删除调度边界的拒绝。

## 观测结果

| 指标 | 开发 | 新文本 |
|---|---:|---:|
| 调度边界 | 851 | 853 |
| pending状态观测 | 3799 | 3649 |
| 请求/前缀范围预筛选候选wire | 36 | 36 |
| 上述候选观测次数 | 1412 | 1539 |
| free−reserve−16≥0观测 | 40 | 203 |
| 同时剩余预算≥16观测 | 37 | 203 |
| 进入rank检查 | 250 | 203 |
| rank有压力位置 / 无位置 | 195 / 55 | 147 / 56 |
| 前缀块引用拒绝 | 0 | 147 |
| 实际保护提交 | 4 | 0 |
| 去重capacity计数 | 32 | 35 |
| 未去重capacity事件 | 401 | 400 |

预筛选仅排除live/未finish/inflight/非front-range等，不是最终hash/proof准入。rank计数可包含已拆分suffix，因此不能直接与front候选观测总数相减；新文本没有保护拆分，203次中147次进入引用检查，另56次无压力位置。

新文本147次引用拒绝涉及6个wire，**全部为候选首块（offset 0）、物理block ID 1的ref_cnt>0**；没有null或缺hash拒绝。ref_cnt分布：1出现5次、2出现6次、3出现1次、4出现132次、5出现3次。它们不是147个独立请求或失败批次。

例如新文本step647：free776、reserve552、可用预算16，容量裕量208，但首块ref_cnt=2；原检查`any(b.ref_cnt or b.is_null or b.block_hash is None)`拒绝整个16-block受保护前缀。开发step646则free774、reserve682、裕量76，随后有保护提交。两次运行步骤不是严格配对反事实，不能据此单独归因调度速度。

## 输入共享关系与终态

实际Qwen tokenizer复算：开发68条prompt的前16 tokens有20种；新文本68条只有1种，文本为`Workload fixed-baseline-validation-20261011a`。共同namespace前缀形成跨session相同首块；诊断中被拒绝的确实也是共享的物理首块1。当前策略保护完整front chunk并要求其中每块ref_cnt为0，故即使其余块无引用、有驱逐压力，也不能保护包含共享首块的整个chunk。

这解释了本次观测到的引用分支拒绝，但没有逐个记录每个ref的owner来源，不能断言全部ref来自当前decode请求；普通STORE也可能持有引用。也没有执行“只改变共享前缀”的反事实实验，不能宣称增加17 tokens是所有差异的唯一原因。

6个受引用拒绝wire中，5个随后有default_manager批次及对应原回执；另1个没有STORE提交，其generation snapshot中pending_store为dropped且END已submitted。该快照不能进一步判断drop的具体原因。两格每格69代闭合，无poison/残留；日志没有匹配到运行fatal或已知停机traceback。诊断成功不把历史coverage失败改为成功。

## 证据与复现

- [原始36文件](capacity-diagnostic-evidence/capacity-diagnostic-20261012a/)，共47,899,039 bytes，[SHA核对](capacity-diagnostic-evidence/raw-byte-audit.json)通过；manifest不含自身。
- [本地审计](capacity-diagnostic-evidence/local-audit.json)、[冻结源审计](capacity-diagnostic-evidence/frozen-audit.json)、原始summary三份相同。
- [可复算窗口分析](capacity-diagnostic-evidence/reproducible-window-analysis.json)、[rank与批次分析](capacity-diagnostic-evidence/rank-terminal-analysis.json)、[tokenizer首块复算](capacity-diagnostic-evidence/capacity-prefix-token-audit.json)。窗口包含首次/末次步、每步裕量及wire终态；wire-level事件需结合range/batch读，不能跨范围配对。
- [230源快照](capacity-diagnostic-evidence/capacity-diagnostic-20261012a-source.tar.gz) 948,244 bytes，SHA256 `6f8ba4a380c7f0de6ddf2a2c2e0d1194d40d9dc9f455bcbf5887ac107f186ca0`；源字节审计通过。
- `python scripts/audit_capacity_diagnostic.py <raw-dir> --output <audit.json>`；`python scripts/analyze_capacity_windows.py <raw-dir> --output <windows.json>`。分析脚本实验结束后仅在本地开发，不在冻结GPU源清单中。
- [真实契约34项](capacity-diagnostic-evidence/native-tests-final.txt)、[本地5项](capacity-diagnostic-evidence/local-audit-tests.txt)。不是全套native/CUDA重跑。

## 下一步

优先设计**共享前缀引用的安全保护候选**，不先减少reserve。原检查直接放开会改变pin/ref与rollback语义，必须先证明新保护引用只增加/释放自己的份额、原引用不丢失、共享块不重新排序、失败/取消/旧代回执不误释放。先用真实BlockPool＋generation manager建立CPU契约，再独立opt-in实现及GPU bitwise/资源准入，最后回到固定输入覆盖复核。原策略和本次反例继续保留。

本轮服务已停止，端口8000/8080/5555空闲，GPU1MiB/0%，容器开机。容量估计过保守仍是可能的性能限制，但不能以本反例为理由直接降低安全门槛。
