# 匿名结构：原生逐次准入诊断

2026-10-13。独立 one/two 两格各 35/35 检查通过，逐调用需求、新分配、空闲命中 touch、请求身份和调度步时间区间闭合。原无探针 pilot 仍为 **3/4**，formal 未运行。观测会增加开销，本报告不提供性能结论，也不替代原零覆盖失败。

## 观察结果

| 指标 | one | two |
|---|---:|---:|
| allocate_slots 调用 | 2,992 | 2,987 |
| 成功 / 拒绝 | 2,901 / 91 | 2,902 / 85 |
| 成功新分配块（重复分配累计） | 3,219 | 3,324 |
| GPU 命中块从空闲队列取走（累计） | 7,933 | 7,376 |
| 零计算 token 却新分配的调用 | 7 | 5 |
| 外部回载调用 | 93 | 86 |
| 含原生 in-flight reservation 的调用 | 1 | 1 |
| 完整序列门槛拒绝 | 90 | 85 |
| 最终容量门槛拒绝 | 1 | 0 |
| 同一步既有成功又有拒绝 | 91 | 85 |
| 保护提交 / 原回执 | 1 / 1 | 1 / 1 |

每格覆盖 61 个实际 raw 请求身份（含 warmup）；补充审计将每个调用关联到 generation 记录，并校验精确调度步时间区间、串行顺序和原生查询字段。两格资源与代际原审计通过。累计 touch 可以远大于物理池 909 块，因为同一块可反复被使用和释放，不能当成同时占用量。

拒绝中的外部回载分别 86/81 次；这些分类与完整序列门槛重叠，不能相加。成功新分配和零引用 touch 是不同的空闲队列消费者，单看 get_new_blocks 会漏掉后者。逐调用账本可以解释该次原生检查，但不能预测随后请求、未来回执或下一调度步的需求。

## 实现与证据

保持原 248 项源不变，新增 observer、connector、runner、auditor 四项，启动前冻结 252 项源。未更改原策略、reserve、输入、预算、模型或到达。原生方法仍调用一次，记录 full-sequence 检查与最终需求检查，保持原返回值/异常。

- [完整包](evidence/shared-structure-native-evidence.tar.gz)：51 文件、194,816,024 原始 bytes；SHA256 `7f7a6a754cf4a8b3902cb7dbb40c7738ec1a0c1b6a6f425620393a80ed3b94b6`。
- [诊断摘要](evidence/shared-structure-native-summary.json)、[计数分析](evidence/shared-structure-native-analysis.json)、[补充身份审计](evidence/shared-structure-native-identity.json)、[字节与三处复算](evidence/shared-structure-native-integrity.json)。服务器、本地、冻结源报告相同；身份审计为事后补充，不冒充启动前代码。
- 复算：`python docs/experiments/2026-10-13/verify_shared_structure_native.py <archive> <new-directory>`。分析入口为同目录 `analyze_native_admission.py` 与 `audit_native_call_identity.py`。
- 启动前远端 observer 4、capacity 8、audit 6，共 18 项通过；本地两类 audit 12 项通过。后续真实 manager/scheduler 边界测试单独记录，未重跑历史完整 961 项 CUDA/native 基线。

## 从观察到下一版策略还差什么

真实 manager 多调用契约 4 项、原生 Scheduler reservation 契约 1 项、真实 `Scheduler.schedule` 序列契约 6 项均已通过。Scheduler 测试不加载权重、不执行 GPU kernel；外部命中回调是受控夹具，不是 LMCache 服务验收。

在 32 可用块元数据池中，冷输入分别需要 2/3 块：剩 5 块时两个请求都能进入；额外占 1 块后仅第一个进入。外部回载在同一步 0 计算 token 的条件下也出现后续准入受阻，并保留原生 prefill reservation。共享前缀情形则不同：第二个请求复用本步已登记的 32 token，只需再计算 16 token，两种引用条件下均能进入。

首次 Scheduler 测试误用相同 token 序列模拟独立冷输入，得到 2 failed/2 passed；中间版本按观察修正预期为 4 passed，但尚未分离原因。最终明确区分独立/共享输入，6 passed。a/b/c 原日志均保留；不能用中间版本的通过宣称冷输入行为已验证。manager 契约另证明释放引用恢复 free 数量不恢复已驱逐的 hash。

因此下一步是 [原生准入边界设计](NATIVE_ADMISSION_BOUNDARY.md) 中的完整调度契约，特别是 GPU/外部混合命中和生命周期交接。**不能直接拿单调用剩余空间替换整步安全 reserve，也不能以本轮 one/two 有提交为由重启原 formal。** 只有新 opt-in 候选先通过正确性门槛后，才另立固定 pilot。
