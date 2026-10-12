# 新组合策略 pilot：有限准入与冻结比较

2026-10-12，GPU pilot 启动前记录。阶段从正确性支线转回 P3 的策略效果比较。

## 有限正确性准入

- 正常 zero/one/two：1,044 请求，720/720/864 层 KV 比较全部相同，最终 pins 0、909 free；见 GENERATION_PROTECTION_GPU.md。
- 1088-token 长前缀：1,800 层全等，8 条完整 prefix/suffix 来源链；见 GENERATION_PROTECTION_LONG_PREFIX.md。
- 原生 STORE 成功后注入失败回执：864 层全等、资源闭合；非实际 DMA 失败，未联合长 suffix 故障。见 GENERATION_PROTECTION_FAILED_RECEIPT.md。
- 共享取消和实际共享受害者抢占：936/504 层全等，引用 2→1→0；抢占观察后取消压力流，不证明完整生成恢复。取消格保留退出 output_handler traceback，不能称全程无错误。见 GENERATION_SHARED.md。
- 提交后主机异常：b 轮自主退出并保留 16 个 unresolved pins，未误回滚或提前 END；a 轮缺快照失败保留。这是失败隔离，非自动恢复。见 GENERATION_PROTECTION_SERVICE_FAULT.md。

这些证据允许在已测同步 eager/单卡范围做有限性能 pilot；不代表生产正确性认证。异步/图模式、实际 DMA 故障、抢占后完整恢复和更广泛并发仍是独立限制，不继续无边界增加 smoke 才开始策略比较。

## 冻结设计

沿用原 fixed formal 开发 trace，SHA256 `49f2c9cb0b2faba83806a53727ef2280fa2bf38351154041f18847c3b0b056af`；68 请求、每请求 48 decode tokens。开发复测，不是留出集。

四格各一次，顺序 default-zero（h2.5/d64）、selected-zero（h5/d1）、selected-one（h5/d1）、selected-two（h5/d1）。h5/d1 来自此前六点三重复选择，不重新调参。one/two 改为与 selected-zero 相同的 lazy 参数，因此比较的是保护预算的增量；与历史 h2.5/d64 候选不是同一配置，历史值不合并。

四组均 GenerationProtectionConnector、相同 generation/ownership 栈。Qwen3-4B BF16、4090 24GB、同步 eager、GPU KV 2 GiB、CPU KV 16 GiB、15 秒 idle grace，保护预算 0/16/32 blocks。关闭逐步事件和 KV probe，仅退出摘要及相同 server 状态观测；状态观测器仍有共同开销，不能视为原生 vLLM 性能。

复用 run_protection_ablation 测量与精确退出日志分类，不改已安装包。逐格要求请求/token、H2D、保护提交=回执且失败为零、预算内峰值、generation 终态、server 空集合、资源回收、无运行期 fatal。候选必须实际保护；任何格失败即停止后续，保留失败及未运行，不调 reserve/压力/预算筛选通过。完整四格才列相对 selected-zero 的 pilot TTFT P95；单次结果不宣称收益、显著性或泛化，不自动混入 formal。

入口 scripts/run_generation_strategy_pilot.py。运行前重新审计历史正常三格和最新共享抢占，runtime 与最新共享抢占已验收源核对；全 scripts/configs 源签名在新 plan 中冻结。每格冷启动，保留原始日志、请求、状态、摘要、命令与源码包。后续独立复算、决定是否预注册轮转三重复 formal；若覆盖失败则先给出触发范围诊断。

## 首轮入口故障及修复记录

a 轮首格实际完成 68 请求，但审计函数缺失而停止，后三格未启动。原因是新入口把仓库根目录插到 sys.path 首位，加载了根目录遗留的旧 run_protection_ablation.py；scripts 下的新版并未缺失。该旧脚本也未包含在原 scripts/configs 签名内，因此 a 的计时不准入、不合并。保留 a 原始文件、scripts/configs 包以及根目录影子源码包。

修复：scripts 目录优先、根目录第二；启动前验证测量模块绝对路径。增加子进程真实导入回归，在根目录放置同名故障模块，确认优先使用 scripts。相关测试 33 passed（新入口 6 + 既有测量 27），不代表全套 GPU/native 重跑。b 使用新目录、新签名，221 个冻结文件的归档字节已复核；不改模型、trace、候选或准入规则。
