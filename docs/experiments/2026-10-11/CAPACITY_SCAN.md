# 保护预算容量扫描

日期：2026-10-11。遵循 [预注册方案](CAPACITY_SCAN_PREREGISTRATION.md)，固定策略、`validation-20261011a` 输入及到达、模型和服务配置，只改变 GPU KV 池容量。1.5 GiB 试点三格及正式九格通过；2.5 GiB 首格 zero 因 owned job/session 未闭合而停止，未运行其 one/two 和正式重复。试点与正式重复分目录保存，不能混合计入性能均值。

## 1.5 GiB 试点

目录 `capacity-pilot-20261011a`，zero → one → two 各一次。三格均完成 68/68 请求及输出 token 合约，H2D > 0，运行期/退出后资源回收，`service_errors=[]`。one/two 的 protection commit/receipt 均为 1/1，峰值 pin 分别为 16/32 个 block。可行性门槛通过，允许进入新目录内的三次正式重复。

| 模式 | TTFT P95 (ms) | 实际 prefill token | H2D (B) | D2H (B) | 保护提交/回执 | capacity / inflight 回退 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| zero | 8009.63 | 38164 | 4378853376 | 2868903936 | 0/0 | 0/0 |
| one | 7720.04 | 38164 | 4378853376 | 3019898880 | 1/1 | 18/4 |
| two | 7761.93 | 38260 | 5209325568 | 3019898880 | 1/1 | 20/4 |

two-r0 有一次八行 vLLM 主动关闭后的 output-handler traceback，被既定门槛单独记为 `shutdown_teardown_errors`；不是零 ERROR 日志，也没有修改门槛。one/two 相对 zero 的输出 hash 分别有 2/68、7/68 不同。本轮没有逐层 KV 探针；固定输入与 token 合约不代表 decode 相同。试点的时间和搬运差异不作为性能收益结论。

## 1.5 GiB 正式重复

正式目录为 `capacity-formal-1p5g-20261011a`，zero/one/two 各三次、612 个请求全部通过 token、H2D、资源和日志门槛。相对 zero 的 TTFT P95 均值：one `-5.09%`，two `-3.99%`；会话完成 P95 分别 `-0.23%`、`-0.40%`。同时 H2D 均值分别 `+8.92%`、`+6.49%`，one/two 的保护 wall 均约 56–57 ms。该 n=3 结果只说明固定 trace 上的一次容量对照，不能作显著性或生产默认结论。

| 预算 | TTFT P95 均值 ± 样本标准差 (ms) | r0 / r1 / r2 (ms) | 实际 prefill 均值 | H2D / D2H 均值 (GiB) | 会话完成 P95 均值 (ms) |
| --- | ---: | --- | ---: | ---: | ---: |
| zero | 8015.34 ± 53.63 | 8077.11 / 7980.65 / 7988.25 | 38196.00 | 4.336 / 2.672 | 31448.35 |
| one | 7607.74 ± 344.86 | 7541.89 / 7980.78 / 7300.56 | 37518.67 | 4.723 / 2.672 | 31375.72 |
| two | 7695.17 ± 68.75 | 7626.54 / 7764.04 / 7694.93 | 37865.33 | 4.617 / 2.742 | 31324.09 |

one 的第二次 TTFT P95 比同 repeat zero 高约 0.12 ms，其余两次更低；two 三次均低于同 repeat zero。one 波动明显大于 zero。实际 prefill 均值分别低 1.77%/0.87%，但 H2D 增多；不把本次延迟差异唯一归因为减少重算。保护 wall 包含计数 instrumentation，是方法总耗时，不是 GPU DMA 或每请求关键路径。ready-to-first P95 均值为 zero 8015.82、one 7608.35、two 7696.09 ms，未把客户端入口等待隐藏。

九格无服务错误，也无单列的 shutdown traceback；保护 commit/receipt 按 r0/r1/r2 为 one 1/1、1/1、2/2，two 均为 1/1。峰值 pin/inflight 为 one 16、two 32 block；均在退出前闭合。zero 从未新增保护 pin/STORE。

输出 hash 失配相对同 repeat zero：one 为 8/7/2，two 为 9/7/9；zero 为 0。没有逐层 KV 探针，decode 不保证逐请求文本相同，失配和限制必须保留。

## 2.5 GiB 试点

目录 `capacity-pilot-2p5g-20261011a/zero-r0` 的 `passed=false` 原样保留。68/68 请求完成 token 合约，H2D 为 1,358,954,496 B，服务错误与关闭 traceback 均为空；保护提交、回执与峰值 pin 都为零，默认 STORE 的 worker/scheduler 回执均为 48。

45 秒运行期 drain 超时后，`active_sessions=1`、`owned_jobs=1`；引擎退出并解除 GPU registration 后，两项仍为 1。读写锁、temporary、prefetch job/controller result、owned transfer、read pin、STORE batch 和保护在途数全部为零。这是当前时限内的所有权/会话终态失败，不是 GPU 传输仍在执行的证据，也没有观察长期 TTL 后是否回收。最终隔离服务进程已退出，GPU 显存回到 1 MiB。

遵循停止规则，未继续 one/two，也未启动 2.5 GiB 正式重复。统计器将该 cell 排除，`aggregates=[]`。本容量不提供性能对比结论，不能用其请求成功或 TTFT 覆盖资源失败。

该失败发生在 zero 预算、没有保护 pin/STORE 的配置，因此不是非零保护动作造成失败的直接证据；但 zero 仍使用本项目 ProtectionConnector/owned-service 栈，不能据此断言原生 LMCache 有相同问题。计时路径已关闭逐事件日志，当前快照不足以确定残留 job 的具体请求、QUERY/END 顺序或根因，也不能直接等同于历史 30 秒迟到回执空闲 owner 问题。

## 结论与后续

本次共完成 13 个 cell、884 个请求，其中 1.5 GiB 12 格通过，2.5 GiB 一格资源审计失败。固定策略在 1.5 GiB 有局部延迟改善并增加 H2D，而先前 2 GiB 同输入的均值变化仅为 one -0.57%、two +0.31%；没有跨容量稳定收益证据，不设置生产默认预算，也不升级卡。

下一步先开展**独立诊断**：保持 2.5 GiB 原失败归档与门槛不变，在新的诊断目录记录 job/request 身份、LOOKUP/QUERY/END、terminal 与 idle/shutdown 时序，确定这 1 个 owner 的来源；若需要增强诊断，先做相应契约测试。不能把诊断轮并入当前计时或把修复后运行改称原扫描通过。定位后再决定修复和回归范围。之后补同所有权栈的立即卸载、固定 horizon/提交上限对照及新的到达负载，避免只与未经调优的 zero 比较。

## 证据

- [1.5 GiB 试点统计](capacity-evidence/capacity-pilot-20261011a-analysis.json)。
- [1.5 GiB 试点原始归档](capacity-evidence/capacity-pilot-20261011a.tar.gz)：trace、manifest、请求、日志、关闭上下文和资源审计。SHA256 `107ae325d0f0b7c1e21e9ac6e38131a6259c87f87b05903d3e93d4128e84f2f1`，本地与服务器相同。
- [1.5 GiB 正式统计](capacity-evidence/capacity-formal-1p5g-20261011a-analysis.json)。
- [1.5 GiB 正式原始归档](capacity-evidence/capacity-formal-1p5g-20261011a.tar.gz)：SHA256 `82c52554c7f90d7ccaa919a5d62670322743e3ddd49af9525339024bf32c5a7e`。
- [2.5 GiB 失败统计](capacity-evidence/capacity-pilot-2p5g-20261011a-analysis.json) 与 [原始归档](capacity-evidence/capacity-pilot-2p5g-20261011a.tar.gz)：SHA256 `bceb6cc5861693deb087c27c3a86136e6829f1c5e49ac2bc02560b606c45b52b`。
- [跨归档输入/源码审计](capacity-evidence/capacity-input-source-audit.json)：含已有 2 GiB 验证的 22 个 cell、160 个源码/配置条目完全一致，trace 与 prompt hash 均通过。三个新归档 SHA256 均与服务器一致。

三格 manifest trace SHA 均为 `079c9507c9585a5e5397affc8750c42603ee7efeeca3127d1cafafc2b1c54eb5`，与已有 2 GiB 验证输入相同。已有 [2 GiB 结果](PROTECTION_ABLATION_VALIDATION.md) 属于此前运行时段；跨容量比较还受到运行时段影响。

实际运行命令（只列 runner；统计命令为 `python scripts/summarize_protection_ablation.py <目录> --output <统计文件>`）：

```bash
python scripts/run_protection_ablation.py --output artifacts/capacity-pilot-20261011a --repeats 1 --modes zero one two --workload-label validation-20261011a --kv-cache-bytes 1610612736
python scripts/run_protection_ablation.py --output artifacts/capacity-formal-1p5g-20261011a --repeats 3 --modes zero one two --workload-label validation-20261011a --kv-cache-bytes 1610612736
python scripts/run_protection_ablation.py --output artifacts/capacity-pilot-2p5g-20261011a --repeats 1 --modes zero one two --workload-label validation-20261011a --kv-cache-bytes 2684354560
```

本轮未改运行代码，未重跑既有完整 CUDA 单元测试；验证为实际 GPU cells、原始日志/资源/token 复算及归档 hash 检查。
