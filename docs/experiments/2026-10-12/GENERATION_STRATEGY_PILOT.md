# generation＋保护策略 pilot

2026-10-12。当前从组合正确性准入进入 **P3C 策略效果比较**。b 轮四格通过，共 272 个测量请求、13,056 输出 tokens；另有 4 个预热请求。单次开发集结果，不是 formal，不证明稳定收益。

## 设计与结果

[预注册及有限准入范围](GENERATION_STRATEGY_PREREGISTRATION.md)。沿用原开发 trace；所有配置采用相同 generation/ownership 栈、Qwen3-4B BF16、4090 24GB、同步 eager、2 GiB GPU KV、16 GiB CPU KV、15 秒 idle grace。关闭 KV probe 和逐步 JSONL，仅保留计数/关闭摘要和共同 server 状态观测。

候选使用此前选中的 h5/d1，与 selected-zero 只差保护预算；历史 h2.5/d64 候选不可合并。顺序如下，每格一次：

| 配置 | TTFT P95 ms | 相对 selected-zero | 保护提交/回执 | 峰值保护 blocks | D2H bytes |
|---|---:|---:|---:|---:|---:|
| default-zero h2.5/d64 | 8454.21 | +3.68% | 0/0 | 0 | 2,868,903,936 |
| selected-zero h5/d1 | 8153.79 | 0 | 0/0 | 0 | 2,868,903,936 |
| selected-one h5/d1 | 7944.38 | −2.57% | 4/4 | 16 | 2,906,652,672 |
| selected-two h5/d1 | 8996.27 | +10.33% | 4/4 | 32 | 2,717,908,992 |

所有格实际 prefill 为 **24,156 tokens**、外部命中 **12,288 tokens**、H2D **1,811,939,328 bytes**。所以不能将单块较快归因为降低重算量；双块搬运更少也未得到较低尾延迟。这里只有单次固定顺序观测，无法排除调度波动/运行顺序影响，不选择性发布单块收益。

one/two 分别出现 32/32 次容量回退、2/5 次在途预算回退；这是摘要记录数，不是每个 scheduler step 的发生率。保护存在实际覆盖，但可触发范围仍有限；本次没有修改 reserve 或工作负载来提高覆盖。

## 验收与限制

- 逐事件 ID、输入/输出 token、原始 SSE 时间戳、存储延迟摘要及输出 hash 独立复算全部通过。
- 每格 69 个唯一 generation（68 测量＋1 预热），注册/END 计数一致，所有 ledger 终态闭合；server session/job/transfer 集合为空。
- 保护提交=回执、失败回执 0，prepared/inflight 在退出前清零；预算峰值没有越界，退出后 GPU 注册清零。性能轮没有额外物理 BlockPool/KV 探针，不能声称这里再次测到了 bitwise 或 909 free。
- 单块格出现 8 行已精确分类的正常停机 output-handler traceback；原始日志与停机上下文保留，其余三格未出现该类别。运行期 fatal 检查通过，不写成“所有日志无错误”。
- 相对 selected-zero，default-zero / one / two 的输出 hash 差异为 **2/68、0/68、2/68**。固定 decode token 数不保证逐字一致；本轮不是输出差异根因诊断。

## 首轮失败与工程修复

a 首格完成 68 请求后因缺少 `service_error_report` 审计函数而停止，后三格未启动。新入口错误地优先搜索仓库根目录，加载那里遗留的同名旧 ablation 脚本；并非 scripts 下的新文件缺失。旧文件不在原冻结路径集合内，因此 a 不计入有效结果。保存 a 原始证据、scripts/configs 源包及根目录影子源码包。

修复导入优先级并在开跑前验证实际测量模块路径；子进程测试用根目录同名故障文件验证不会误导入。b 使用新目录和新签名，不改变候选、trace 或验收门槛。最后 **38 项测试通过**：入口/配置与导入 6、归档篡改审计 5、既有测量回归 27；不是全套 native/GPU 单元测试重跑。

## 证据

目录 [generation-strategy-evidence](generation-strategy-evidence/)。b 共 63 个原始文件、3,971,880 bytes，与远程 SHA256 清单逐一一致；a 16 个原始文件也已核对。日志因仓库全局忽略规则，提交时显式纳入。

- [本地独立复算](generation-strategy-evidence/b-local-audit.json)、[原始字节核对](generation-strategy-evidence/b-byte-audit.json)、[源包核对](generation-strategy-evidence/initial-byte-audit.json)。
- b 冻结源 221 项；[源码包](generation-strategy-evidence/generation-strategy-pilot-20261012b-source.tar.gz) SHA256 `259182205927c91efc8671cff4167f445a1c67fc07dd9e21ab35ab683356945e`。离线审计工具在实验期间仅写到本地，未改变服务器冻结源。
- 独立复算入口：`python scripts/audit_generation_strategy_pilot.py docs/experiments/2026-10-12/generation-strategy-evidence/generation-strategy-pilot-20261012b --output <report.json>`。

## 下一步

预注册四组各三次、按 repeat 偏移 0/1/2 轮转的开发 formal，共 12 格；保持四组和全部参数，不因本次 single pilot 删除双块或重新调参。formal 需要独立入口和完整同运行时准入，明确 harness 新签名与本次 runtime 字节一致；pilot 不计均值。继续以原始时间戳、generation/资源、保护覆盖和错误日志为门槛，任何失败停止并保留未运行格。

若重复后仍无稳定收益，结论为该负载下保护不优于调优固定策略，进一步分析触发范围与在途代价，不以追加 smoke 代替策略评价。留出负载、实际 DMA 故障、异步/图模式和抢占后完整恢复保持独立待办。本轮服务已全部停止，容器保持开机。
