# 下一步：容量门槛的可观测诊断

本设计基于 [新文本零覆盖反例](GENERATION_STRATEGY_VALIDATION.md)。实现进展：独立诊断Connector及prepare副本已完成；去掉观测语句后与原决策AST一致，30项真实manager/BlockPool与诊断回归通过。两格GPU诊断已完成；最终结果见 [报告](GENERATION_CAPACITY_DIAGNOSTIC.md)，下一步转向共享前缀引用安全契约。目的为解释为何开发输入有保护提交而历史新文本没有，不以改变参数获得通过为目标。

1. 先增加独立、opt-in的只读诊断入口。复用冻结的开发和新文本输入，策略固定selected-one h5/d1、16 blocks；模型、资源、到达及reserve算法不变。诊断与性能归档隔离，不汇入TTFT比较。
2. 在scheduler owner的原allocation边界记录当前free、watermark、逐live请求的token/computed footprint、计算出的reserve、剩余预算，以及候选wire/generation、STORE范围、front完整chunk、引用状态和是否被普通manager移走。核对观测计算与原reserve一致；记录该边界候选所有检查阶段，区分容量拒绝、无候选、在途限制、哈希/引用失败和普通STORE先行，不能只打开已去重的capacity日志就称完整时间线。
3. 每个候选统计首次/末次可见步、出现步数、最小/最大free−reserve−16、原STORE或保护提交与回执终态。记录实际已使用的数值，不在日志线程再次调用改变cache状态的lookup、pin或manager step。候选终态关联保留generation与原range lineage。
4. 先CPU测试观测不改变原返回/顺序/异常、reserve复算、去重与关闭快照，再各跑一个开发/新文本诊断。两格必须通过原请求/资源/generation/回执门槛；新文本可以没有保护提交，但这仅表示**诊断完成**，不把原性能coverage失败变为成功。任意资源/身份/未知异常停止并保留证据。
5. 若带观测诊断无法复现零覆盖，报告观测扰动，不追跑直到得到期望结果。若重现，报告各候选的容量裕量与存活窗口分布；先判断真实容量不足或保留估计过松紧，再讨论独立安全证明与reserve候选。没有证明前不降低安全保留，也不改变模型/卡掩盖局限。

验收交付为两份原始诊断、离线独立时间线复算、明确可证与不可证的原因、原零覆盖反例继续有效。修改reserve、扩大异步/多worker支持及正式性能重测是后续单独工作，不包含在这轮诊断范围。
