# 无保护参考配置独立对照预注册

2026-10-11 20:36（北京时间），任何本轮 GPU cell 启动前登记。

## 为什么独立立项

原固定参数 validation 的 `protect-one-r0` 没有触发保护，按原覆盖规则失败。原矩阵停止且永久保留，原六组 reference 前置未满足，本实验不续跑原矩阵，也不纳入 one/two。这里回答独立问题：不启用保护时，合理调参的 lazy-offload 相比原生 vLLM 和立即卸载是否值得使用？

这属于观察过验证失败后的探索性后续研究，不称为预先未见的验证。配置和选择固定，不据本轮数据重新调参，不混入已有 validation 前两格。

## 固定配置

- 四组顺序：`native`、`owned-immediate`、默认 `h2p5-d64`、开发 formal 已选中 `h5p0-d1`。后两组预算零；没有保护候选。
- 使用 `fixed-baseline-inputs-20261011a/validation.json`：规范化 SHA256 `015c2148973855fe2fa1e0cb7cef4ecb136f69ecf41fde2212b61bbb9e5acd6b`，68 请求、75,808 prompt tokens、每请求 48 decode tokens。
- Qwen3-4B/BF16、4090 24GB、单卡同步 eager；GPU KV 2 GiB、缓存组 CPU KV 16 GiB、max_num_seqs=4、max_model_len=8192；不改安装包与策略源码。
- lazy 组使用已有 IdleFinalizeConnector、15 秒 grace；立即卸载使用 OwnedServiceConnector；原生无 MP 服务。全部同 warmup、客户端测量与服务关闭流程。
- 这是整体配置对照，包含 Connector/owned-service 开销，不是纯策略或原生 LMCache 性能对照。

## 门槛和顺序

1. 新增 `--scope unprotected` 显式区分原 reference 全矩阵。pilot 四格串行；全部通过后才 formal 十二格，各三次，轮转偏移 0/3/2。
2. 采用固定 formal 的六点选择结果，仅允许原始已有源文件 hash 一致；本轮新增 runner/auditor 自行冻结，新矩阵内不能改动 scripts/configs。记录来源清单；不要求本轮源文件集合与旧 formal 完全相同。
3. 全部要求 HTTP/SSE、event_id 唯一、实际 prompt/completion tokens、trace/source/配置签名、原始时间戳复算、正常退出和无运行期 fatal error。精确分类的已知退出 traceback 原样保存。
4. 缓存组必须 H2D>0、全部 owner/job/controller/pin/transfer/session/temporary/锁归零、GPU unregister。lazy 组保护提交/回执都应为零、shutdown_closed 通过。原生的 CPU KV/H2D 等为不适用，不伪造通过值。
5. 任何失败即停止当前矩阵；不换 trace、不加压力、不放宽规则。部分矩阵无性能聚合；pilot 单独保留，不进入 formal 均值。
6. 不晚于 21:50 启动新 cell，22:00 收尾。报告所有点 TTFT/入口等待/端到端/会话跨度、prefill、H2D/D2H、排队、逐重复、输出 hash 差异与退出错误。n=3 不代表显著性，输出差异不单独证明 KV 错误。

原六组 scope=all 仍要求原 validation 完整通过，当前不能执行。不得因本四组通过而更新原 validation 状态。
