# 实际 EngineCore 提交后主机异常与证据保存

2026-10-12。新组合在真实模型服务中注入一次保护提交后、worker handoff前的主机异常。a轮暴露缺失快照；独立修复后b通过限定故障准入。不是CUDA driver/DMA异常、自动恢复或正常资源归零证明。

## 故障位置和判定

独立 `GenerationProtectionFaultProbeConnector` 只在显式 `CACHEPILOT_GENERATION_FAULT=after_commit` 下，对首次实际保护提交注入 `CACHEPILOT_INJECTED_AFTER_PROTECTION_COMMIT`。复用固定bounded smoke输入，one预算16 blocks，Qwen3-4B/4090 24GB/同步eager TP1/2GiB KV。实际原manager已登记batch、持有16个refcount=1的block；尚未生成worker handoff。

runner在兜底清理前等待至多20秒，记录API父进程poll返回值和注入进程/proc状态，不向它们发送停止信号。门槛要求自行退出、请求失败、目标无END/无故障后rollback，以及包含原目标在途操作和16个保护blocks的失败snapshot。

## a失败及修复

a中EngineCore自行消失、API退出（returncode=0），目标未提前END或rollback；但generation.json不存在。此前仅依赖Connector.shutdown的保存逻辑没有在实际fatal路径形成证据，故整体不通过。这里API返回0不能单独证明运行正常；日志有明确注入异常和EngineDeadError。

新增 `_save_fault_snapshot()`，在 `build_connector_meta` 或 `update_connector_output` 捕获异常并poison后、向上抛出之前保存只读状态。使用临时文件替换，保存fault_phase/error、prepared/inflight block数、generation及原票据；记录失败仅追加exception note，不替代原异常，不触发free/END/重试。正常请求路径不新增写盘。

## b通过

- 恰好一次真实保护提交后注入，目标native batch仍登记，16个block引用均为1。
- 在harness任何停止信号之前，EngineCore进程已不存在、API已退出；失败快照已存在。
- snapshot的fault_phase=`build_connector_meta`、protected_inflight_blocks=16，原generation的operation仍未终结，END未提交。
- 原事件中没有目标worker_bound，没有故障后的目标rollback或END。
- 本地独立事件/进程/snapshot复算：a=false、b=true，不依赖runner保存的passed。

保留16个引用是异常时不误释放的证据；随后进程退出销毁地址空间，**不等于正常路径通过原receipt精确释放**。未验证worker已拿到metadata后的故障、真实DMA故障、取消/抢占、跨进程自动恢复或长期server状态重建。

## 验证与归档

回调专项首轮14 passed/1 error，原因新测试未导入依赖fixture `case`，修正测试import后15 passed；原失败保留。最终服务器现有测试集显式CUDA/native **879 passed、115 subtests、121 warnings，19.34s**。本地归档证据篡改/normal/generation审计另 **18 passed**；这18项不全部是新增，也未包含在服务器879中，不能相加作唯一测试数量。

`generation-protection-service-fault/`保存a/b共27个原始文件、906,696 bytes，服务器/本地SHA逐项一致；b运行的216项源码结束后核对并tar归档。a计划保留原源码SHA；a服务用的Connector版本为753acbd。独立审计和本地结果另存，见a-independent.json和b-independent.json。回归结束GPU 0%/1MiB，无新服务继续运行。

下一步：新组合长prefix拆分、共享取消/自然抢占及失败回执的独立GPU专项；metadata/receipt其他位置服务故障仍开放。限定正确性范围明确后再新签名策略pilot，已有性能结论不变。
