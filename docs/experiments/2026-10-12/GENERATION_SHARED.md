# 新组合共享取消与共享受害请求抢占

2026-10-12，两个独立GPU正确性专项完成。新增诊断Connector组合已有SharedCancel/ProtectedPreemption探针与GenerationProtectionConnector；runner新增互斥shared-cancel/shared-preemption入口。复用原驱动，不修改生产策略或scheduler抢占决策。固定Qwen3-4B/BF16、4090 24GB、同步eager TP1、2GiB KV、two预算；两格同218项源码签名，结束后逐项核对并tar归档。

## 共享取消

347个完整回放请求，另1个共享前缀流主动取消，合计348个generation精确闭合。暂缓真实STORE回执，32个共享block在新请求存活/取消后/原回执后引用依次2→1→0；取消只释放新请求引用，原manager仍持有保护pin。目标两个chunk均有严格STORE→native terminal→H2D来源。

936层回载KV全部bitwise相等；6次保护STORE、13个总STORE批次，峰值32blocks、最终pins0，30个原reservation/26次native transfer闭合，BlockPool恢复909。

日志限制：正常请求处理完成、EngineCore进入teardown、MPClient complete之后，API output_handler记录一次EngineDeadError traceback，另有CUDA IPC producer提前终止警告。原日志完整保留；不宣称无错误，也不将通过的运行期资源审计扩张为所有退出路径无瑕疵。没有重跑挑选干净日志。

## 共享前缀受害请求实际抢占

347个完整回放请求，另4条3500-token压力流，观察目标抢占后主动取消这些压力流；351个generation精确闭合。32个受保护blocks与其中一条压力流共享，真实vLLM决策使其RUNNING→PREEMPTED，num_preemptions增加1、computed_tokens重置为0。引用2→1→0；受保护期间39次分配未侵占目标block，hash与原manager持有状态保持。

504层实际回载KV全部bitwise相等，目标两个chunk有严格来源；5次保护STORE、17个总STORE批次，18个原reservation/24次native transfer闭合，最终pins0、BlockPool909。该格vLLM日志未检出ERROR/Traceback。

这证明受控压力下实际抢占与保护可共存，不代表任意到达分布或反复抢占恢复完整覆盖。压力请求在抢占后取消，不能描述为全部完成生成或验证受害请求完整续跑。两格均人为延后回执交付，未延迟实际DMA，不是性能实验。

## 独立验证与下一步

本地联合generation/normal/shared审计通过；对目标全部chunk回载来源、引用变化、精确END分别检查。新增归档篡改测试覆盖提前释放、遗漏END、伪造PREEMPTED状态；与既有共享审计合计34 passed。本轮未修改生产Connector，未重跑879项全套服务测试。

49原始文件9,287,612bytes与服务器SHA一致，证据、独立复算及218源码tar位于 `generation-shared-evidence/`。两格结束前后session/job/transfer集合为空，回归后GPU0%/1MiB，服务已停止。

下一步进行限定正确性准入汇总：列明已通过正常/长prefix/失败回执/共享取消抢占与host fault范围，以及未覆盖的长suffix失败、其他故障点、真实driver/DMA、多worker/异步等。之后预注册新签名策略pilot；先要求实际保护覆盖和资源门槛，不能直接formal或宣称保护优于调优zero。退出traceback继续作为开放支线，性能阶段严格按预先定义日志门槛处理。
