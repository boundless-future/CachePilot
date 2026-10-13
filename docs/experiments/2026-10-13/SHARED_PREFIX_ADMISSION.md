# 共享前缀服务准入（2026-10-13）

用户授权推进 1–3：独立接线、正常 GPU 正确性、异常路径。此窗口不启动性能试验。

## 固定设计与门槛

- 新入口 `SharedPrefixConnector` 要求 generation 及 shared-prefix 双显式 opt-in；CPU 候选 `SharedPrefixProtection.bind` 仍拒绝服务绑定。旧默认入口不变。
- 每个 block 额外取得一个属于本策略的引用；不抢走已有 owner 引用。预算按完整 16/32 个引用计，不因共享减少预算或 conservative reserve。
- 保留 token/hash proof、原 batch 回执、prefix/suffix generation 票据及部分注册失败后的 poisoned fail-closed。
- pin 日志增加实际 refs_before/refs_after/shared_blocks_before，区分先共享再保护与先保护再共享。
- 模型 Qwen3-4B BF16、4090 24GB、同步 eager/TP1。正常及异常 bounded workload GPU KV 2GiB；历史新文本保持原 2.5GiB 和 h5/d1，不篡改 prompt/到达时间。

## 执行次序

1. 真实 native callback 契约：新旧 helper 参数化运行 metadata、原回执、旧代、零步、异常终态；新增已有 owner 成功/失败/零步/取消契约和诊断 MRO 检查。
2. 新入口 zero/one/two 正常 GPU；独立重算 KV、严格 STORE→实际 transfer→H2D 来源、原 reservation/generation/BlockPool 闭合。历史新文本单独诊断，并要求已有共享引用的提交与严格回载来源非空。
3. 新入口真实取消、自然抢占、成功 STORE 后报告失败、raw ID 重用旧回执拒绝。统计真实零步回滚；若没有自然覆盖，明确缺口并另行受控验证。

任一检查失败保留目录和原始日志，不重写为成功，不合并诊断时延为性能结果。自然抢占后压力流取消不等于完整恢复证明；成功后报告失败不等于 DMA fault。

## 当前记录

- 首轮 native-a：76 passed。补充 shared owner 与 probe MRO 后 native-b：85 passed。均为相关测试子集，不称全套回归。
- 本地 AST 约束因增加只读引用字段首次失败；更新允许的日志差异时曾缩进错误，修正后 2 passed。未修改预算/选择/commit 语义。
- normal-a 源在服务器启动前归档；运行期间不更新服务器脚本。后续 runner/审计开发只在本地进行，运行前另行冻结签名。
- GPU 结果待填写，未提前标为通过。
