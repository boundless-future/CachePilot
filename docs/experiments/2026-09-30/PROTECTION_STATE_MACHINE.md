# 3C：独立保护状态机首版

## 已实现范围

[protection_state_machine.py](../../../../scripts/protection_state_machine.py) 是单 scheduler-owner 的纯 Python 参考模型，配 [fake worker 场景](protection-scenarios.json) 和 [测试](../../../../tests/test_protection_state_machine.py)。它不导入 vLLM/LMCache，不改默认 GPU 调度，不代表性能收益。

流程是 `arrive → prepare → submit → terminal receipt → unpin`：

- `prepare` 接收本步需求预算、保护预算、在途批次数和保留空闲余量。预算不足或零 token 步直接回退，不等待、不 pin。
- 候选必须从该 generation 已确认保存的 prefix 末端开始，逐块核对物理 ID、版本和给定 prefix hash，遇到失效或预算边界只选连续前缀。
- pin 后 allocator 压力不能覆盖这些块。提交只产生一次不可变 `StoreAction`，同 generation 一次在途；共享块保留各 batch 的引用计数。
- 取消未提交批次立即 unpin；已提交批次只标 orphan，等待真实终结回执才能释放。reset、request-id 重用同理。
- 回执通过永不复用的 batch token 定位。旧回执不会更新新 generation；重复回执无操作；部分成功只推进连续 saved prefix，失败不推进。
- 不明超时/失联不直接释放在途 pin，`pending()` 保留可观测状态；模型不承诺永久失败下的活性。

## 验证

17 项测试覆盖预算回退、zero-token、hash/version 失效、前缀缺口、共享块、取消、重用、部分成功、失败、重复提交/回执、reset、pin 失败回滚、实际 fake allocator 压力和需求低估，并验证可选的跨 generation request-id 串行限制。另在一项测试中运行 20 个固定随机种子、每个 300 步生命周期交错；每步检查 live batch 的 pin 引用等于 pool pin 账本，最终 pin/unpin 总量相等。

`python scripts/simulate_protection.py --output docs/experiments/2026-09-30/protection-scenarios.json` 输出七类场景 × 关闭/开启保护预算的 14 个执行轨迹。可解释例子：16 个物理槽、需求 10、保留余量 2、保护预算 4 时，保护前 4 个 cached chunk，fake allocator 使用其余槽；关闭预算时前缀被覆盖。所有场景结束均无 pin/batch 残留。这是人为输入需求和确定性 fake free queue 的正确性例子，**不能把保存 chunk 数当成 GPU 命中率、加速比或实际收益**。

## 接 GPU 前必须解决

简单演示按一个 block/chunk 计数；真实环境 256-token LMCache chunk 与 16-token vLLM block 存在 16:1 分组。新增 [chunk_protection_model.py](../../../../scripts/chunk_protection_model.py) 与七项测试，按 16 个物理 block 一组选择和 pin，24-block 预算只能保护一个完整 chunk；任何一块失效即停止其后的前缀。每个 vLLM block 的累计 hash 不同，也不同于 LMCache chunk hash，因此明确要求 `validate_chunk` oracle 验证 chunk 与 token 顺序的对应关系，不能把这些 hash 当同一个值。它仍不是实际 token/layout adapter。需求来自外部真值，当前 lookup 信号仍有误报；没有得到可用于生产准入的预测器。

新增 [vllm_metadata_pool.py](../../../../scripts/vllm_metadata_pool.py) 和服务器契约测试，直接实例化真实 vLLM `BlockPool`，使用原生 free queue、`touch`、`free_blocks`、`get_new_blocks`，但不分配 CUDA tensor 或启动模型。覆盖 null block 不计入可用容量、16:1 分组、真实 allocator 不复用受保护块、取消后等终结回执释放、两代共享块引用和 hash/活动引用失效。hash 内容由测试构造，不是模型生成的 KV 内容；桥接 version 是本地快照编号，不是 vLLM allocator 的 generation，未证明跨重启或所有 ABA 场景。仅选择 idle full block，不涵盖运行请求对受保护块新增引用。该桥接不安装进 Connector 或 scheduler。

实际 MP worker 的回执当前按 request-id 计数，不携带模型里的 batch token。模型新增 `serialize_request_ids=True` 约束，旧代批次终结前拒绝同 ID 新代准备，其他 ID 可继续。真实 adapter 必须启用同等限制，或扩展 wire protocol；不能把模型默认允许的同 ID 不同代并行提交直接接入现有接口。此开关不解决网络重复回执、跨进程重启或多 worker 终结聚合，尚无 wire adapter。

真实接入还需要：同 scheduler 临界区里的复核/pin、真实 prefix closure、可提交 worker 步、STORE 失败/迟到回执语义、pin hook 的部分失败约定、共享块容量记账及公平性。模型从不等待 admission，所以不会在模型内引入排队饥饿；真实在途失败长期占用预算仍需资源恢复协议。阶段 3B 未全面通过前，不启用 GPU 提前保护消融。下一步可继续 fake worker 接口和多 block/chunk 映射，而不是把模型的需求 oracle 接入线上策略。
