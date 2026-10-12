# Generation 与非零保护：真实 manager CPU 交接契约

2026-10-12 19:29。独立候选实现及 CPU 契约完成，服务 Connector 尚未接线，未进行本组合的 GPU 验收，不构成策略收益证据。

## 实现与所有权

新增 `scripts/generation_protection_bridge.py`，保留原 zero-protection 服务路径及其非零预算 guard。`GenerationProtectionBridge.commit_protection()` 显式连接原保护 helper 与 generation 账本：提交前核对原 pending 对象、wire、完整 metadata、连续范围、block 列表、suffix hash 和票据容量。原 ticket 跟随保护 prefix，suffix 派生 ticket 保留 parent lineage；已经 seal 的 generation 可以拆分先前预留的工作，不能因此新建 producer。

实际 batch 注册、STORE 和 pin 释放仍由原 manager/helper 完成。桥只登记 prefix in-flight 和原 batch ID；正确原回执终结 prefix，默认 drain 或实际 drop 终结 suffix。错误/重复回执不得释放 pin，旧代回执不能改变 successor pending。zero-token rollback 保留原票据且不派生 suffix。

发现并覆盖一个组合接线异常窗口：`register_batch` 已生效后抛错，helper 可能仍标记 PREPARED；原 scheduler 的通用异常回滚会尝试释放这些 pin。新增 `GenerationBoundedProtection` 在桥 poisoned 后拒绝 rollback/prepare，桥拒绝后续 receipt/admission/reset/finish/release。保留 unresolved 状态和 pin，禁止 END，要求停止并重建实例。它不是自动故障恢复，也没有验证真实 DMA/driver fault。实际 scheduler wrapper 契约保留原异常链并确认 pin 未被回滚。

候选 helper 的 `bind()` 明确拒绝服务接线；测试通过独立入口交接，不能把这份结果写成 Connector 已支持非零保护。

## 验证与证据

在现有服务器 conda 环境运行真实 LMCache manager 和 vLLM BlockPool；无模型服务、无 GPU 推理。最终命令：

```bash
/usr/local/miniconda3/envs/cachepilot/bin/python -m pytest \
  tests/test_generation_protection_bridge.py \
  tests/test_generation_manager_bridge.py \
  tests/test_bounded_protection.py \
  tests/test_generation_session_ledger.py -q
```

最终 **60 passed / 63 upstream deprecation warnings / 8.57s**，其中新组合测试 18 项。覆盖完整/部分保护、成功/失败回执、wrong/duplicate receipt、旧代成功/失败回执、新代 pending 隔离、suffix idle drop、零步 rollback、非法 wire/token/range/block/hash/capacity 拒绝、注册后及 policy 修改后故障、真实 scheduler 异常回滚路径、禁止意外服务 bind。没有执行多 worker、异步调度、真实 driver fault、GPU bitwise、性能消融。

`generation-protection-evidence/` 保留四轮逐步测试原始日志：a=7、b=40、c=59、d=60 passed。这些是迭代回归，不能相加宣称独立测试数量；a–c 对应中间版本，最终源码只对应 d。文件 SHA256 与服务器逐字节复核，见 `manifest.json`。

最终源码 SHA256：

- bridge：`7840fd0fa7163dfd312c8203b8083b7f2ab54864ed7449441c631d5d978d04cf`
- tests：`82c9547f4c2f01f863aa42af33e614b11fa0d3c2d8efeaa4abba694ca6210c8a`

本阶段仅向服务器增加该候选脚本和测试，当前目录不再等于历史 210 项源码集合。匿名结构历史冻结 tar 仍为复现依据，不能用当前目录的新增签名覆盖历史报告。

## 下一道门槛

1. 独立 opt-in Connector 接线：helper 绑定、metadata 拼接、原 worker batch identity、保护回执、END pump 和 lineage 诊断输出；补连续 reset、多候选部分提交与 connector 回调故障契约。
2. 确认 poison/异常时服务停止语义，不让调度器继续运行，不将隔离实例的 pin 留存称为正常资源闭合。
3. 单卡同步 TP1 下独立 zero/one/two GPU 诊断：真实保护触发、STORE→原 terminal→H2D、KV bitwise、精确 pin/resource 回收、长 prefix、失败/取消/抢占及 generation overlap。
4. 正确性通过后冻结新源码，运行新 pilot 与正式消融，对照调优固定 zero；不调整 reserve 或 trace 来制造正收益。
