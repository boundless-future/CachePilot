# 2026-10-12 停机交接

核验时间：2026-10-12T23:18:09+08:00。用户要求收尾今天实验；不启动新实验，服务器可由用户关机。本轮没有执行容器关机命令。

## 已确认

- 无运行中的项目实验、pytest、vLLM/LMCache服务或GPU计算进程；8000/8080/5555空闲。
- RTX4090占用1MiB、利用率0%。没有需要等待的在途实验。
- 服务器231项scripts/configs源与shared-prefix-contract快照完全一致，无修改/新增漂移。源码包及相关实验原始文件已在本地和Git归档。
- 代码提交`2d47bf2`已与GitHub main一致；收尾前本地工作区干净。本交接文档另行提交推送。
- 项目heartbeat `cachepilot-15`已是PAUSED，无需再次修改；保持暂停至用户再次授权。
- 最后验证为84项真实库契约（含29项共享引用新增）及2项本地源码约束测试；收尾仅只读核验，不重跑GPU实验。

## 当前断点

共享首块导致零覆盖已定位；独立共享前缀保护候选完成CPU契约。候选`SharedPrefixProtection.bind()`仍拒绝服务绑定，尚无共享候选模型服务/GPU bitwise或性能结果。原新文本pilot零覆盖失败、开发三重复的有限性能结论均保持不变。

末段证据：[开发formal](GENERATION_STRATEGY_FORMAL.md)、[新文本反例](GENERATION_STRATEGY_VALIDATION.md)、[引用诊断](GENERATION_CAPACITY_DIAGNOSTIC.md)、[共享引用CPU契约](SHARED_PREFIX_CONTRACT.md)、[停机检查](shared-prefix-contract/shutdown-check.json)。更早工作见[当天记录](PROGRESS.md)，本交接不重复累计不同实验请求数。

## 开机后的第一步

1. 先核对SSH、GPU、端口和在途进程，避免重复启动。远程是选择性同步目录，不先git pull/全量同步；旧实验复现用各自源码快照。
2. 实现独立opt-in共享前缀Connector/绑定子类，保留旧默认与CPU bind拒绝；核对真实generation manager绑定、metadata/worker回执、环境限制与服务callback/部分提交故障契约。
3. 通过后才运行共享候选GPU正确性诊断：实际共享首块、保护提交/原回执、KV bitwise及资源终态，随后取消/抢占/失败回执路径。
4. GPU准入通过后另开固定新文本pilot→formal。不直接重跑失败pilot、不改输入/降reserve来追求正结果，也不将CPU契约当成GPU通过。
