# Generation 与保护：独立 Connector 接线

2026-10-12 20:38。接续 [CPU交接候选](GENERATION_PROTECTION_CONTRACT.md)，本阶段完成独立 Connector 实现及真实库回调验证。没有运行新组合的模型服务或性能实验。

## 已实现

- 新增 `scripts/generation_protection_connector.py` / `GenerationProtectionConnector`。须显式选择类，同时启用 `cachepilot.generation_ledger=true`、`cachepilot.generation_protection=true`。原 `GenerationLedgerConnector` 仍拒绝非零预算；共享父类只增加默认行为不变的工厂入口。
- `GenerationBoundedProtection.bind()` 绑定真实 manager bridge，使用原 BlockPool 分配边界检查；保护提交先经过账本交接，再进入默认 manager drain，合并 STORE metadata。prefix 与 suffix 的票据及原 batch ID 随已有 handoff envelope 进入 worker。
- 原 worker envelope 返回后，先校验完整 handoff，原 manager 释放 pin，再由 helper 记录保护回执；默认 suffix 终结之后才能 END。snapshot 增加派生票据 lineage 和 poisoned 状态。
- native 注册后、默认 drain 后或 metadata 封装时异常均阻止实例继续；receipt 进入 native mutation 后异常也进入隔离状态。错误/重复 envelope 在 mutation 前拒绝。拒绝自动重试或猜测 free；这是 fail-closed 边界，不是故障自动恢复。

## 验证

新文件 `tests/test_generation_protection_connector.py` 有12项测试：one/two × success/failure、真实 native build、worker pickle envelope 与原 scheduler receipt、错误/重复回执、默认 suffix drain、零token rollback、双重 reset 后旧代 success/failure、提交后封装失败、native free 后 helper receipt 异常、END pump 拒绝 poisoned、显式 opt-in 与构造工厂。测试 transport、模型请求处理和实际 GPU copy 是 stub；native metadata/manager/BlockPool 和 receipt callback 使用安装的真实库。不能称为跨进程模型服务验收。

逐步结果（不可相加）：

| 回归 | 结果 |
|---|---|
| 第一轮接线＋相关回归 | 35 passed |
| generation/保护/idle 专项 | 157 passed |
| 中间版本完整回归 | 863 passed、9 skipped、115 subtests |
| 最终显式 CUDA/native 完整回归 | **873 passed、115 subtests、121 warnings，18.57s** |

最后一轮包含随后新增的 native free 后 receipt fault 测试以及9项已有 CUDA transfer/writer 契约。CUDA tests 是底层真实 kernel/D2H/H2D 回归，不等于本组合完整 KV bitwise、负载覆盖或性能。warnings 为上游弃用提示，无跳过。

```bash
cd /root/CachePilot
CACHEPILOT_RUN_CUDA_CONTRACT=1 CACHEPILOT_REQUIRE_RESERVATION_NATIVE=1 \
  /usr/local/miniconda3/envs/cachepilot/bin/python -m pytest tests -q
```

原始四轮日志及远端/本地SHA核对见 `generation-protection-evidence/wiring-manifest.json`。当前运行源码已变化；历史正式矩阵只对应其归档冻结源码，不以本版本冒充同签名复现。

## 仍须通过的服务准入

1. 补多个候选中途提交失败及真实服务启动/停止边界。CPU poison 证明不会继续操作，尚未验证模型进程发生这些故障时的退出和证据保存。原 shutdown 在 END pump 异常时可能先抛出，不能保证生成终态 snapshot，GPU故障诊断须保留引擎日志并补该路径。
2. 新建独立诊断入口和预注册；固定模型/输入/预算，先 zero，再 one/two。必须联合检查真实保护触发、原 STORE terminal、H2D 来源、完整 KV bitwise、BlockPool/refcount/generation/session 闭合。不能直接复用旧零保护 auditor 宣称组合验收。
3. GPU 长 prefix、取消、抢占和失败回执覆盖通过后，才冻结新签名并启动策略 pilot/formal。多worker/异步/真实driver故障及自动恢复保持开放。

20:38 回归结束 GPU 0%、显存1MiB；容器保持开启。
