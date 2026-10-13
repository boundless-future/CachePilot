# 匿名结构共享候选预注册

2026-10-13 12:40后，用户授权持续推进至北京时间14:30。GPU前固定：六session原匿名结构60请求/209,661输入tokens/每条48输出，trace SHA `445b05df9898232b97d2a0f7a0fc7633a42ab8a4d712943ee3f70ebb6c2acb83`。源21/22/26/57/58/86全部保留，input/8、内部时间/10、首到达对齐0、open arrival、源hash映射token IDs；不是原文、未见集合或live Agent。

沿用历史共享formal运行栈，仅新增两项runner/auditor。Qwen3-4B BF16、4090 24GB、GPU KV2GiB/CPU16GiB、idle15秒、同步eager/TP1。default-zero h2.5/d64；selected-zero/shared-one/shared-two h5/d1；保护预算0/0/16/32 blocks。无额外polling、KV探针或逐步JSONL。

先按上述顺序pilot，4/4门槛通过才同源码formal三次左移轮转。所有请求/输出长度、原始SSE计时、源重建/tokenizer、资源、原回执、非零提交与H2D>0必须通过；失败立即停止，保存未运行格，不调reserve/输入或重跑挑结果。原自然抢占允许新代：唯一raw请求61（含warmup），generation总数61+实际preemptions，wire唯一且精确END、操作和服务资源均闭合。历史结构零保护曾实际62代，不能照搬合成无抢占的固定69代门槛。

原token数组直接发给completion，不当作文本再encode；逐token范围和长度检查，tokenizer文件SHA及源重建审计沿用原准入。性能主指标三轮TTFT P95均值/样本SD和相对selected-zero差异；补充prefill、H2D/D2H、保护次数、输出hash差异与退出警告。三次不是显著性证明，总体H2D不是逐批保护回载证明。

14:25后不再启动新格，为14:30收尾留时间；若在途格异常长，安全清理并标记不完整，不聚合缺格矩阵。任何后续诊断另立目录/签名，不改原失败。主线完成且有余时优先离线复用机会和机制分析，联合异常正确性独立规划。全流程记录、归档并提交推送。
