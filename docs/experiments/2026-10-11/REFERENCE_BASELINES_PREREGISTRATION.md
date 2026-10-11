# 原生 vLLM 与同所有权栈立即卸载的同期对照

2026-10-11。此方案在 reference GPU pilot 前保存；只有固定六点 formal 与新文本 validation 完成并审计后才同步新代码、冻结新一轮源码。不同矩阵均值不混合。

## 问题与边界

保护策略除与调优后的 lazy-offload 比较，还必须回答：相同资源/请求预算下，原生 vLLM 和立即卸载是否已经足够快？当前 `configs/owned-service.json` 使用已验证的 OwnedServiceConnector 默认立即卸载通路；不为这一轮新增生命周期机制。

原生组无 LMCache；立即卸载组共用已有 OwnedServiceConnector/Owned MP 服务；固定与保护组仍用 IdleFinalizeConnector/Owned MP 服务和 15 秒宽限。所有缓存组的所有权数据通路相同，但立即卸载不经过保护 envelope/idle lazy 收尾。故本轮是**整体配置对照**，不是剥离所有 adapter 开销的纯策略因果消融。

## 固定输入与配置

- Qwen3-4B/BF16、RTX 4090 24GB、单卡、同步 eager，max_num_seqs=4，max_model_len=8192。
- 各组 GPU KV 2 GiB；缓存组 CPU KV 16 GiB。新实例启动，warmup 低于一个 chunk；不调用 cache reset，不追加 drain 流量。
- 使用预先冻结的 `fixed-baseline-inputs-20261011a/validation.json`：68 请求、75,808 prompt tokens，每请求 48 decode tokens。相同输入已用于文本验证，本轮不得称为第二套未见文本。
- 配置顺序：native、owned-immediate、默认固定 h2.5/d64、在六点开发 formal 上已选中的 fixed（若与默认相同则去重）、protect-one、protect-two。候选仍 h2.5/d64；不再次选参。
- pilot 各一次；全部运行门槛通过后 formal 各三次，repeat 顺序按三个位置轮转。pilot 不进入正式均值；任一运行失败停止当前矩阵。

## 运行门槛与解释

全部组要求完整 HTTP/SSE、唯一 event_id、实际 prompt/completion token、原始时间戳复算、输入与源码签名。全部服务正常关闭；运行期服务错误为失败，已冻结的 output-handler 关闭竞态仍按精确时序分类并保留。

缓存组另要求 H2D>0、运行期及退出后所有 owner/controller/锁/session/temporary/transfer 资源归零、GPU unregister。固定/保护组沿用原 protection summary/回执门槛，保护候选需有实际提交。原生组不存在 CPU KV、owner 或 H2D，不伪造零快照来声称这些门槛“通过”；适用项明确为无此机制。

立即卸载组不要求保护 summary；它没有保护 pin，STORE/RETRIEVE 使用已验证的原生 worker 与 owned-service 路径。本轮性能日志关闭逐层 KV probe，因此输出 hash 差异保留且不作 bitwise 等价断言。既有 OwnedServiceConnector 正常回载/自然抢占门槛见 2026-10-01/OWNED_SERVICE.md，不扩大为未测试故障覆盖。

21:50 后不启动新 cell，22:00 收尾。未完成矩阵只归档逐格数据，不能用部分格选优或发布完整均值。报告 TTFT/入口等待/会话完成、prefill、H2D/D2H、保护成本、所有重复和退化点；n=3 不代表显著性。
