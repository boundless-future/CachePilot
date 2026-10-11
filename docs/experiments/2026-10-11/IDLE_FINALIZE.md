# 可选空闲 pending STORE 收尾候选

日期：2026-10-11。接续 [owner 诊断](CAPACITY_OWNER_DIAGNOSIS.md)。本候选是新的策略变体，不修改既有 ProtectionConnector、安装包或容量性能统计。以下记录实现契约和验证进展。

## 接入与不变量

`IdleFinalizeConnector` 继承 ProtectionConnector，显式配置 `cachepilot.idle_finalize=true`。仅支持固定源码版本、同步 text-only、单卡 TP=PP=DP=1。实现位于 `idle_finalize.py`，不调用 block touch/free，不提交 STORE，不制造模型请求。

- 原 Scheduler.has_requests 判定无工作，且 requests、running、waiting、skipped_waiting、deferred_frees 都为空，才可能收尾。不能把暂停状态下的 unfinished=0 解释为真正空闲。
- manager in-flight、handoff pending、保护 prepared/inflight 任意非空时，全局跳过。
- 最多检查 256 个 pending raw ID（对应受控 service admission 上限）；所有条目必须 finished，条目的 wire identity 必须一致，并与每个 req_client 的当前 identity 完全相同。所有候选先校验再变更。
- 用真实 policy.drop_request 丢弃未提交项，沿用 dropped_on_request_drop 计数，并额外记录 idle 原因和 token 范围；manager 清 registry/token ledger 后向对应 wire identity 发 END。本地发出 END 不表示远端成功，GPU 验收仍要求 server owned_jobs/session/lock/transfer 归零。
- 保留 GPU prefix cache 内容、引用和 LRU 顺序。后续请求可能 GPU 命中，也可能在已驱逐后重算；不能保证 CPU 缓存覆盖不变。

## 首轮反例：立即丢弃

`idle-finalize-zero-20261011a` 沿用 2.5 GiB/68-request trace，空闲立即丢弃。请求与资源收尾通过，service_errors 为空，但 **H2D=0，result.passed=false**。短暂无请求窗口触发过早丢弃，后续回访失去外部覆盖。这是候选设计造成的反例，不是原策略表现，不放宽回载门槛，也不删除该轮。

## 第二候选：连续空闲宽限

为验证生命周期与缓存保留的取舍，下一诊断固定 `cachepilot.idle_finalize_grace_seconds=15`，不是从多个时间点挑最好结果，也不声称它是最佳参数。以 `idle-finalize-grace15-20261011a` 新目录运行，原失败归档不变。

固定版 EngineCoreProc._process_input_queue 在无工作时阻塞 input_queue.get。独立候选包装该队列的 consumer get：仅当有 pending 且本来准备无限阻塞时，改为至多等待宽限时长；新输入优先返回原 handler，取消这段等待。超时后在**同一个 EngineCore 调度线程**重新核查 has_work、队列为空与全部资源边界，然后丢弃/END。后台 producer 的 put 等操作仍转发原队列。没有 scheduler 后台清理线程，也不将 has_requests 强制改为 true 来维持空转。

宽限只保证一次无新输入的等待区间；utility/abort 等输入也会打断并重新等待，持续控制流量下不是绝对墙钟上界。超时后原循环至多经过一次空 step 再回到阻塞，pending 清空后恢复无限阻塞。shutdown 时不新丢弃。引擎源码 SHA 有检查；多进程/版本扩展必须重新审计此接入。

## GPU 诊断结果

四轮均使用 RTX 4090 24GB、Qwen3-4B、CUDA 13.0、同步单卡 TP=PP=DP=1 和同一 68-request trace；这些目录是生命周期诊断，未并入 1.5 GiB 容量性能均值。

| 轮次 | 配置 | 请求/错误 | idle drop | H2D | 资源终态 | 结论 |
| --- | --- | ---: | ---: | ---: | --- | --- |
| `idle-finalize-zero-20261011a` | 立即收尾 | 68/0 | 68 | 0 B | 归零 | 失败反例：过早丢弃破坏后续回载覆盖 |
| `idle-finalize-grace15-20261011a` | 15 s 宽限 | 68/0 | 1 | 1,509,949,440 B | 归零 | 通过局部生命周期/H2D 门槛 |
| `idle-finalize-zero-reentry-20261011a` | 15 s 宽限 + 同 trace 重入 | 68/0 | 2 | 1,509,949,440 B | 首轮、重入均归零 | 重入输出 hash 差异 5/68，token 数相同 |
| `idle-finalize-two-reentry-20261011a` | two-chunk + 15 s + 重入 | 68/0 | 0 | 1,811,939,328 B | 首轮、重入均归零 | 保护/回退兼容性通过；本轮未触发 idle drop |

15 秒宽限轮的 D2H 为 2,264,924,160 B；zero 重入轮为 2,717,908,992 B；two-chunk 轮为 3,019,898,880 B。所有首轮请求都满足 token 合约且无 service error。重复回放的输出 hash 差异保留：zero 重入 5/68、two 重入 6/68；两轮重复 completion token 均为 48/48，因此不能把回放当作 decode 完全确定性的证明。

`two-reentry` 的 `before-repeat.json` 和 `after-repeat.json` 均显示 `owned_jobs=0`、`owned_transfers=0`、`owned_read_pins=0`、`active_sessions=0`；zero 重入轮同样归零。`registered_gpu_ids` 在引擎仍存活时存在，属于正常 worker 注册，不作为运行期泄漏；引擎退出后的 `result.json` 显示注册清空。

审计汇总见 [`idle-finalize-audit-20261011.json`](capacity-evidence/idle-finalize-audit-20261011.json)。四个原始目录分别归档为 [`idle-finalize-zero-20261011a.tar.gz`](capacity-evidence/idle-finalize-zero-20261011a.tar.gz)、[`idle-finalize-grace15-20261011a.tar.gz`](capacity-evidence/idle-finalize-grace15-20261011a.tar.gz)、[`idle-finalize-zero-reentry-20261011a.tar.gz`](capacity-evidence/idle-finalize-zero-reentry-20261011a.tar.gz) 和 [`idle-finalize-two-reentry-20261011a.tar.gz`](capacity-evidence/idle-finalize-two-reentry-20261011a.tar.gz)。

## 验证门槛与剩余工作

已通过本地 17 项隔离契约，以及服务器真实 BlockPool/manager 三项：共享 GPU 引用和 LRU 不变、真实保护提交未回执时不丢弃其他 pending、旧 generation 结束后新 generation 不被误清。另有既有 idle pending/lifecycle 回归。新 GPU 候选必须完成原 token 合约、H2D 覆盖、保护覆盖（非零组）、默认/保护回执与 owned-service 终态；诊断输出差异照实保留。四轮 GPU 诊断结果见上节；服务器最终源码回归结果在本节持续记录。

真实 DMA fault、旧的 30 秒已提交回执空闲问题不由此候选自动解决。修复前的 2.5 GiB 扫描仍为失败，候选正确性验收不等于性能收益，也不能与历史 zero/one/two 均值直接混合。当前候选只在固定源码、单卡同步 text-only 服务上通过初步 GPU 验证；多进程/异步调度、共享前缀、取消/抢占、失败回执、多个 pending chunk 和更长空闲窗口仍需独立门槛。


## 最终源码回归与后续

本地隔离/诊断/审计测试 **39 passed、8 skipped**（缺少 LMCache/native 依赖的路径跳过）。服务器设置 `CACHEPILOT_RUN_CUDA_CONTRACT=1` 完整回归 **686 passed、115 subtests passed**，没有跳过测试；121 条依赖弃用警告保留于 [完整日志](capacity-evidence/idle-finalize-regression-20261011.txt)。新增队列契约覆盖 abort/shutdown/utility/new request 优先返回、显式 timeout/nonblocking 保持、超时后重新检查工作/输入和 END 异常显式传播；审计负例覆盖缺 terminal、提前解 pin、错 epoch、重复释放、缺 END/reclaim 和错误 drop 身份。

独立审计逐项核对首轮/重复轮输入与输出 token、原 reservation 身份恰好一次释放、native terminal 先于 read reservation 释放、job→END→reclaim 以及最终资源；首轮 metrics 与包含重复流量的 service 事件总数分开记录。立即收尾轮没有 transfer，不能从空集合审计推导传输正确性。四轮合计 408 个 trace 请求（另有各一次 warmup）。本次无新的逐层 KV 探针，不扩大已有 bitwise 证据的范围。

下一专项预注册见 [非零保护与 idle drop 共同覆盖](IDLE_FINALIZE_PREREGISTRATION.md)。在其覆盖门槛通过前，two/2 GiB 的 drop=0 只算兼容性证据。随后优先验证实际 raw ID 复用/取消与失败回执交互，现有 trace 重入使用新服务请求 ID，不能替代同 raw ID 跨代验证；多 worker/异步模式继续拒绝启用，无需为当前单卡项目放宽支持范围。

## 2.5 GiB 非零保护交互专项

按上述预注册新增 two/2.5 GiB/15 秒宽限＋重复 trace。a 在启动阶段因 SSH PATH 缺少 Conda bin，`serve.sh` 找不到 `vllm` 而失败，未发模型请求；[原始失败归档](capacity-evidence/idle-finalize-two-2p5g-reentry-20261011a.tar.gz) 保留。仅修正 PATH 后在 b 新目录重复同源码/trace/资源/门槛，没有增加压力或改变候选算法。

b 首轮及重入各 68 请求，输入/输出 token 合约、服务错误与资源门槛通过。首轮 H2D=1,660,944,384 B，D2H=2,189,426,688 B；全服务期 9 个真实保护提交及对应成功回执、288 次保护 pin 释放，峰值 32 block，2 次 idle drop。137 个 LOOKUP job（含 warmup）、280 个原 reservation 和 84 个原身份 transfer/native terminal 闭合，引擎退出后 GPU registration 清空；没有追加请求推动收尾。

两次 idle drop 分别为 `[0,1024)` 和 `[512,1024)`。**保护 wire identity 与被丢弃 wire identity 的交集为零**：该轮证明同一隔离服务运行中保护与 idle drop 共同覆盖，并未证明同一请求先保护保存、再丢弃其 pending 后缀。第二个目标 QUERY 返回两个已有缓存 chunk，没有该 identity 的 STORE，不从范围起点推断保护来源。

重入相对首轮输出 hash 有 8/68 差异，固定 token 不代表文本等价。本专项没有逐层 KV 探针，不宣称传输内容全等或性能收益。五个实际运行的候选诊断共 544 个 trace 请求，另有五个 warmup；启动失败 a 单列，不计作有效运行。专项通过后仍保留真实 raw ID 复用、共享前缀取消/失败回执交互及旧 30 秒延迟边界，尚未恢复 2.5 GiB 正式容量计时。

证据：[生命周期与覆盖审计](capacity-evidence/idle-finalize-two-2p5g-audit.json)、[保护引用审计](capacity-evidence/idle-finalize-two-2p5g-protection-audit.json)、[b 原始归档](capacity-evidence/idle-finalize-two-2p5g-reentry-20261011b.tar.gz)。归档下载逐文件 hash 与服务器核对；校验值见 [归档清单](capacity-evidence/idle-finalize-archive-manifest.json)。候选源文件、测试、审计入口及 runner 另存 [源码快照](capacity-evidence/idle-finalize-source-20261011.tar.gz)，与 Git 版本一起使用。

## 空闲收尾后的实际 raw ID 复用

新增显式诊断选项 `--reuse-request-ids`，两遍 68-request trace 使用同一组 API ID，并仅在隔离实例禁用 vLLM ID randomization。客户端检查 SSE response ID 是否保留指定值；正常 replay 不发送该字段，输入、到达与 token 合约不改变。

`idle-finalize-raw-id-20261011a` 的 two/2.5 GiB/15 秒宽限轮完成 136 请求。68 对实际 raw ID 分别对应不同 wire generation，服务事件逐项证明旧 reclaim 先于新 lookup。第一遍被空闲丢弃的 `cmpl-cp-idle-67-0` 在第二遍重新注册，没有继承旧终态。全服务 137 个 job、282 个原 reservation、91 个 transfer/native terminal 闭合，9 次保护提交/回执、峰值 32 block；资源归零。首轮 H2D=1,660,944,384 B，重复输出 hash 差异 8/68 保留。本轮证明已回收后复用，不覆盖旧 STORE 仍在途、旧回执迟到或实际 DMA 故障；无逐层 KV 探针。

审计：[raw ID 生命周期](capacity-evidence/idle-finalize-raw-id-audit.json)；[原始证据](capacity-evidence/idle-finalize-raw-id-20261011a.tar.gz)；[源文件与测试快照](capacity-evidence/idle-finalize-raw-id-source-20261011.tar.gz)。完整显式 CUDA 回归为 **703 passed、115 subtests**，含已准备的回执/取消组合配置测试；[测试日志](capacity-evidence/idle-finalize-raw-id-regression-20261011.txt)。新的客户端测试使用本地实际 SSE 服务器，验证默认 payload、两遍显式身份和错误响应 ID；没有把并发请求的到达顺序当作身份保证。
