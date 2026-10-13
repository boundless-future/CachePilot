# 全请求包络：抢占与精确容量边界

本轮新增18项真实 Scheduler 元数据契约，未修改运行时策略。候选仍仅供 CPU 研究，`WholeStepProtection.bind` 继续拒绝服务接线。原匿名结构 pilot 的 3/4、零覆盖失败及尚无稳定收益结论不变。

## 已验证路径

| 场景 | 数量 | 实际检查 |
| --- | --- | --- |
| 共享 owner 保留/离开 × 本地/混合外部命中 × 0/16/32 引用 | 12 | watermark=7；保护引用占用后 free 恰好等于 reserve；多一引用时 policy 拒绝；两个请求均按真实 Scheduler 准入，外部零计算路径保留原生 reservation |
| 真实抢占钩子连续三次重入 × 0/16/32 引用 | 3 | 在途旧结果存在时 reserve=None；真实 update_from_output 消化旧结果后才允许估算；重入与新请求共同准入，最终资源闭合 |
| 自然 decode 容量不足抢占 × 0/16/32 引用 | 3 | 64-token prefill 用满剩余4块，合成首个输出 token 后 decode 需要第5块；schedule 自行抢占；连续3次容量不足仍不准入；释放压力后通过本地64-token命中只计算1 token恢复 |

共享场景将完整池压到精确候选阈值，不再只是大余量下的通过测试。多一引用的拒绝检查发生在纯 policy gate，拒绝后没有实际增加引用；不声称这是完整 Connector 的拒绝路径验证。

自然抢占通过 `schedule()` 触发，不手工修改 running 列表或请求状态；另一组显式调用 `_preempt_request` 的测试用于覆盖旧结果在途，二者分别记账。额外0/16/32占用仍是真实池引用，并非真实 STORE/DMA。模型配置为固定 Qwen3-4B，执行输出、外部命中与无约束结构化输出接受回调均为测试构件，未执行模型 worker。

## 失败及纠正

首次连续重入测试3项失败：测试错误地要求抢占后立刻得到 reserve。固定 vLLM 保留 `num_in_flight_tokens`，候选按设计返回未知；没有放宽策略。测试现先确认未知，再通过真实 `update_from_output` 消化旧输出，检查 in-flight/stale 计数归零后重入。原日志 `initial-failure.txt` 保留。

自然抢占首次3项失败发生在输出处理的 `accept_tokens`：原元数据 fixture 的空 SimpleNamespace 未实现该接口。该场景显式提供无约束接受回调后通过，原日志 `natural-fixture-failure.txt.gz` 保留。没有更改上游 Scheduler 或 CachePilot 运行时。

## 验证与门槛

定向整个 Scheduler 文件 **36 passed**，包含本轮18项及原18项。固定服务器隔离目录 `/root/cachepilot-whole-step-20261013`，测试源码与本地 SHA256 核对；全量强制 native/CUDA **1218 passed、零跳过、115 subtests、121 warnings**。Windows **688 passed、313 skipped、26 subtests**。证据位于 `docs/validation/whole-step-boundaries-2026-10-13/`。这些数目不是模型端到端或性能实验数目。

当前矩阵未找到已覆盖路径上的包络反例，但没有证明所有调度路径的上界，不能据此直接打开服务绑定。下一步先覆盖远端完成/失败后的连续恢复、带 decode 的多请求跨步状态及跨步保护引用释放；再增加独立 Connector/config/source gate，执行 GPU KV/资源/代际准入，全部通过后才冻结新 pilot。完整生产恢复、异步/多 worker 和稳定收益仍开放。
