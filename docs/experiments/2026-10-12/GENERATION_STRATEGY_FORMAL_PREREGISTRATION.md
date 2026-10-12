# 新组合策略三重复正式开发比较

2026-10-12，开跑前冻结。前置为 generation-strategy-pilot-20261012b 四格独立审计通过；pilot 不计入本次统计。保留全部候选，不因 pilot 单块较快、双块较慢删选配置。

四组：default-zero h2.5/d64，selected-zero / selected-one / selected-two 均 h5/d1；预算0/16/32 blocks。三次重复，共12格/816测量请求。顺序以四组上述序列为准，r0/r1/r2 左旋0/1/2位。每格独立冷启动。n=3并非完全位置平衡，不宣称排除全部顺序影响。

模型、资源、68请求固定开发trace、48 decode tokens、同步eager、计时和停机规则与pilot相同。逐格保护实际覆盖、原回执和资源关闭、generation终态/注册END数、token、输入签名、原始SSE时间戳复算、精确错误日志分类均须通过。失败停止后续；不追加重跑筛选通过，不更改reserve/预算/负载。不完整矩阵不给总体均值。

主指标各格TTFT P95的三次均值、样本SD及全部单次值；对照selected-zero，负变化表示更低。报告prefill、H2D/D2H、保护批次、回退、输出差异及停机错误。SD不是置信区间；这是开发复测，不是留出泛化验证。若效果重复不一致或无收益，如实记录，不以进一步正确性smoke替代效果结论。

运行入口run_generation_strategy_formal.py；冻结pilot原221项源码逐字节不变，仅允许新增formal runner、formal auditor和pilot离线auditor三文件。启动前重新审计pilot；冻结新全源签名，每格检查。服务启动参数复用pilot；完整原始文件、实际命令和源包归档。测试覆盖顺序、禁止runtime变更与不完整/重复矩阵拒绝汇总。
