# 共享前缀服务准入（2026-10-13）

用户授权推进 1–3：独立服务接线、正常 GPU 正确性、异常路径。本轮完成以下限定准入，没有运行性能 pilot/formal，没有改写昨日的新文本失败。

## 1. 独立接线与回调契约

- `SharedPrefixConnector` 继承 generation-aware Connector，要求 generation protection 与 shared-prefix protection 双显式 opt-in；`SharedPrefixServiceProtection` 才允许服务 bind。原 CPU 候选 bind 继续拒绝，旧默认入口不变。
- 每个完整哈希 block 额外取得一个本策略引用；保留其他 owner 引用，16/32 blocks 全额预算及 conservative reserve 不变。
- 保留 token/hash proof、prefix/suffix 票据、原 batch 回执、代际 END 和 poisoned fail-closed。rollback 只将有历史 free-rank 且最终归零的块恢复原顺序；共享块不虚构旧 free-rank。
- 新旧 helper 参数化执行真实 metadata/worker envelope/原回执/旧代/零步/部分异常/终态 snapshot 契约；新增已有 owner 成功、失败、取消、零步，以及两个共享候选在第二个 native registration 前/后失败的真实 Connector callback 测试。发生部分提交时保留引用，禁止 rollback/END。
- 日志增加 pin 前后引用数；离线审计按每次 PREPARED→commit→原 batch 逐步关联，避免把已回滚的共享准备误记到后来非共享提交。

首轮相关子集 76、补充后 85 passed；最终服务器显式 CUDA/native 全套 **961 passed、115 subtests、121 warnings，无 skip**。首次全套 948 passed/9 skipped 后另跑 CUDA 9 项通过；后续增加测试及取消三个无操作参数分支，最终以上述 961 为准，不将历次测试相加。本地最终审计/AST 子集 13 passed。

## 2. 正常 GPU 及已有共享前缀正确性

固定 Qwen3-4B BF16、RTX4090 24GB、vLLM0.30.0/已固定 LMCache，同步 eager、TP1、GPU KV **2GiB**、CPU KV16GiB。历史 trace 使用原 h5/d1、idle grace15秒。未调低 reserve、未扩大显存、未修改原 prompt/到达时间。

| 单元 | 完整请求 | KV 全等层数 | 保护提交 | 自然零 token 回滚 |
|---|---:|---:|---:|---:|
| normal-zero | 348 | 720 | 0 | 0 |
| normal-one | 348 | 720 | 11 | 22 |
| normal-two | 348 | 864 | 9 | 18 |
| shared-cancel | 347 | 936 | 6 | 12 |
| failed receipt | 348 | 864 | 9 | 18 |
| shared-preemption | 347 | 144 | 8 | 17 |
| raw ID reuse / old failed receipt | 348 | 936 | 6 | 12 |

以上七格原 reservation、STORE/RETRIEVE terminal、generation END、保护引用和物理 BlockPool 独立审计通过：final pins0、各909 free。服务器、本地和各自冻结源复算结果一致。

**重要范围：上述 bounded 单元的 shared_prepares=0。** 它们覆盖新 helper 下先保护再共享的异常路径，不能单独证明先共享再保护。因此补充了以下独立历史输入诊断：

| 诊断 | 结果与限制 |
|---|---|
| trace-a：全量同步 KV 探针 | 原68请求/3,264输出无错，generation/资源和普通 KV 闭合；保护0、capacity fallback37，覆盖失败 |
| trace-b：只读候选窗口＋全量 KV 探针 | 再次保护0；841个调度窗口中，候选观测2076次请求条件不满足、1805次容量不足，没有进入后续引用准入的合格窗口；失败保留 |
| coverage-a：关闭 KV 同步与物理逐步快照 | 同一68请求，3次先共享再保护，pin前共享首块引用2/4/4，原回执/代际/资源通过；不宣称 KV bitwise |
| sparse-a：只捕获保护 STORE 和相关回载 | 4次共享保护提交，捕获144层 STORE；原trace未再加载这些前缀，无严格回载来源，准入失败 |
| restore-a：稀疏探针＋独立诊断尾段 | 原68请求不变；追加320条224-token驱逐/收尾请求及3条257-token前缀恢复请求，共323条，各输出1 token。3次共享保护提交，1批实际 STORE→H2D 严格来源链、1 chunk/36层 bitwise 全等；其他两批没有算作严格回载覆盖 |

restore-a 中131个原 reservation 全部 RELEASED、55个 transfer terminal、392代精确 END、909 free、pin0；所有请求完整，GPU退出注册清零。尾段的 prompt长度、原保护prefix哈希、顺序、响应token和请求数量都独立检查。原68条 trace 摘要保持 `015c2148973855fe2fa1e0cb7cef4ecb136f69ecf41fde2212b61bbb9e5acd6b`。

全量同步探针改变了观察到的候选窗口；轻量覆盖和稀疏探针分别提供了不同范围的证据。不能从这些单轮结果声称已量化探针因果效应或保护性能。restore-a 是扩展正确性诊断，不是原trace性能通过，也不是三个保护批次全部回载。

## 3. 异常路径范围

- 真实客户端取消共享请求：32个块 refs2→1→0，实际STORE成功，原回执仅释放保护引用，两个chunk严格回载。
- 自然抢占共享受害请求：32个块 refs2→1→0，38次分配观察及严格回载通过；压力流在观察到抢占后取消，**不是完整 resume 验证**。
- 失败回执：实际STORE成功后报告一次失败，864层全等，资源闭合；**不是 DMA/driver 故障**。
- 实际 raw ID重用：旧代在途时新代到达，旧失败回执保留新代pending；四种旧/重复成功或失败回执被真实 generation-aware入口拒绝。账本和物理状态前后相同，新代两chunk/72层严格回载（全轮936层）。
- 正常轮自然覆盖40次零 token回滚，异常四格另59次；CPU另验证已有外部owner和共享free-rank顺序。
- **尚未覆盖的组合**：同一先共享再保护批次同时遭遇取消/抢占/旧代重用；多worker/TP>1/异步调度、真实DMA故障、完整抢占恢复、永久I/O自动恢复仍开放。不能把分开的契约与GPU专项视为所有组合已验收。

## 失败与日志保留

trace-a、trace-b 和 sparse-a 都保留失败，不替换目录或混入成功矩阵。共享候选没有为了覆盖而改预算/预留/输入。原历史性能pilot仍未通过，不推进formal。

七格 bounded 日志保留退出时 CudaIPCTypes 共享张量警告；失败回执与旧失败回执各有预期 STORE ERROR，均与注入目标关联。不能写成“日志无错误”。trace-a/b/sparse-a 普通请求/资源通过不等于共享bitwise通过。

本地AST因新增只读字段初次失败，更新允许差异时一次缩进错误，修正后通过；末次增加审计篡改测试时缺失PREPARED触发KeyError，补显式断言后13项通过。初次工具输出未伪造成归档日志，服务GPU原始日志完整保存。

## 证据与复现

- [完整原始包](evidence/shared-prefix-complete-evidence.tar.gz)：305个文件、84,179,553原始bytes，压缩15,734,476 bytes；SHA256 `2742fcc5a695eea369a8afc7b8a491b1ce00626a3123176ddd3a4707fcdb51c1`。
- 包含正常/异常/失败诊断全部原始文件、逐文件inventory、10套与各plan精确匹配的冻结源码（233–238项）、测试输出。源码从已保存快照按原manifest重建，逐字节校验，未拿当前源码冒充历史运行源。
- [字节审计](evidence/raw-byte-audit.json)、[本地七格复算](evidence/local-audit.json)、[冻结源复算](evidence/frozen-audit.json)、[源文件复核](evidence/local-frozen-sources.json)。
- [restore本地最终报告](evidence/shared-prefix-restore-20261013a-local.json)、[coverage本地报告](evidence/shared-prefix-coverage-20261013a-local.json)。
- [最终审计/测试/源码补充包](evidence/shared-prefix-final-review.tar.gz)：最终961测试日志、强化逐次身份/postlude审计和源码。压缩411,373 bytes，SHA256 `ec22da606b8733c2bd530ced9476ffea2c8cf41e236da54e469a81817cd7da80`。
- 首轮七格包保留为历史增量归档，其内容已包含在完整包中，不当作额外实验重复。

恢复任一实验先展开其 frozen-source.tar.gz，按该目录plan核对源码、配置、模型和trace；诊断runner命令见 scripts/run_generation_protection_smoke.py 与 scripts/run_shared_prefix_trace.py。完整包中先检查 stopped.json/partial-audit.json，不能只看底层result.json的passed。

## 下一步

1. 冻结轻量服务候选及新的四组历史新文本pilot预注册；保留zero/one/two、相同参数与资源，任何格零覆盖或资源失败就停止。pilot全通过后才formal三次重复。
2. 先共享再保护异常组合扩展列为独立覆盖任务；尤其应补共享owner在原回执前离开和抢占后完整恢复。
3. 根据新矩阵决定是否有稳定收益。3C收益结论仍未成立，不为获得正结果修改原反例输入，也暂不扩模型/卡。

本轮收尾：8000/8080/5555空闲、GPU1MiB/0%，无服务实验残留，容器保持开机；未启用自动化。
