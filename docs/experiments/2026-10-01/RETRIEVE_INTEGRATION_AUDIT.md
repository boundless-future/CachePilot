# 真实 RETRIEVE 接入边界审计

日期：2026-10-01；固定安装源码只读审计，无新GPU实验，未修改LMCache。前置CPU实现见[Lookup lease](LEASED_LOOKUP.md)。本报告确认Python调用链，**不证明实际DMA终结或阶段3B通过**。

## 当前调用链

以下路径相对于服务器`/usr/local/miniconda3/envs/cachepilot/lib/python3.12/site-packages/lmcache/`，行号以[源码hash清单](retrieve-integration-audit/source-evidence.json)对应版本为准。

| 层 | 接入点与当前行为 |
|---|---|
| worker | `v1/multiprocess/transfer_context/worker_transfer.py:648`的LMCache-driven `submit_retrieve`发送key、instance ID、block IDs、producer event和skip；返回`to_device_future()` |
| wire | `v1/multiprocess/protocols/engine.py:158`的RETRIEVE是上述5项payload，返回`tuple[bytes,bool]`；`:178`的QUERY只返回hit count，没有reader ticket |
| RPC client | `v1/multiprocess/transport/zmq_impl/client.py:107`发送RETRIEVE，没有server generation/reader identity字段 |
| server | `v1/multiprocess/modules/lmcache_driven_transfer.py:778`的`retrieve`使用登记context，按group解析keys、检查block underflow、stage block IDs并wait producer event |
| CPU读取 | 同文件`:944`进入`StorageManager.read_prefetched_results`；`v1/distributed/storage_manager.py:301`调用`unsafe_read(keys)`，按key取得buffers |
| 传输提交 | transfer文件`:961`调用`transfer_kv_per_object_group`；正常返回后`:974`才把本组keys加入已提交集合 |
| 正常释放 | transfer文件`:979`记录device event，`:981`提交stream callback，payload为keys；`:185`注册到`finish_read_prefetched(list[ObjectKey])` |
| completion dispatcher | `v1/multiprocess/native_completion.py:147`编码payload并调用native `record_completion_on_stream`；`:114`drain解码、调用handler；异常计数并记录，未知kind丢弃 |
| future | `v1/multiprocess/futures.py:25`普通query只检查消息done；`:197`设备future wait会synchronize event，`:254`query会query event，raw返回不代表设备完成 |
| stream一致性 | `v1/platform/devices/cuda/cache_context.py:447`创建torch stream，`:457`创建包装相同指针的CuPy ExternalStream；Python侧event/回调指向同一个context stream |

Engine-driven prepare/commit、SHM、blend和P2P属于另外的传输路径，本次拟先支持当前使用的LMCache-driven CUDA路径。

## 必须保留的状态区别

**RPC返回、设备完成、资源清理完成是三个状态。** server在event和callback提交后就返回event handle与bool；event记录先于释放callback，所以worker即使已观察event完成，server dispatcher也可能尚未清理。资源验收必须检查slot/lease/token的清理账本，不能只看future完成或生成成功。

**异常不证明未提交。** `StorageManager.read_prefetched_results:362`在caller异常时立即匿名finish_read(good_keys)。传输helper的fallback路径（`object_group_transfer.py:480`附近）按batch提交async H2D，再调用kernel；Python异常可能发生在部分提交之后。native plan路径则先建plan（`:293`），再统一调用native执行（`:369`）；本轮未审计该native执行的错误与同步细节，不能断言它发生了在途use-after-free。接入lease时应保守持有，直到实际stream完成证明可用，不能沿用异常即结束读取的规则。

**早期拒绝也需原ticket。** 未登记instance当前尝试依据session释放该worker的一份锁（transfer文件`:223`）；block underflow（`:887`）记录event后返回false，仍保留历史资源失败。未来先校验请求身份与range，再处理claim/拒绝；不能因no-device-work或false按key猜测其他reader的引用。

**回调提交和执行可能各自失败。** payload编码/native提交失败不等于DMA停止；dispatcher的handler异常计数也不等于槽回收。未来registry持有原ticket、lease、提交身份和错误，在回调中先记录terminal证据，再逐项清理；重复/迟到回调不得重试已知成功项。

当前`MessagingFuture`/`DeviceMessagingFuture`没有cancel接口。外部请求取消、MQ超时或丢弃future均不能推断设备停止；异常future也可能是远端失败，必须结合明确的提交/完成证据。

## 下一步实施顺序

1. 新增独立提交适配契约，沿用已实现的`LeasedLookupHarness`，先使用真实`MessagingFuture`/`DeviceMessagingFuture`与受控event backend fixture。覆盖消息先到/event未完成、false但event在途、超时/异常、部分提交、回调提交失败、重复/旧回调和多worker。该测试仍不算GPU证明。
2. 在server registry中绑定唯一transfer identity、原ticket及lease；同一whole-shard只执行一次，提交前校验buffer/key/range/group对应。vLLM可能只retrieve命中后缀，必须显式校验子范围与已领取原槽的关系；首版可保守持有整个槽，不能按传入key补造token或再次pin新reservation。校验失败走未提交终结，提交是否发生未知则保留。
3. 以新的completion kind携带server/lookup generation、worker incarnation和transfer identity，native callback仍只递送身份，实际token和lease保留server内存中。completion dispatcher依据原registry终结，不能以客户端bool或普通future触发unpin。最初支持同一context stream，额外stream必须join或另行证明完成。
4. 设计版本化wire请求/响应：QUERY交付每worker ticket；worker RETRIEVE明确携带ticket，server核对登记worker、request generation、rank及range。兼容接口与旧匿名接口需显式选择，缺少身份时拒绝owned路径。跨进程/重启身份、超时回收另列，不能把当前进程内C++token直接序列化成通用凭证。
5. 用真实pinned CPU buffer和CUDA stream验证H2D内容、event、callback与allocator复用；至少覆盖正常/END/TTL、部分提交失败及回调失败。随后接实际Connector/vLLM做受控服务实验，再决定更广泛失联/writer/shutdown改造。

这是接入设计和已核对的源码事实，以上新适配、wire及GPU验证均**未实现**。原生安装栈与历史资源失败保持不变。
