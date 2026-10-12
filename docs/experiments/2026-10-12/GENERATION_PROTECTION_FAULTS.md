# 多候选部分提交与失败关闭证据

2026-10-12 20:52。完成真实 manager/BlockPool CPU 故障门槛；尚未运行本组合模型服务的故障注入或 KV bitwise 诊断。

## 本次实现

`GenerationProtectionConnector.shutdown()` 在原 shutdown/END pump 抛出异常时，先保存只读 generation snapshot，再传播原异常；不重试 END、receipt 或 free。新增 `shutdown_complete=false`、原异常、prepared/inflight保护block数量，保留 pending/inflight票据、lineage与poison信息。JSON采用同目录临时文件替换；写证据本身失败时以 exception note 补充，不能覆盖原始异常。仅 scheduler 路径处理，正常父类行为保留。

## 新增五项测试

- 真实 BlockPool 构造两个独立256-token缓存前缀，实际调用prepare同时选中两者，合计32个pins；成功提交后原回执精确释放，free恢复128。
- 第二个候选在native register之前失败：第一个已inflight、第二个仍prepared；两者引用保留，不得rollback或END。
- 第二个候选在native register之后失败：native已登记两个，但helper仍是一个inflight、一个prepared；不依据helper标签猜测free权限。
- poisoned shutdown保存未完成状态，16个在途pins不被释放、无END，目标JSON落盘且临时文件完成替换。
- snapshot写盘失败仍传播原reconstruction异常，附带证据失败说明，不影响pin所有权。

首轮 `fault-a` 为 **3 failed、32 passed**：新增双候选fixture给第二个raw ID复用了sequence=1，真实账本以Sequence identity collision拒绝，未进入目标提交故障。修正fixture为独立sequence=2，不改生产身份校验，原失败日志保留。

最终 `fault-b` 显式CUDA/native全套 **878 passed、115 subtests、121 warnings，19.74s，无跳过**。其中9项为已有底层真实CUDA传输契约，不代表新组合模型服务验收。

```bash
cd /root/CachePilot
CACHEPILOT_RUN_CUDA_CONTRACT=1 CACHEPILOT_REQUIRE_RESERVATION_NATIVE=1 \
  /usr/local/miniconda3/envs/cachepilot/bin/python -m pytest tests -q
```

原始a/b日志、服务器/本地字节核对见 `generation-protection-evidence/fault-manifest.json`。20:52回归后GPU 0%/1MiB，容器保持开启。

## 下一步具体入口要求

1. 建立独立服务诊断runner，显式选择GenerationProtectionConnector及两个generation opt-in；保留固定单卡同步TP1、Qwen3-4B/BF16与安全reserve。将输入、预算、源码SHA和诊断范围预先记录。
2. 先验证zero服务启动/关闭，再one/two实际保护触发与回执、原terminal、H2D来源、完整KV bitwise及资源闭合。任一门槛失败保留原始目录并停止；不能以CPU或旧栈通过代替本组合覆盖。
3. 在模型服务中注入部分提交/封装/回执异常，验证引擎确实停止、日志和未完成snapshot落盘。磁盘不可写时只能保留原异常链，不能保证磁盘证据存在。当前测试直接调用真实callback，不是完整EngineCore进程退出实验。
4. 正常与故障服务门槛通过后，再新签名pilot/formal策略消融；不提前宣称性能收益。
