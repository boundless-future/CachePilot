# 下一窗口：共享候选四组pilot

当前1–3限定准入已完成，见 SHARED_PREFIX_ADMISSION.md。不要重跑旧3B全套或把旧新文本失败改成通过。

## 下一主线

1. 先检查SSH、GPU/端口及运行任务。服务器 /root/CachePilot 已含新共享候选和诊断工具；历史实验必须从对应 frozen-source.tar.gz 恢复，不能拿当前目录重冒历史签名。
2. 预注册并实现新的历史新文本四组pilot：default-zero h2.5/d64、selected-zero h5/d1、shared-one h5/d1、shared-two h5/d1；GPU KV2GiB、CPU16GiB、idle15秒、Qwen3-4B、同步TP1/eager。保留原trace摘要015c2148973855fe2fa1e0cb7cef4ecb136f69ecf41fde2212b61bbb9e5acd6b及68请求/48输出，不加本轮323条诊断尾段。
3. 关闭同步KV探针、逐步物理快照/JSONL，使用一次性保护/资源/generation摘要。各非零格必须实际保护和回载/资源/原回执闭合，任一不满足即停止并保留所有格。不要依据目前单轮coverage宣称新pilot必过。
4. 四格全部通过、源签名与独立审计一致后，才启动新的三次轮换formal；保留退化、output hash差异和关闭警告。不把诊断或pilot混入正式均值。

## 并行主线以外的覆盖待办

- 同一先共享再保护批次的owner取消、完整抢占恢复、旧代回执组合GPU验证。当前分立GPU专项和CPU组合契约必须在报告中区分。
- 稀疏探针只严格恢复了3个保护批次中的1个；不能声称另外两个bitwise已通过。
- 实际DMA/driver fault、多worker/异步、永久I/O自动恢复与默认生产启用均未准入。

证据：evidence/shared-prefix-complete-evidence.tar.gz 和 shared-prefix-final-review.tar.gz；最终961测试日志、原始SHA清单、每run冻结源码及失败stopped/partial-audit均在包内。容器保持开机，无任务残留。
