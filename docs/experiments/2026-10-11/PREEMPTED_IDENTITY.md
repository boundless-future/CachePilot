# 抢占恢复绕过 lookup：身份登记缺口

2026-10-11 22:38–22:54，用户在 22:00 收尾后重新要求继续推进。本次为独立定位，不重开旧性能矩阵，不重新启用 heartbeat。

## 已确认的调用链

新增逐事件诊断实际观察到：

1. 请求首次 lookup 使用 wire A，`OwnedRequestClient.identities[raw_id]` 登记 A。
2. 自然抢占后，真实 Connector 执行 manager reset 并重建 tracker；我们的适配器为新 tracker 生成 wire B。
3. 固定版 `LMCacheMPConnector.get_num_new_matched_tokens` 对 `PREEMPTED` 返回 `(0, False)`，不调用 lookup。因此 client 映射仍为 A。
4. 新 pending metadata 和 worker STORE 使用 B。完成时 manager 仍按 raw ID 发出 session-end action，client 将其解析成 A，B 没有对应 END。

这是 **CachePilot 自有 generation 适配与底层抢占旁路组合产生的缺口**，不能描述成已经确认的 LMCache 原生身份协议缺陷，也不能归为保护预算策略不可行。本轮保护预算为零。

## 两次 GPU 诊断

| 独立运行 | 请求 | 抢占 | 结果 |
|---|---:|---:|---|
| failure-only identity a | 68 | 10 | pending 排空，未触发 idle 异常；9 active sessions 残留，资源门槛失败 |
| lifecycle timeline a | 68 | 6 | 观察到上述链路；4 个 STORE wire 无 lookup/END，4 active sessions 残留，资源门槛失败 |

两轮原始 token/时间戳独立检查均通过；均不进入性能均值。第二轮包含 warmup 的 69 个 lookup 身份、75 次 tracker 创建、6 次 reset、24 个 STORE 身份，其中 4 个 STORE 身份来自抢占后的无 lookup generation，涉及 3 个 raw ID。每个新代首次 pending admission 的 client 映射均不匹配。

第二轮没有复现原 idle fatal：这些 pending 在后续真实调度中排空了。但实际运行已经证明了产生身份不匹配的路径，下面的 CPU 复现进一步证明它在 pending 保留时触发原 guard。原 pilot 的那次具体失配没有原始身份值，不能事后声称已逐请求还原。

4 个未 END STORE 身份与 4 个残留 session **仅数量一致**；本轮没有逐项服务器 session 快照，不能将数量等同于服务端身份确认。日志是 client 方法调用/返回顺序，END 返回也不是 server ACK。第二轮退出有 output-handler traceback；因为 runtime drain 失败，不满足已有严格关闭竞态豁免规则，原 `service_errors` 保留，不从结果中删除。

## 最小复现与验证

`tests/test_preempted_tracker_identity.py` 使用实际 vLLM Request、LMCache tracker、原 `get_num_new_matched_tokens`、真实 manager/BlockPool 及我们的 OwnedServiceConnector；仅 transport 用记录替身，不需要模型推理：

- pending 路径：真实抢占 tracker 重建且不发 lookup，新 metadata 保留 B、映射仍 A，空闲 guard 抛错，pending、引用及 LRU 不变。
- STORE 路径：真实 manager drain/pin/receipt 后释放 GPU 引用，worker STORE 使用 B，session-end 仍发送 A，没有发送 B。

第二项测试首版忘记先 finish 就使用“仅保护已结束请求”的 helper，未提交 STORE 而失败；改用真实默认 manager drain 后两项通过。没有据此调整产品代码。

相关专项 **56 passed**。完整显式 CUDA/native 回归 **746 passed、115 subtests、121 warnings，18.46 秒**。之后新增的离线审计器 5 项测试另计，服务器/本地均通过；不算入 746。原 idle guard、OwnedRequestClient 和生产候选源码均未修改，原 pilot 的 177 项源码检查继续通过；新增诊断默认不启用。

## 下一步修复约束

需要把 lookup 路由与所有 session generation 的终结登记分开，不能只把 `identities[raw_id]` 改成新 wire：旧 lookup 清理仍必须使用 A，已提交但迟到的旧代 STORE 也可能在提前 END 之后重新创建 session。

建议先在独立候选中登记每次 tracker/STORE 使用的原身份，在原 manager 确认该 raw ID 的所有在途批次终结、未提交 pending 已处理后，按确切身份结束所有应终结代际；lookup 查询继续使用其原身份。还需核对 server 的 sequence/high-water 语义和多次 END 幂等边界。未经这些检查，不允许删除 idle guard、仅覆盖 mapping 或盲目 END 当前 raw ID。

修复准入包括：连续两次抢占、旧批次迟到提交/回执、raw ID 复用、取消、多个 pending、END 部分失败和实际服务 session 逐身份闭合。随后单独冻结修复栈，运行新长 prefill pilot；原失败永久保留。此处是修复设计约束，**尚未实现修复，长 prefill formal 仍禁止启动**。

## 复核与原始证据

原始包与 [SHA256 清单](long-prefill-evidence/identity-evidence-manifest.json) 已保存。本次两个压缩包分别为：

- `long-prefill-identity-20261011a.tar.gz`：`49036f26f05cd61a2cb1b190408abb5d78a3715fdafd843eb8028d780e013d45`
- `long-prefill-timeline-20261011a.tar.gz`：`caf5372ad77733a7e0b30728af0c2525a0ef0238902f2451673f7757cd446eb8`

解压第二包后运行 `python scripts/analyze_identity_timeline.py <解压目录>/artifacts/long-prefill-timeline-20261011a --output timeline-analysis.json`。已从下载包离线复算并保存 [结果](long-prefill-evidence/timeline-analysis.json)。新增诊断代码及原源码签名包含在运行 manifest 中。

22:53 收尾：8000/8080/5555 全关闭、无实验/compute 进程、GPU 1 MiB/0%，容器保持开机；见 [快照](long-prefill-evidence/identity-closeout-20261011.json)。退出后空闲不抵销运行中的 session 残留。
