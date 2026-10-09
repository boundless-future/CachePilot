# 3C 小预算保护：首个真实 GPU 正常路径

2026-10-09：**最多两个完整 chunk 的分配前保护已实现，正常路径和零步回滚通过真实单卡验证。** 这不是 3C 整体验收，也没有性能收益结论。新增路径的取消、自然抢占、STORE 失败及迟到回执目前主要有真实库 CPU 契约覆盖，仍需 GPU 专项。

## 固定范围与实现

沿用 RTX 4090 24GB、Qwen3-4B BF16、vLLM 0.30.0、CUDA 13.0、固定 LMCache 提交；GPU KV 2 GiB、CPU L1 16 GiB、无 L2。eager、TP=1、单 full-attention group、16-token block / 256-token chunk。本轮两组都显式 `--no-async-scheduling`；这只关闭模型异步调度，LMCache 的异步 LOOKUP/回载仍存在。与前一零预算报告的默认异步调度范围不同，不能直接横比耗时。

`bounded_protection.py` 在 scheduler owner 的 `schedule()` 之前做候选选择、两条 hash 链校验和 `BlockPool.touch()`。独立 allocator 包装器只观察真实分配结果，断言 PREPARED block 不会被作为新 block 分配，没有把 CPU harness 安装到分配回调。固定 scheduler/allocator 文件 SHA256 不符时拒绝启动。

首版刻意只接受以下候选：请求已结束且不在 scheduler 活请求表，所有 block 的引用数为 0，pending 队首整项从 token 0 开始，终点为 256 或 512，没有 salt、跳过前缀或混合 group。暂不拆分较大 STORE，不猜测已保存后缀；不支持 LoRA、speculation 或模型异步调度。预算为所有 PREPARED 与已提交保护批次合计最多 32 block，不是每请求各两个 chunk。默认 manager 自己的 STORE pin 不计入此额外预算。

准入采用非常保守的余量：对 **全部** 活请求，按 `ceil((max(num_tokens_with_spec, num_computed_tokens)+1)/16)` 计算完整 footprint，保留两倍总和、watermark 和额外 16 block。既不扣除 resident/local hit，也不根据 LOOKUP 是否 resolved 推断物理需求；第二份 footprint 给原生 async-load admission reservation 留余量。这个限定版检查可能大量回退，不能当成准确需求预测器或性能策略。未来若放宽运行范围必须重新验证。

生命周期为 PREPARED → metadata committed → worker bound/submit returned → 原 worker/scheduler receipt。只有有 token 的步才移交 pin 给原 `LazyOffloadManager` registry；先注册同 raw ID 的在途批次、再移除队首候选，随后默认 drain 自动排除整个 raw ID，避免重复 STORE。已 committed 的引用不能因取消、异常或超时释放，只由原 manager 收到原回执后释放；交接失败而终结未知时停止实例，不尝试强制回收。

零 token 步只回滚 PREPARED。回滚后仍无其他引用的块按原相对顺序放到 free queue 前部，避免 `free_blocks()` 的默认队尾插入反复刷新 LRU。**这不是精确恢复整条旧 LRU 队列**，它会保守地提前淘汰候选；本步其他分配/命中及新增引用保持有效。该语义及代价须保留在未来消融里。

## r3 工作负载与结果

零预算、两 chunk 预算分别启动独立实例，每组 348 个请求：576-token 目标冷/热请求、12 个不同前缀压力请求、160 个 224-token 短尾请求、目标恢复/热请求、12 个原压力前缀回放、160 个固定收尾请求。普通请求生成 8 tokens，短尾生成 1 token；temperature=0、seed=42。两组 payload 和文本完全一致。短尾流量是实验输入，不是空闲 drain 修复。

| 验收项 | 零预算 | 两 chunk |
| --- | --- | --- |
| 请求 | 348/348 | 348/348 |
| 默认 manager STORE / 保护 STORE | 14 / 0 | 5 / 9 |
| 总 STORE metadata / 整 chunk | 14 / 28 | 14 / 28 |
| 保护峰值 | 0 | 32 block（72 MiB KV） |
| PREPARED / 零步回滚 | 0 / 0 | 28 / 19 |
| 持有 PREPARED 时的实际分配审计 | 无 | 9 次，未返回被保护 block |
| 目标恢复外部命中 / GPU 命中 | 512 / 0 tokens | 512 / 0 tokens |
| 全部回载 KV 比较 | 20 chunk，720/720 层全等 | 24 chunk，864/864 层全等 |
| 原 reservation token | 22，逐个 RELEASED 一次 | 26，逐个 RELEASED 一次 |
| actual native terminal | 24 | 26 |
| 最终 free blocks | 909 | 909 |

`added_protection_stores=9` 表示由保护分支提交的批次数，**不等于相对对照净增加 9 个 STORE**。总 metadata 数相同；同 token 前缀跨不同请求再次 STORE 也可能被 LMCache 已存在对象去重。累计 896 次 block pin 获取包含 19 次回滚，每一时刻实际保护不超过 32 block。

两组在目标恢复前和最终、引擎仍运行时检查 session/job/controller/transfer/read/write/store-batch/deferred-context/error 全部清零，退出后 GPU 注册清空。原始事件验证原 reservation 恰好处置一次、真实 native terminal 先于释放；最终 BlockPool 909 free、tracked ref/deferred free 为空。

关闭阶段仍有日志异常：两组均出现 CUDA IPC producer 退出提示，零预算组在主动 SIGTERM、EngineCore 完成请求处理与资源 teardown 后，API output handler 另报 `EngineDeadError`。这些发生在所有请求完成和运行期资源检查之后；退出后 GPU 注册清空，但本报告不声称关闭日志无异常，亦未定位该退出提示的根因。原日志完整保留。

严格来源审计另确认 **7/9 个保护批次、14 chunk、504 层**：worker 原保护 envelope → STORE 前 KV → 同 wire identity 的真实 native STORE 提交/terminal → 无中间 STORE → 后续回载相同 SHA256 与 bitwise 内容。剩余两批生命周期闭合，但不声称有同等严格的回载来源覆盖。所有保护范围只匹配摘要的宽松交集不能替代此证明。见 [严格来源结果](protection-two-evidence/protection-two-r3/protected-kv-provenance.json)。

第一目标恢复后的实际 staging DMA 累计：两组 H2D 都为 72 MiB，零预算 D2H 720 MiB、保护组 864 MiB。这是 **回放压力前缀之前** 的检查点，不是全轮 DMA 总量。全轮保护组 native 提交布局折算逻辑 STORE 1008 MiB、RETRIEVE 864 MiB，不能把逻辑 payload 冒充 DMA 计数。增加保存/回载覆盖也不能直接推出性能收益。

证据：[比较](protection-two-evidence/protection-two-r3/comparison.json)、[零预算](protection-two-evidence/protection-two-r3/zero/result.json)、[两 chunk](protection-two-evidence/protection-two-r3/two/result.json)。原事件、KV 摘要、请求、状态和日志一并保留；没有保存 CUDA tensor 或模型。

## 失败轮与修复

- **r1 失败**：保护组已有 3 个实际保护提交，18 次 PREPARED 中 15 次零步回滚；最终 free=909、读写锁和 transfer 归零，但 8 个 pending STORE、8 个 owner/session 保留。新增回滚路径调用 `free_blocks()` 把 hashed block 放回 LRU 队尾，反复 LOOKUP 零步刷新了淘汰年龄。这是本项目新增设计带来的问题，不能归因于既有 r6，也不能用关闭时回收算通过。修复为未引用块恢复到队首；增加连续 20 次零步和本步新增外部引用的回归。
- **r2 失败**：回滚修复后运行期资源已归零，但目标仍命中 GPU 512 tokens、外部命中 0。原 80 个短尾不足以让新 LRU 行为下的目标离开 GPU，因此未满足实际 H2D 验证条件。r2 不算通过；两组固定短尾统一改为 160，并回放全部压力前缀，r3 从全新实例通过。没有 reset cache，也没有为单独一组追加自适应流量。
- 初始 CPU fixture 给只读 `block_hash` 直接赋值、以及把无 hash filler 误认为 FIFO 的两次测试失败已纠正：改用真实 Request + `cache_full_blocks` 建立 hash 和队列。没有修改上游安装库。

## 回归与复现

新增真实 BlockPool/manager 契约 18 项，审计反例 14 项，覆盖单/双 chunk、预算不足、引用、hash/前缀变化、分配排除、连续零步、默认 drain 防重、失败删除 suffix、跨代延迟回执以及来源证明反例。保护 connector/旧回执原有测试继续执行。完整显式 CUDA 回归 **487 passed、115 subtests、121 warnings**，见 [日志](protection-two-evidence/protection-two-r3/full-regression.txt)；此后新增严格来源审计 5 项独立测试另行通过，不属于前述 487 项或 r3 GPU 运行。严格来源审计以 r3 原始事件离线执行，后续复现脚本也会自动要求该审计通过。

收口时对包含严格来源审计的最终代码重新执行完整 CUDA 回归：**492 passed、115 subtests、121 warnings**，见 [最终回归日志](protection-two-evidence/protection-two-r3/full-regression-final.txt)。未重复宣称为新的模型服务实验。

```bash
cd /root/CachePilot
export PATH=/usr/local/miniconda3/envs/cachepilot/bin:/usr/local/cuda-13.0/bin:$PATH
python scripts/protection_zero_smoke.py --output artifacts/protection-two-new --bounded-workload --modes zero two
python scripts/audit_bounded_kv.py artifacts/protection-two-new/two --output artifacts/protection-two-new/protected-kv-provenance.json
CACHEPILOT_RUN_CUDA_CONTRACT=1 python -m pytest -q
```

预算为零不安装新调度入口；非零需要同一 owned MP server，配置默认仍为零。复现程序拒绝占用已有端口，GPU 服务串行，运行期间不得同步源码。`.log` 仅改名 `.txt` 归档。

## 下一步及保留限制

1. 在此新增路径上做 GPU 专项：保护 STORE 的失败回执、取消/自然抢占、延迟与重复 receipt、预算回退；CPU 契约与 3B 老路径实机证据不能替代。
2. 若上述通过，再设计较长 pending op 的 chunk 前缀拆分与已保存前缀账本；当前 >512-token 整项直接回退，不能把本轮称为通用多轮 Agent 策略。
3. 然后关闭同步 KV 探针/逐步日志，做同配置多轮消融和至少三次重复，报告 TTFT、实际 prefill、排队、实际 DMA、保护占用与策略 CPU 开销；保留退化结果。

旧 30 秒延迟 r6 的空闲 owner 收尾、永久 I/O 自动恢复、driver fault/硬件 DMA 中断、多 GPU、异步 reset API、compile-only 输出差异根因仍开放。本次修的是新增 PREPARED 回滚引起的 LRU 刷新，没有修复旧 r6，也不扩大生产安全结论。
