# 长 prefill 准入试点预注册

2026-10-11，文件和实现于本轮启动前登记；21:37 部署，任何本轮 GPU 请求前固定。

## 研究问题

此前 1K 左右初始材料、48 decode token 的 reference 与压缩到达负载中，外部缓存虽减少 prefill，但整体 TTFT 没有优于 native。下一步增加上下文计算权重，先验证四组配置的可运行性和资源门槛。**今晚仅执行每组一次的 pilot；不报告均值/性能优劣，不启动 formal。** 正式重复将使用本 pilot 的相同源码/trace 签名另行执行。

## 固定干预

- 源 trace 沿用 unprotected reference 的原始未压缩到达输入，规范化 SHA 为 `015c2148973855fe2fa1e0cb7cef4ecb136f69ecf41fde2212b61bbb9e5acd6b`。
- 每个 session 前置固定的 3,072-token 材料（Qwen3-4B tokenizer 编码后截取再 decode，追加换行）；同 session 所有轮次使用相同前置文本，保留原 prompt 为后缀。实际拼接后 token 数重新编码记录，不声称恰好增加 3,072 tokens。
- 不改变 arrival_ms、48 decode tokens、种子、请求数、轮次和开放环实现。由于前置文本会改变 KV key、对齐和输入内容，这不是只改变一项 GPU 内部计算的纯机制实验。
- 维持 8K 上下文上限；任何事件 input+output 超限则在 GPU 启动前拒绝。保持 append-only 文本校验。
- 四组固定为 native、owned-immediate、h2.5/d64、h5.0/d1。Qwen3-4B/BF16、4090、同步 eager、2 GiB GPU KV、缓存组 16 GiB CPU KV，lazy 15 秒 idle grace，max_num_seqs=4。

## 停止和证据

复用现有 reference 的全部请求/token/H2D/资源/关闭日志门槛，native 不适用项仍不适用。任一失败立即停止本矩阵，不加压、不换参数、不追加请求、不重跑筛选。21:50 后不启动新 cell。完整四格通过才具备后续 formal 准入；部分矩阵不计算均值。pilot 结果始终只作准入，不据其排名重新选固定参数。

新增 workload 选项在压缩到达 formal 完成并归档后才部署。压缩到达历史源码包已保存；修改后的脚本不得续跑其旧 pilot/formal。新的源码集合、完整 trace 与干预参数写入长 prefill plan。
