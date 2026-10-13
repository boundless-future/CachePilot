# 全请求包络 CPU 候选

本轮新增独立 `WholeStepAdmission` 和 `WholeStepProtection`，默认配置不启用，服务 bind 明确拒绝。不是新GPU服务或性能验收，也没有改变原匿名结构3/4失败。

## 决策

每个live请求的F为 `ceil((max(num_tokens_with_spec, num_computed_tokens)+1)/16)`。
候选reserve为所有live请求F之和，加实际Scheduler `_inflight_prefills` 成员的F之和，再加watermark和16块margin。
旧策略仍为两倍所有live请求F加watermark/16，未改动。

不扣驻留块、不扣local/external命中、不按计算token估计物理分配；已有remote prefill再加一份完整footprint，以覆盖其剩余预留。新准入的remote请求在本步先消耗其部分footprint，剩余预留理论上对应尚未消耗部分；这一解释需要更多实际序列验证，尚不是全路径上界证明。

无live、零token预算、缺remote成员信息、MM/LoRA、在途计算或spec token均返回未知。重复/非live的remote成员直接拒绝。固定16块与同步TP1 full-attention约束继承当前研究范围。纯包络函数不负责检查全部服务配置，所以独立保护类禁止服务绑定；不能通过替换生产helper的admission_policy绕过门槛。

候选仍全额计入16/32保护引用预算，保持原完整前缀证明、generation票据、prefix/suffix拆分、原批次回执及poison语义。只改变reserve计算和由reserve决定的压力排名范围，并不声称与旧策略候选集相同。

## 验证

新增23项：10项纯包络、6项真实manager/generation保护交接、7项真实Scheduler序列。

- 旧reserve114加16保护超过128可用块而拒绝的fixture，新reserve65允许16块；成功/失败原回执均闭合。这是构造的CPU场景，不是原pilot反例的修复证明。
- 零计算commit回滚保持pending票据和free排名；native注册后异常保留16引用并禁止错误rollback；成员不一致在pin前失败。
- 实际Scheduler冷prefill/外部回载分别在额外0/16/32块占用下完成两个请求准入；外部回载仍为0计算token且保留原生reservation。另已有remote未完成时新到达，32额外引用下两请求均进入remote等待，原预留2块，最终96可用块恢复。
- Scheduler使用真实Qwen3-4B配置及native元数据，外部命中回调为fixture，不运行模型worker；额外占用是池引用，不代表真实保护STORE或DMA。

独立服务器 `/root/cachepilot-whole-step-20261013`，原工作目录不覆盖。
首轮定向31 passed；补连续remote后7 passed/11 deselected；最终强制native/CUDA **1200 passed、零跳过、115 subtests、121 warnings**。Windows **688 passed、313 skipped、26 subtests**，缺库跳过不算上游验收。日志在 `docs/validation/whole-step-2026-10-13/`。

## 下一门槛

优先补抢占释放后重入、混合共享命中和owner离开、带watermark的多请求连续准入，核对每次真实reservation/full-fit及请求状态；验证多次失败尝试是否能打破包络。当前小矩阵不是整个调度器证明。若反例存在，保留并修正候选范围，不能临时调系数筛通过。

完成上述边界后才独立Connector接线及配置/source gate，随后GPU逐批KV/资源/代际准入，再冻结新pilot。保护跨步在途也需要覆盖；当前包络不保证相对无保护长期调度完全相同。原pilot/formal历史、无稳定收益结论保持不变。

后续已完成18项共享/watermark精确阈值与抢占恢复边界，见 [边界报告](WHOLE_STEP_BOUNDARIES.md)。服务接线仍关闭，下一步远端结果连续恢复及跨步保护验证。
