# 稳定请求身份与保护净价值诊断

2026-10-19。接续 [NET_EFFECT](NET_EFFECT.md)，本轮完成机制归因。**当前头部保护候选没有观察到新增复用价值，停止扩大该候选的性能实验。** 3C 的策略净收益尚未成立；这不否定已经通过的接线、KV 正确性和资源回收证据。

## 固定条件与执行结果

沿用原六会话匿名结构轨迹，60 请求/格；Qwen3-4B BF16、RTX 4090 24GB、vLLM 0.30.0、既有锁定 LMCache、TP1、同步 eager、GPU KV 2 GiB、CPU KV 16 GiB、max-num-seqs=4、输出 48 tokens。统一 h5/d1/grace15，zero/legacy-two/filtered-two 分别为零保护、原 two、two 加无候选早退。每格重启服务，没有换输入或预算来追求通过。

诊断 Connector 仅在原调用返回后记录 GPU computed prefix、external matched tokens、raw ID 和 generation wire ID。回放 API 请求 ID 固定为 `cachepilot-trace-{event_id:04d}`，响应为 `cmpl-...`，内部 raw ID 为 `cmpl-...-0`；显式关闭 vLLM 请求 ID 随机化。原运行冻结 301 项源码，输入 trace SHA256 为 `445b05df9898232b97d2a0f7a0fc7633a42ab8a4d712943ee3f70ebb6c2acb83`。

| 格 | 完成请求 | 输出 tokens | scheduler generations | 全局 RETRIEVE 操作 | 保护成功批次 | pin 峰值 |
|---|---:|---:|---:|---:|---:|---:|
| zero | 60 | 2,880 | 62 | 7 | 0 | 0 |
| legacy-two | 60 | 2,880 | 63 | 5 | 1 | 32 |
| filtered-two | 60 | 2,880 | 62 | 7 | 1 | 32 |

共 180 请求、8,640 输出 tokens。API/响应/原请求/每次 generation 精确映射；generation END、原批次回执、native 提交→terminal→成功退休、pin 预算和最终资源空均通过。RETRIEVE 是全局操作数，不能将其增减当作被保护前缀的收益。

## 被保护内容的实际去向

两保护格的唯一目标均来自 **event 8 的 `[0,512)`**，token 前缀 SHA256 为 `45f3fefbef7f43c8365fc6eb7d6860b1123ef27af80101d324d59c9621f31932`。将相同逻辑 token 目标用于 zero；不跨格使用 batch/wire ID 或单调时钟。

| 后继 event | zero GPU 命中 tokens | legacy-two GPU 命中 tokens | filtered-two GPU 命中 tokens | 两保护格 STORE 完成相对 cache-match 观测 |
|---|---:|---:|---:|---|
| 11 | 1,696 | 1,696 | 1,696 | 晚于观测 |
| 14 | 1,728 | 1,728 | 1,728 | 晚于观测 |
| 17 | 1,760 | 1,760 | 1,760 | 晚于观测 |
| 19 | 1,792 | 1,792 | 1,792 | 晚于观测 |
| 24 | 1,824 | 1,824 | 1,824 | 早于观测 |
| 25 | 1,872 | 1,872 | 1,872 | 早于观测 |

三格的这六次请求 external matched tokens 全部为 0，每次只有一个已完成 cache-match 观测，generation 内匹配值无变化、请求无重入。GPU 覆盖均超过目标的 512 tokens。前四次观测甚至先于保护成功；后两次虽然在成功之后，zero 已有完全相同的 GPU 命中。

因此本轨迹中，保护没有为目标请求增加 GPU 之外的 CPU 回载覆盖，也没有显示额外避免重算的作用。此前“没有对应 RETRIEVE”不足以得出这个结论，本轮补齐了 **zero 的相同请求也命中 GPU** 这一对照。

这里的时间是 Connector 返回后记录的 **cache-match 观测时间**，不是异步 CPU lookup 的发起时刻。STORE 早于观测不能证明查询发起时 CPU 条目已驻留。分析先跳过 external=None 的异步轮询，再取首次已解析观测；发生重入时逐 generation 保留，不能把首次缺口等同实际执行的重算 tokens。

全局 computed prefill 为 33,677 / 36,477 / 33,677，抢占为 1 / 2 / 1；输出 SHA 相对 zero 的差异为 legacy 8 条、filtered 1 条，全部保留。并发调度和输出差异存在，不能把全局差异归因为这一次保护。**本轮带诊断记录，不报告 TTFT 改善，也不并入正式性能均值。**

## 下一条主线：先证明丢失尾部存在安全保存窗口

只读扫描每条请求相对于更早到达 prompt 的最长完整 256-token 共享前缀。zero 首次已解析 cache-match 中的缺口如下，属于机会定位，不是已执行重算统计，不能求和宣称节省量。

| 前序来源 → 当前 event | 共享完整 tokens | 首次 GPU | 首次 external | 当次未覆盖 |
|---|---:|---:|---:|---:|
| 1 → 9 | 4,352 | 1,792 | 0 | 2,560 |
| 7 → 15 | 6,912 | 0 | 5,888 | 1,024 |
| 12 → 16 | 4,608 | 4,256 | 0 | 352 |
| 13 → 18 | 2,304 | 1,984 | 0 | 320 |
| 16 → 38 | 4,608 | 2,256 | 1,328 | 1,024 |

**仅聚焦 `1 → 9`。** 两请求属于 `trace_0022`，实际共有 4,464 tokens，按 chunk 对齐为 4,352。此前没有其他相同前缀来源。source 1 的普通 STORE 在 zero/filtered 成功保存 `[0,1792)`，legacy 为 `[0,2048)`；成功历史不证明后来驻留。event 9 的 GPU 观测随自然抢占/重入变化，zero/filtered 后一代为 3,584；legacy 后续两代为 2,032/3,888，因此不能说当次缺口就是最终执行重算量。

两保护格在 source 1 均有 `capacity` 回退：free=302、reserve=2,081、pin=0。该观测只证明当步无法满足现候选准入，未覆盖全部生命周期。现保护从 0 截取头部，且只处理已结束请求的 front STORE，不能直接拿它保存后续任意 offset。**只改为尾部选择并不自动解决容量和连续 CPU 前缀约束。** 原日志选择及 SHA 见 [source1 证据](evidence/identity-source1-window-review.json)。

下一步只回答：source 1 尚未保存的连续 chunk，在 GPU 哈希失效前，是否存在满足当前容量、引用、前缀连续性条件的保存窗口？先以现有源码和一次最小只读生命周期诊断确认 pending 区间、普通 STORE 进度、block hash/引用、free rank 与 reserve 的先后。event ID 只用于离线审计，不进入策略；不能通过偷看未来 trace 选择目标。

若窗口存在，再仅修改对应候选选择或触发模块，保留预算与回执/资源安全门槛，做同来源 KV 准入及零保护匹配比较。若窗口不存在，记录当前策略适用范围及精确阻断，再决定是否值得设计新的准入方法；不降低 reserve、换轨迹或扩大模型来掩盖问题。当前不启动新性能矩阵。

## 失败、审计与复算

首次 `identity-diagnostic-20261019a` 在 zero 启动阶段失败：`exec: vllm: not found`，尚无完成格。原因是直接使用环境 Python 未设置服务 PATH；同源 b 轮通过 `/usr/local/miniconda3/bin/conda run --no-capture-output -n cachepilot` 启动后完成。a 的 23 文件完整保留，未替换为 b 的结果。

b 的 filtered-two 在请求完成、资源排空后的关闭阶段出现既有窄分类 `AsyncLLM output_handler` / `EngineDeadError` traceback，8 行原样归档；运行期 fatal=0，CUDA IPC 关闭 warning 也保留。不能表述为“没有任何错误”。

原 b 冻源只代表当时的诊断入口与分析器；事后严格交叉格分析、首次已解析观测语义和缺口分析分别记入补充审计，**没有改写原 `identity-analysis.json`、`identity-summary.json` 或原冻源**。v2 为中间分析记录，最终以 v3 为准。

- 原失败包：[a](evidence/identity-diagnostic-20261019a.tar.gz)，SHA256 `2efc56410cb2d04ceef957b53cc9cb6328fe65861755e320f279e315fbb1b413`；[23 文件清单](evidence/identity-diagnostic-20261019a-inventory.json)。
- 原成功包：[b](evidence/identity-diagnostic-20261019b.tar.gz)，SHA256 `0bf2881af7e2bfba4eef8e63f9fc31152cc57e5d1a19389d8540cc19ac859021`；[68 文件清单](evidence/identity-diagnostic-20261019b-inventory.json)。
- 补充 v3 源码：[304 项快照](evidence/identity-audit-v3-source.tar.gz)，SHA256 `a7c167d18204ab8636b2258c2896b37356243da09eab5e68804190fe71275de1`；[逐源码 SHA](evidence/identity-audit-v3-source-inventory.json)，含 302 项运行/研究源码与两个定向测试文件。
- 最终结果：[本地 v3](evidence/identity-local-audit-v3.json)、[服务器冻结快照 v3](evidence/identity-server-audit-v3.json)，解析后的 JSON 完全相同，3/3 通过。原包逐文件哈希均通过。完整核对及测试范围见 [validation](evidence/identity-validation-v3.json)。

本地与服务器的身份分析/回放测试各 **20 passed**；额外本地源码快照及模块边界 **6 passed**。没有改动 `src/cachepilot` 运行时，也没有把离线分析测试当成新的 GPU 正确性验证。服务器审计目录为 `/root/cachepilot-identity-audit-v3-20261019/source`，原实验目录保持 `/root/cachepilot-neteffect-20261019/runs/identity-diagnostic-20261019b`。

将原 b 包解到 `<run>`，将 v3 源码包解到独立目录，可执行：

```bash
python -m pytest -q tests/test_whole_step_identity_diagnostic.py tests/test_replay.py
python scripts/audit_whole_step_identity.py <run> --output <new-output.json>
```

审计是只读的，输出必须使用新路径。服务器最终空闲状态见 [idle](evidence/identity-final-idle-20261019.json)；容器保持开机。
