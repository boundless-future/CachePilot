# 3C 零保护预算：真实交接验收

2026-10-09：**零预算 scheduler/worker 交接已在真实单卡服务上通过。** 尚未增加候选保护、保护 pin 或额外 STORE，尚无性能收益结论。下一步为最多两个完整 chunk 的保护实现。

## 环境与范围

沿用 RTX 4090 24GB、Qwen3-4B BF16、Python 3.12.14、vLLM 0.30.0、CUDA 13.0 和 LMCache commit `1a4b40b1d79b0e76244f127f96ee0982f8bd270f`。重启后核对上一检查点 173 个源码文件 SHA256 全部一致，端口空闲。此次 GPU KV 固定 2 GiB，CPU L1 16 GiB，无 L2；eager，单 worker，默认异步调度。原有 3B 的 L2 与故障证据不替代本轮新增路径验证，本轮也不宣称重新覆盖全部 3B 场景。

## 实现

- `ProtectionConnector` 继承固定版 `OwnedServiceConnector`。配置 `cachepilot.protection.enabled` 区分关闭/开启，`cachepilot.protection.max_chunks` 当前仅接受整数 0；非零预算直接拒绝，不假装已经支持。
- 默认 `EVICTION_AWARE` manager 继续决定 STORE、拥有原 pin 和处理原 worker 结果。保护入口是显式 bypass，未安装 CPU pool harness，未新增 pin/unpin 或 STORE 调用。构建 metadata 前后比较范围、物理 block 列表、前缀摘要与原 wire identity，零预算不允许改变默认操作。
- STORE 构建时分配批次 ID，由原 wire identity 与 scheduler 单调序号构成，附在 metadata envelope 中传给 worker；worker 回执保留同批次 ID，scheduler 先校验再交给原 manager。账本按原 raw request ID 只允许一个批次在途，完成后移除。
- 审计五个边界：scheduler built → worker bound → submit returned → worker receipt → scheduler receipt；要求每批恰好一次、身份/范围不变、时序正确、两侧失败标记一致。服务的 native terminal/原 reservation 审计仍独立执行，worker receipt 不替代真实传输终结证明。
- 默认对照 `HandoffObservedConnector` 与候选共享相同账本和 KV 探针；候选只额外经过关闭/零预算 bypass。这里比较的是相同所有权与观测栈上的默认策略，不是未修改的原生 Connector 性能基线。

`PREPARED` 的真实保护阶段尚不存在：本轮观察的是默认 manager 已产生的 metadata，不证明分配前保护或零步保护回滚。worker 底层仍按 raw request ID 提供完成数，envelope 在 worker 侧绑定当前唯一在途批次；对封装后的旧回执能够拒绝，但不能宣称解决任意底层旧回执重放、重连或多 worker 聚合。单 worker 之外明确拒绝。

## 工作负载与结果

三组分别为默认 EVICTION_AWARE、保护关闭、保护开启但预算为零。每组独立启动服务，使用完全相同的 96 条串行请求：冷/热请求 2 条、不同首 token 的长压力请求 12 条、224-token 输入且生成 1 token 的短尾流量 80 条、目标恢复/恢复热命中 2 条。长请求生成 32 tokens，固定 seed=42、temperature=0、ignore_eos。短尾流量推动默认策略处理 pending chunk，自身不足 256 tokens，不产生新的完整 STORE chunk。

没有人工 stream hold、回执延迟、policy deadline 调整或 reset API。短尾流量是固定实验输入的一部分，不是空闲 drain 修复；不能用这轮通过覆盖 10 月 1 日 r6 长延迟空闲 owner 残留。

| 验收项 | 默认 | 关闭保护 | 零预算 |
| --- | --- | --- | --- |
| 请求完成 | 96/96 | 96/96 | 96/96 |
| 默认 STORE 批次 | 14 | 14 | 14 |
| STORE 整 chunk 数 | 84 | 84 | 84 |
| 完整五边界交接/回执审计 | 通过 | 通过 | 通过 |
| 新增保护 STORE / pin | 0 / 0 | 0 / 0 | 0 / 0 |
| 恢复时外部命中 / GPU 命中 | 1536 / 0 tokens | 1536 / 0 tokens | 1536 / 0 tokens |
| 回载 KV 比较 | 6 chunk、216/216 层全等 | 同左 | 同左 |
| 原 reservation token | 12 个，均 RELEASED 一次 | 同左 | 同左 |
| native terminal | 15 | 15 | 15 |
| 最终 free blocks | 909 | 909 | 909 |

三组对应请求 payload 和生成文本全部一致，按前缀摘要、起止 token 和次数计算的默认 STORE 范围多重集完全一致。该对照不要求不同运行的 request ID、物理 block ID 或提交时间相同。每组在恢复前及恢复后、引擎仍运行时检查 job/controller/transfer/read/write/pin/store-batch/deferred-context/error 均归零；退出后 GPU 注册清空，关闭后原 token/terminal 和 BlockPool 审计通过。CPU 中保存的可复用 KV 对象不要求删除。

证据：[三组比较](protection-zero-evidence/protection-zero-r2/comparison.json)、[默认结果](protection-zero-evidence/protection-zero-r2/baseline/result.json)、[关闭结果](protection-zero-evidence/protection-zero-r2/disabled/result.json)、[零预算结果](protection-zero-evidence/protection-zero-r2/zero/result.json)。各目录保存请求、运行期状态、配置、原事件 JSONL、KV 摘要和日志。

## 测试与失败轮

新增 22 项测试覆盖非法预算、跨代单在途、失败回执、旧批次拒绝、真实 LMCache metadata 的序列化交接、重复回执、worker block 篡改、零预算 metadata 改写及审计缺边/重复/时序反例。服务器完整显式 CUDA 回归 **460 passed、115 subtests、133 warnings**，见 [日志](protection-zero-evidence/protection-zero-r2/full-regression.txt)。本地无 LMCache，19 项独立测试通过、1 个依赖模块跳过；真实库 3 项测试在服务器执行，不能以本地跳过算通过。

[r1 失败结果](protection-zero-evidence/protection-zero-r1/baseline/result.json) 保持 `passed=false`：长压力与短尾请求完成后，实验脚本把原始嵌套 `/status` 直接交给摘要断言，触发 `KeyError('l1_read_locked')`。原始状态已显示锁/job/transfer 归零，但还未执行回载和完整审计，不能算成功。改为复用既有 `cache_state()` 后，r2 从全新实例重跑三组通过；没有修改 r1。

## 复现

在现有固定环境和模型目录执行，输出目录必须全新，所有 GPU 运行串行：

```bash
cd /root/CachePilot
export PATH=/usr/local/miniconda3/envs/cachepilot/bin:/usr/local/cuda-13.0/bin:$PATH
python scripts/protection_zero_smoke.py --output artifacts/protection-zero-new
CACHEPILOT_RUN_CUDA_CONTRACT=1 python -m pytest -q
```

脚本自管三个独立服务实例，拒绝占用已有 8000/8080/5555 端口。`configs/protection-zero.json` 可由 `serve.sh protection-zero` 加载，但必须配套启用所有权 MP server；不能连普通 LMCache server。三组实验实际使用带同步 KV 探针的子类，最终配置保存在各实验目录。源码见 [执行文件 SHA256](protection-zero-evidence/protection-zero-r2/source-sha256.txt)，`.log` 归档仅改名 `.txt`，不提交 KV 二进制或模型文件。

这是每配置一次的正确性验收，不是三次性能重复。同步 KV 探针和逐批 JSONL 都有开销；不得据此给出延迟或吞吐改善。下一步仍须实现实际候选/hash 校验、分配前 pin、PREPARED 零步回滚及与默认 manager 的单次提交协调，再单独验证最多两个完整 chunk 的保护。
