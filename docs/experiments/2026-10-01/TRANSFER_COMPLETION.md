# RETRIEVE传输完成适配契约

日期：2026-10-01。前置[接入审计](RETRIEVE_INTEGRATION_AUDIT.md)和[Lookup buffer lease](LEASED_LOOKUP.md)。这是独立CPU契约，**未接生产wire、native stream dispatcher或真实CUDA，3B未通过**。

## 实现与状态

`scripts/transfer_completion_contract.py`提供`TransferCompletionHarness`。每个transfer身份包含registry incarnation、单调序号和原ReaderTicket；ticket间接包含lookup generation、worker incarnation与rank。先注册原身份，再claim和取得整个shard的buffer lease，不按key创建新reservation。

```text
prepare(ticket) → prepared → enqueue(buffers, bound_callback)
                                ↓
                           submitting
                                ↓
                     submitted / submission_unknown
                                ↓ 原身份server stream callback
                            terminal
                                ↓ 一次性unpin与原token处置
                             closed
```

提交前已收到END或job错误，则不调用submitter，按未提交失败终结；END与进入submitting使用同一Lookup gate定序。进入submitting之后即保守视为可能在途。提交异常或非bool回执记录为submission_unknown，只有匹配身份的server完成证据才能触发回收。false结果也不表示设备停止。

允许回调在submitter返回前到达，但先保存stream_terminal，等submitter返回后才清理，避免重入时提前释放buffer。失败清理保留terminal、cleanup_attempted和原账本；重复回调不重试。一个worker失败不阻止另一个worker终结。

`observe_future`使用安装版真实`MessagingFuture`和`DeviceMessagingFuture`进行非阻塞query。RPC返回、event完成和资源清理分别记录；future诊断不会触发unpin。它不调用可能无限等待设备的`result()`/`wait()`。空event、false、RPC异常、event导入或查询异常，都不能代替server端lease清理凭证。

`stream_complete`目前是**可信CPU fixture提供的停止承诺**。它只防止身份串代和重复处置，不能自动证明DMA已停止，也不提供认证。未来必须由绑定原stream的native callback/明确join后的server observer调用，禁止客户端future直接调用。若callback提交失败且没有后续可信终结证据，则永久保留；本轮没有实现超时强制释放。

## 验证结果

- 新增22项测试、4子测试；包括8线程重复回调和30轮callback/END/submit返回竞争。
- 真实future类使用受控event backend；真实Lookup/storage/controller/L1方法及独立C++ pin使用可复用64字节CPU buffer。
- 覆盖RPC先返回、设备event仍未完成、false但仍在途、空event、RPC超时和异常、event导入/查询失败。
- 覆盖部分提交异常、回调注册失败、重入回调后submit异常、非法receipt、提交前END、取消与TTL、跨代回调、并发重复提交/清理、未知claim结果。
- 注入真实L1 unpin失败验证部分清理证据保留、重复callback不重试、独立worker可结束。TTL已过期时delete/clear/write仍不能重用活动buffer，回调结束后确认stale单独计数。
- 首轮18通过、1失败：测试错误地尝试重写仍被pin的对象，L1正确返回KEY_NOT_WRITABLE。修正为新lookup generation读取已有对象，并补充3项测试后，定向22项全部通过。
- 服务器完整回归：**356 passed，67 subtests，117 warnings，14.28s**；本地122 passed、234 skipped、26 subtests。跳过原因是本地缺少安装版LMCache/native依赖；warning为上游telemetry/torch弃用提示。

原始服务器输出见[full-pytest.txt](transfer-completion-contract/full-pytest.txt)，源码/依赖hash及范围见[evidence.json](transfer-completion-contract/evidence.json)。全量日志由同次SSH执行tee记录，不为保存日志重复运行测试。LMCache安装源码和独立native二进制未修改。

## 后续接入边界

下一步实现server-side transfer plan：提交前将key、group、buffer及vLLM请求子范围绑定原ticket；首版保守持有整个worker shard。当前仅whole-shard契约，没有实际block布局或range校验。

随后接携带transfer身份的completion kind、版本化QUERY/RETRIEVE wire和worker登记校验，再做真实pinned CPU→CUDA内容、stream/event/callback顺序及allocator复用验证，最后接Connector/vLLM服务。不能把当前CPU回调fixture当作native dispatcher已经接通。

当前registry保留完成与失败记录用于审计，没有有界tombstone回收/重启恢复；writer所有权、无END失联恢复、安全shutdown仍未实现。这些限制保持在3B资源验收条件内，不能以本轮测试通过替代整体验收。
