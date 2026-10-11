# 到达时间压缩对照预注册

2026-10-11，北京时间约 21:00；本轮任何 GPU cell 启动前冻结。

## 问题与范围

在相同请求内容、上下文和 decode token 预算下，将到达计划时间压缩一半后，原生、立即卸载、默认固定策略、开发集选中固定策略之间的趋势是否保持？这是一项探索性负载敏感性研究，不是独立真实 Agent 验证，不包含保护候选，也不补写此前失败的保护 validation。

仅在四组 unprotected reference formal 十二格全部通过独立审计后启动。固定四组和参数：native、owned-immediate、h2.5/d64、h5.0/d1；沿用 reference 的 Qwen3-4B/BF16、4090、同步 eager、2 GiB GPU KV、缓存组 16 GiB CPU KV、lazy 15 秒 idle grace。禁止根据本轮结果重新选择参数。

## 唯一干预

源 trace 规范化 SHA256 为 `015c2148973855fe2fa1e0cb7cef4ecb136f69ecf41fde2212b61bbb9e5acd6b`。仅把每个 event 的 `arrival_ms` 乘以 0.5，同时更改 trace_id 后缀；保持全部输入、event/session 身份、48 decode tokens、种子、请求顺序、think_ms 和回放实现不变。开放环不使用 think_ms 做依赖等待。新的 trace SHA、源文件清单在 plan 中固定。

这会同时改变负载重叠、队列和实际缓存驻留时间；只声称到达计划干预，不能声称隔离了某个 DMA 或调度内部机制，也不把它叫作稳定 decode 或真实因果 Agent 闭环。

## 执行和门槛

- pilot 四格通过独立资源、配置、原始时间戳/token/hash 审计后，才执行 formal 十二格，每配置三次，轮转偏移 0/3/2。pilot 不并入均值。
- 复用 reference 的 HTTP/SSE、H2D、owner/job/session/temporary/锁回收、GPU unregister、日志精确分类及无保护提交门槛。native 不适用项仍为不适用。
- 任一失败即停止当前矩阵，保留部分结果，不追加压力、不换参数、不重跑筛选、不报告部分均值。
- 21:50 后不启动新 cell；若不足以完成正式矩阵则记录未完成，不为赶截止放宽门槛。
- 报告每重复及均值/样本 SD：TTFT P95、入口等待、ready-to-end、会话跨度、prefill、GPU/external hits、H2D/D2H、队列、输出 hash 差异和退出错误。n=3 不作显著性结论。
- 该矩阵与未压缩 reference 单独聚合；跨矩阵差异是探索性观察，不混合原始请求扩大样本数。
