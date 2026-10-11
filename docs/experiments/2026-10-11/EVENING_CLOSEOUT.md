# 2026-10-11 晚间收尾

用户授权持续推进至北京时间 22:00，21:50 起未再启动 GPU 工作。以下是当晚各独立阶段的最终状态；每个正式矩阵单独统计，不把它们合成一个样本。

| 步骤 | 状态 | 主要结果 |
|---|---|---|
| 新空闲收尾栈 2.5 GiB | formal 9/9、612 请求 | one/two 相对 zero 为 -3.07%/-2.00%，首组重复退化，版本边界保留 |
| 六点固定参数 + one/two | formal 24/24、1,632 请求 | 选中 h5.0/d1；one/two 对选中项 +0.88%/-0.19%，未证明保护优于调优固定基线 |
| 固定选择的新文本验证 | 2/12，第三格失败 | one 保护 0/0，原失败保留、无完整均值 |
| 无保护四组参考 | formal 12/12、816 请求 | native 整体 TTFT P95 最低；缓存组末轮回访较快，事后阶段分析单列 |
| 到达计划减半 | formal 12/12、816 请求 | native 仍最低；少算 prefill 没有转为整体延迟改善 |
| 零保护覆盖独立观测 | 单次 68 请求完成 | 9 个首次容量回退：预算空闲、reserve 超过可用块；策略未改，原性能门槛仍失败 |
| 长 prefill pilot | 2/4，第三格失败 | 68 请求返回后空闲 wire identity guard fatal，剩 1 owned job/11 sessions；第四格及 formal 未启动 |

全部报告、归档 hash 和审计文件入口见 [机器可读索引](EVENING_INDEX.json)。推进历史和启动环境失败见 [逐步记录](EVENING_PROGRESS.md)。新增正式 reference/arrival 的客户端原始时间戳、token、资源与源码审计在服务器和本地一致。

## 当前测试与实现

最新实际 CUDA/native 全量回归 **737 passed、115 subtests passed**；之后仅异常时记录身份的观察器另有 4 项 CPU 测试通过。Windows 全量测试因缺少 LMCache/msgspec 在收集期失败，已单独保存，未伪称本地全量通过。测试通过不代表新发现的长 prefill GPU 失败已经修复。

新增了负载 runner、独立原始测量和源码快照校验、阶段拆分数据/图、完整归档与 [实现地图](../../IMPLEMENTATION_MAP.md)。README、接入说明、总计划和 TASKS 已同步实际实现；复核归档不需要 GPU，入口见 [复核说明](REPRODUCE_EVENING.md)。

## 下一步

第一项是长 prefill 身份观测，不是立即扩大保护预算或租更大 GPU。已准备 `IdleIdentityProbeConnector` 与 `diagnose_idle_identity.py`：原 guard 抛异常后记录 pending 的原 wire、当前 client mapping、finished/inflight，再重新抛出原异常。GPU 尚未运行，不能声称知道实际失配来自哪条生命周期路径。

先区分 mapping 缺失与不同 generation，再追 lookup/END/reset/pending admission 的时序；建立最小重现和安全修复后，另立新栈 pilot 与 formal。原失败保持不变，不能删除 guard、对不明 raw ID 发 END，或扩大现有候选支持范围。

## 服务器收尾

21:52 的只读 [快照](long-prefill-evidence/closeout-2152-20261011.json) 确认 8000/8080/5555 关闭、无实验进程、无 GPU 计算进程，4090 为 1 MiB/0%。容器保持开机，没有执行关机。实验服务退出后的空闲状态不抹去失败轮内部的资源残留。
