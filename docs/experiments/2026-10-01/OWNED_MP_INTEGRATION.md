# 正式 MP 服务 opt-in 桥接与资源验收

日期：2026-10-01。环境为 RTX 4090 24GB、Qwen3-4B、vLLM 0.30.0、LMCache commit `1a4b40b1d79b0e76244f127f96ee0982f8bd270f`。本轮把身份检查和受控失败释放接到**实际运行的** LMCache MP server、外部 Connector 与 vLLM scheduler，配置为 opt-in；原生路径和此前的独立 ticket/lease 原型没有被替换。

## 接入范围

- `owned_mp_connector.py` 在每个 request tracker 上建立 128-bit 随机 generation，并经现有 `request_configs` 送达 LOOKUP/RETRIEVE；记录真实 BlockPool 的 waiting 引用、完成后的引用、空闲块与 deferred free。
- `owned_mp_server.py` 在固定安装版 `LookupModule` 和 `LMCacheDrivenTransferModule` 外加校验：LOOKUP 记录请求身份，RETRIEVE 核对 generation、模型/token/range、worker 和目标 block 长度。源码 SHA256 不符时拒绝启动。非法短 block 在未提交 GPU copy 前调用安装版失败释放路径。
- 正常 RETRIEVE 提交后在同一 CUDA stream 追加独立 native callback marker，区分 RPC 返回与 stream marker 到达。marker 只提供时序证据；它尚未承担真实 source buffer lease 或目标 BlockPool 所有权的释放职责。
- `owned-mp.json` 与 `owned-server-reject.json` 分别用于正常服务和一次性非法 block 注入；`serve.sh`、`lmcache-server.sh` 保持显式开关。

## 实机结果

| 路径 | 真实链路观测 | 资源结果 |
| --- | --- | --- |
| 正常 CPU KV 回载，`owned-mp-r3` | 1536 external-hit tokens、1 次回载、3 次 STORE；LOOKUP → RETRIEVE 提交 → RPC 结果 → native marker → END，同一 generation；冷/热/重启回载输出一致 | waiting 时 96 个 BlockPool block 有引用；结束后 909 free、无 tracked ref/deferred free；服务退出时 0 读锁、0 prefetch job、0 GPU 注册 |
| 注入空目标 block，`owned-server-reject-r3` | 4352 external-hit tokens；server 校验要求 272 个、实际 0 个，拒绝在 GPU copy 前发生；vLLM 将 272 个错误 block 重算，目标与参考输出一致 | BlockPool free 909 → 909、无 tracked ref/deferred free；0 CPU 读锁、0 job，vLLM 停止后 GPU 注册清空 |

后一项是受控非法输入，不能推断正常 Connector 会提交空 block。此前未加 guard 的同类 underflow 对照留下 17 个读锁，见 [原实验](../2026-09-27/SERVER_REJECTED_RETRIEVE.md)。本次只证明固定安装版在此路径的局部缺口得到补偿，没有覆盖实际传输中断。证据：[正常摘要](owned-mp-evidence/normal/summary.json)、[正常原始结果](owned-mp-evidence/normal/result.json)、[退出状态](owned-mp-evidence/normal/after-engine-stop-status.json)、[非法输入摘要](owned-mp-evidence/underflow/summary.json)、[非法输入原始结果](owned-mp-evidence/underflow/result.json)。同目录保留 server 与 scheduler 原始 JSONL。

完整服务器测试在显式启用 CUDA 契约后为 **405 passed、115 subtests passed、133 warnings**，见 [测试日志](owned-mp-evidence/full-pytest-cuda.txt)。此前默认测试为 396 passed、9 skipped；跳过的 9 项正是需显式启用的 CUDA 契约。复现命令：

```bash
export PATH=/usr/local/miniconda3/envs/cachepilot/bin:/usr/local/cuda-13.0/bin:$PATH
python scripts/validate_environment.py --modes owned-mp --output artifacts/owned-mp-r3
python scripts/summarize_owned_mp.py normal artifacts/owned-mp-r3 --output artifacts/owned-mp-r3/summary.json
python scripts/retrieve_failure_smoke.py --mode server-reject --owned-guard --output artifacts/owned-server-reject-r3
python scripts/summarize_owned_mp.py underflow artifacts/owned-server-reject-r3 --output artifacts/owned-server-reject-r3/summary.json
CACHEPILOT_RUN_CUDA_CONTRACT=1 python -m pytest -q
```

复核中修正了两个入口问题：新 tracker 不再接受 request 配置预置的 generation，由桥接层独立随机生成；`validate_environment.py --modes owned-mp` 自动设置 server guard 的事件目录。修正后 `owned-mp-r5` 再次通过，同样有 1536 外部命中、96 个 waiting block 引用和最终 909 free，见 [r5 摘要](owned-mp-evidence/normal/summary-r5.json)。`owned-mp-r4` 因只加载 Connector、没有启用 server guard，普通 smoke 虽通过，但事件验收失败，不算正式桥接通过。`owned-server-reject-r2` 因启动命令覆盖 PATH 丢失 bash 而未启动服务；保留服务器原目录，没有作为实验结果。

当前代码的 `owned-server-reject-r4` 追加复测也通过：4352 外部命中、272 block 重算、909 free、0 读锁及退出后 0 GPU 注册，见 [r4 摘要](owned-mp-evidence/underflow/summary-r4.json)和[完整结果](owned-mp-evidence/underflow/result-r4.json)。两类复测没有扩大故障覆盖范围。

## 仍未通过的 3B 门槛

服务桥仍使用 LMCache 的匿名读锁和 key-only completion 释放。随机 generation 的进程内检查不能代替 reservation/epoch、原 buffer lease、版本化 wire ticket 或有界失联恢复；独立 native marker 也不能证明 DMA 执行错误时一定终结。无 END_SESSION 的客户端死亡、迟到 LOOKUP、controller 永久阻塞、server shutdown、真实 driver fault、自然 DMA 期间抢占和正式 writer 失败路径尚未在此桥上闭合。BlockPool 快照证明本轮两个用例最终回收，不等于实现了跨异步阶段的目标 block owner。

因此**正式服务接入的受控正常与非法输入路径局部通过，阶段 3B 整体仍未通过**。下一步将独立原型的原 reservation/lease/ticket 与真实 READ/RETRIEVE/STORE 生命周期连接，并用失联和真实故障复核，之后才考虑 3C 的 GPU 策略消融。
