# 长 prefill pilot：第三格失败并停止

## 原计划与实际执行

按 [预注册](LONG_PREFILL_PREREGISTRATION.md)，在相同四组配置与资源下，每 session 前置固定材料，保持原到达计划与每请求 48 decode tokens。实际输入范围 **4,129–4,261 tokens**，68 请求共 **284,772 prompt tokens**；8K 上限未改变。新 trace SHA256 为 `d15108f305834554a7cd3cbe7da5103ee3a8348e587594300db5bf37d29e3cd0`。

native 与 owned-immediate 两格通过；第三格 h2.5/d64 的 68 个请求和原始 token/时间戳检查通过，但空闲宽限结束时抛出 `Idle pending differs from current wire identity`，EngineCore 发生 fatal error。随后资源审计失败，停止整个 pilot；h5.0/d1 未运行、formal 未启动、不发布均值或性能排序。

## 失败证据

- 第三格有真实 H2D **20,988,297,216 B**、D2H **9,852,420,096 B**；33 个普通 STORE 完成、保护预算为零。不是无回载或保护零覆盖导致的失败。
- 本轮有 **12 次自然抢占**，但只有聚合计数，不能据此证明抢占造成该身份失配。
- 宽限回调进入 `finalize_idle_pending`，在逐候选预检中发现 `pending.store_metadata` 的 wire identity 与 `OwnedRequestClient.identities[rid]` 不一致。原性能日志没有这两个具体值，尚不能区分缺失 mapping 与映射到了不同 generation。
- 采样时剩余 **1 个 owned job、11 个 active session**；读写锁、temporary、transfer、controller job 都为零。资源门槛仍必须失败，不能因局部归零或进程 returncode=0 改写通过。
- 该 fatal error 出现在 runner 发起关闭之前，不能归类为此前精确识别的正常停机 output-handler 竞态。完整日志原样保留。
- 原审计为 `matrix_complete=false`、2/4 格、`aggregates={}`；第三格未走到 reference-validation 文件生成，独立补审计单独保存，没有伪造该文件。

## 已完成的定位与未修复部分

保留生产候选的原 fail-closed 身份检查。新增两项固定版真实 manager/BlockPool CPU 特征测试：通过实际 OwnedRequestClient 的 END 和 tracker 替换分别形成缺失/新代映射，验证旧 pending 触发相同检查、policy 不变、GPU block 引用/LRU 不变，也不额外 END 新代。它们表明两种状态均可触发检查，**不等于实际失败过程已被重建**。

随后完整 CUDA/native 回归为 **737 passed、115 subtests passed、121 warnings，17.04 秒**。测试通过没有修复该 GPU workload 失败，也没有扩大候选的支持范围。

准备了默认不启用的 `IdleIdentityProbeConnector`：只在原空闲收尾抛异常后记录 pending 范围、原 wire、当前 mapping、finished/inflight 和分类，然后重新抛出同一个异常。4 项 CPU 测试验证缺失/不同代/相同身份分类、无状态改写、原异常和成功返回值保持不变；诊断写入失败也不掩盖原异常。**本日没有用它启动新 GPU 重放。**

## 下一轮执行顺序

1. 在新目录独立运行一次身份观测，保留原 failed pilot。入口 `scripts/diagnose_idle_identity.py --prerequisite artifacts/long-prefill-pilot-20261011a --output artifacts/long-prefill-identity-new --stop-starting-at <带时区的未来截止时间>`。该入口要求原 trace、原失败和原源码哈希一致；诊断不纳入性能聚合。
2. 根据缺失或替换的具体身份，再补 lookup/END/reset/pending admission 的原代际时序。不能直接删除 guard、对任意 raw ID 发 END，或把 11 sessions 都归为同一个问题。
3. 形成最小 CPU 重现和安全修复候选后，另立测试范围，补自然抢占、迟到回执、raw ID 重用和多 pending 操作的 GPU 回归。
4. 修复冻结后从新的长 prefill pilot 开始。旧 pilot 保持失败；新栈通过不改写本报告。此前短上下文成功结果继续限定在原 trace/源码范围。

## 原始证据

- [部分 pilot 原始包](long-prefill-evidence/long-prefill-pilot-20261011a-partial.tar.gz)，SHA256 `15a2e9e7ed86fcf75ba52f925f519880c83f20e4f8e143a521e4351cc139f0f2`。
- [原独立矩阵审计](long-prefill-evidence/long-prefill-pilot-20261011a-partial-independent.json)、[失败格和三格原始测量补审计](long-prefill-evidence/failure-audit.json)。204 个已完成请求的时间戳/token 检查均通过，不能因此将矩阵判通过。
- [当时运行源码](long-prefill-evidence/long-prefill-source-20261011.tar.gz)，SHA256 `b64d710de9471146023a7910b029063c4d1176a9c8a09f124bacbd127e1c50e2`。
- 从该源码包独立核对 [177 项 manifest](long-prefill-evidence/source-verification.json) 全部匹配；68 个原事件除 prompt/token/hash 外的字段与 reference 完全一致。
- [新增特征测试后的全量日志](long-prefill-evidence/evening-final-regression-20261011.txt)。


## 22:54 后续独立定位

用户重新授权后已完成两轮独立诊断及真实库最小复现，详见 [PREEMPTED_IDENTITY](PREEMPTED_IDENTITY.md)。原 guard 未改；实际观察到抢占重建 tracker 后跳过 lookup，新代 STORE 与旧代 END 分离。两次新重放没有再次触发原 idle fatal，但分别残留 9/4 sessions；不算修复。下一步是独立代际终结登记候选和迟到批次回归，原 pilot 始终维持 2/4 失败。
