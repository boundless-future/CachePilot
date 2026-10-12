# 新组合失败回执 GPU 专项

2026-10-12。独立 `GenerationProtectionReceiptProbeConnector` 复用既有ReceiptProbe与generation Connector，通过正常合作继承连接实际worker future和scheduler原回执。runner新增 `--failed-receipt`，与 `--long-prefix` 互斥；默认正常三格行为保留。

固定576-token来源、two预算、原bounded负载，Qwen3-4B/BF16、4090 24GB、同步eager TP1、2GiB KV；plan先记录217项源码。选择一次实际成功的保护STORE，在完成后把worker结果报告为失败。**这不是STORE执行失败、DMA中断或driver fault**，也不是长前缀suffix失败的联合实验。

## 结果

- 348完整请求，348代generation注册/END提交/server END逐身份闭合；14个STORE批次，其中恰好1个失败回执。
- 9次保护STORE，峰值32个blocks，最终pins0，BlockPool恢复909；26个原reservation及26次native transfer终结闭合。
- 864层实际回载KV全部bitwise相等；原严格保护来源审计通过。
- 独立回执时序审计通过：scheduler built→安装探针→实际future ready→真实result=True但交付False→worker failed receipt→保护释放记录→scheduler failed receipt；回执前32个block均有引用。
- 完成前后服务精确集合为空，账本无未完成操作。引擎日志有1条与注入目标对应的原adapter STORE失败ERROR，符合预期；不将日志宣称无错误。

22原始文件4,222,630bytes与服务器SHA一致，217项实际运行源码逐项核对并tar归档；本地联合normal与failed-receipt审计独立通过。证据见 `generation-protection-failed-receipt/independent.json` 及原始目录。运行后GPU0%/1MiB，服务停止、容器保持开启。

本窗口长prefix与本专项共696请求；长prefix1800层、本专项864层分别报告，不混入性能样本。新增lineage篡改/既有split/normal/fault离线审计19 passed，不声称本轮重新执行879项服务回归。当前生产Connector未修改，仅新增诊断组合、runner分支和审计。

下一步：新组合共享前缀取消与实际抢占GPU专项；若要验证长suffix失败，须单独设计能保留成功对照与严格来源的负载。之后才评估新签名策略pilot/formal。
