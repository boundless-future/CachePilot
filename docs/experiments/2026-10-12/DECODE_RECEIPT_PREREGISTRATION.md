# decode192 新立即卸载候选矩阵预注册

北京时间14:31，独立候选GPU诊断已完成且exact server、144 reservation、69 lookup、101 native transfer、实际BlockPool 909 free闭合。原immediate失败和只读诊断失败均保留。此注册先于新矩阵GPU启动。

固定原decode192 trace SHA `5deebe7cd1b7ac052ef0010fd870f349f7058675db93d9df20c5bc5f8d9c8cae`、68请求/13,056输出tokens、Qwen3-4B BF16、4090 24GB、2GiB GPU KV、同步eager单worker；prompt/arrival/seed/think-time不变。非零保护仍不参加。

四套配置为 native、**ImmediateReceiptConnector eager**、GenerationLedgerConnector default h2.5/d64、selected h5.0/d1；15秒idle grace不变。名称沿用 owned-immediate 但新signature标记 `immediate_receipt_end=true`，绝不能与旧OwnedServiceConnector性能合并。属于整套配置比较，无单修复收益推断。

关闭热路径日志；只读服务exact快照持续保留。完整回归、真实future/回调专项通过后冻结全部scripts/configs源码，再pilot四格；所有token、原资源、服务日志、exact-server为空和lazy generation闭合均通过，并独立复算后才可formal十二格。新immediate config在独立收集器中必须精确等于固定源配置加候选选择/opt-in，不能仅凭类名接受。

14:55停止新GPU格，15:00汇报前不启动明显无法安全结束的矩阵；pilot是功能/准入检查，不能当正式n=1收益。今日优先完整pilot和独立审计，formal预计下一工作窗口执行。如果任一格失败则立即停止并保留未运行格；不改资源门槛、不调参、不重跑择优。

立即候选当前不允许抢占、unhealthy worker或raw ID重叠复用；取消/检索中止尚未验收。真实future false仅证明原完成可传递，GPU真实STORE故障与driver fault仍不在验收范围。
