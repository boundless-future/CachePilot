# 六会话匿名结构：GPU准入试点预注册

2026-10-12 16:03，在任何此结构GPU结果产生前登记。decode receipt formal十二格先完成并独立通过；服务器205项decode源码保持冻结，之后才允许添加新runner和CPU来源审计文件。既有源码SHA必须逐项相同，新试点拥有新签名，不能继承decode签名。

输入沿用 `REAL_STRUCTURE_PLAN.md` 的全部六条eligible源session、60个请求及既定变换。trace SHA为 `445b05df9898232b97d2a0f7a0fc7633a42ab8a4d712943ee3f70ebb6c2acb83`，209,661 prompt tokens、48固定输出/请求、2,880输出预算，末次计划到达86.1秒。不增加padding、session复制、压力请求，不改时间比例或按性能删选session。先复核六原文件、catalogue和trace，再比对实际模型的四个tokenizer文件SHA。

环境仍为4090 24GB、Qwen3-4B BF16、TP1、同步调度、eager、2GiB物理KV、16GiB CPU L1。四格顺序固定：native、原回执后END的immediate候选、generation ledger默认h2.5/d64、generation ledger选中h5/d1。每格冷启动独立服务；零保护，不调整预算或参数，不并行跑GPU。入口 `scripts/run_real_structure_pilot.py`。

此轮是**新结构的准入与压力观测试点，不是正式性能结论**。不额外侵入native allocator；在请求回放期间每0.5秒异步拉取一次vLLM metrics，保存原始响应和相对时间，测量KV利用率、running/waiting请求峰值。该采样有额外API开销；采样峰值只是下界，不能替代逐block证据。缺失指标为unknown，不能记作0。保存前后实际prefill、GPU/external hit、preemption、请求queue/prefill/decode/inference计数及H2D/D2H、完整请求和会话完成时间。这里不使用合成trace的early/pressure/revisit划分。

门槛：请求60/60且token一致，无服务运行期错误；缓存格有真实H2D，运行期及退出后session/job/transfer/temporary均空、GPU注册释放；lazy各generation sealed、原操作terminal/dropped、END提交且无deferred；零保护无commit/pin；采样三项指标存在且无HTTP错误。immediate候选尚不支持抢占，其格出现实际preemption则拒绝准入，即使请求和资源碰巧通过也不放宽。任一格失败立即保存失败、已跑及未跑清单并停止；不能以补压力重跑改写此次失败。

没有抢占或没有高占用时如实记录，不能声称“真实Agent压力”。缺H2D时记录复用只在GPU或没有覆盖回载，不降低门槛凑齐四格。指标缺失时先保存本轮，再单独解决观测兼容性。下一步如出现新生命周期路径或非零保护，再先做独立原身份/bitwise诊断；本轮输出hash差异保留，贪心decode不保证bitwise文本一致。

四格全部准入后才能设计关闭额外采样的三次正式重复，并注册固定顺序、签名和停止条件。只允许此前完整门槛后进入formal，不能把此轮单次TTFT当作参数选择或泛化收益。用户17:00截止，16:55停止启动新格；本窗口不足则保留准备成果，安全收尾，不省略门槛。
