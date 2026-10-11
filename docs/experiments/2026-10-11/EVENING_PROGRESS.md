# 2026-10-11 晚间推进记录

用户授权持续推进至北京时间 22:00（UTC 14:00）。到点停止启动新实验，安全收尾并汇报。当前线程 heartbeat 为 `cachepilot-22-00`，每 15 分钟检查一次；恢复前必须查看本记录、TASKS 和服务器进程，避免重复运行。

## 已完成检查点

- 18:54 左右：提交并推送 `4713bcd`。完成 opt-in idle STORE 收尾实现、原始失败保留、五轮实际运行共 544 请求、六目录归档下载 hash 核对、可重跑事件审计、完整 CUDA 回归 686 passed/115 subtests。2.5 GiB two 共同覆盖 9 次保护提交/回执与 2 次 idle drop，但两类 identity 不相交；详见 IDLE_FINALIZE.md。
- 首次 2.5 GiB two 启动失败因 SSH PATH 缺少 Conda bin，无模型请求；a 失败保留，b 仅修正环境后通过。不要把该失败归因于策略。

## 已完成：真实 raw ID 空闲后复用

目的：此前 trace 重入使用新服务请求 ID，不能证明同 raw ID 在旧 generation 已 idle drop/END 后再次注册的隔离性。新增客户端显式、可选 request ID 映射及 SSE 响应 ID 校验；默认回放 payload 不改变。只在独立诊断设置 `VLLM_DISABLE_REQUEST_ID_RANDOMIZATION=1`。

预注册：保持 Qwen3-4B、4090、2.5 GiB GPU KV、16 GiB CPU KV、同步 eager、two-chunk/15 秒宽限和已固定 68-request trace。两遍请求用 `cp-idle-<event_id>` 相同 API ID，首轮先满足原空闲回收门槛，再发第二遍。目录 `idle-finalize-raw-id-20261011a`。每个 raw ID 必须出现两个不同 wire generation，第一次 reclaim 在第二次 lookup 之前；要求实际 idle drop、保护/H2D 覆盖、token 合约、原 reservation/native terminal 和最终资源闭合。输出 hash 差异照实保留，不作 KV bitwise 或性能结论。

停止规则：资源/身份/覆盖失败即保留并定位，不添加压力、不改门槛。此项是已完整回收后 ID 复用，不能替代旧回执尚在途时的 ID 复用或真实 DMA 失败。原 2.5 GiB 性能失败归档不变。

实现期测试：首次客户端测试误把同到达时间的并发请求接收顺序当作固定顺序而失败；修正为每遍请求 ID 集合与逐事件响应身份检查，不改变回放时序。

GPU 结果：`idle-finalize-raw-id-20261011a` 完成两遍各 68 请求；68 组相同 API/raw ID 均对应两个不同 wire generation，旧 reclaim 逐项早于新 lookup。实际被 idle drop 的 `cmpl-cp-idle-67-0` 在第二遍重新注册，资源闭合。137 个 job、282 个原 reservation、91 个 transfer/native terminal 闭合；首轮 H2D=1,660,944,384 B。重复输出 hash 差异 8/68 保留。这覆盖已回收后的复用，不覆盖同 raw ID 在途重叠。

## 当前专项预注册：延迟/失败回执、共享取消

以 `idle_finalize_smoke.py` 组合原始 `ReceiptProbeConnector` / `SharedCancelProbeConnector` 与已固定的 IdleFinalizeConnector。原 `protection_zero_smoke.py` 的请求、同步 KV 探针、资源 20 秒门槛和所有审计保持不变；仅显式开启 15 秒 idle grace，不加 `--recovery-traffic`。Qwen3-4B、4090、GPU KV 2 GiB、CPU KV 16 GiB、同步 eager、two-chunk。

按顺序独立执行 `idle-finalize-receipt-delay-20261011a`（真实 STORE 完成后的 5 秒回执延迟）、`idle-finalize-receipt-failure-20261011a`（真实 STORE 成功后交付一次失败回执）、`idle-finalize-shared-cancel-20261011a`（原共享 GPU 前缀请求取消）。每轮失败即停止该轮、保留原始原因；只有关闭并确认端口/GPU 安全后才考虑独立下一 case，不重复追加压力。要求原始 smoke 门槛全通过；若 idle drop=0，只记兼容性通过，不记收尾机制覆盖。前两种回执注入不是真实 DMA/driver fault。

新的入口不修改安装包，也不改变旧烟测 runner；配置组合仅允许上述两个已审计 probe 类型，未知类型拒绝。若原 20 秒 drain 不足，记录该轮失败而不提高时限。

配置测试与原生组合 MRO 已核查；完整显式 CUDA 回归 703 passed、115 subtests。

延迟回执 a 已结束，`passed=false` 原样保留：原 20 秒门槛内 before_restore 的 job/session/pin/transfer 均为 0，实际 idle drop=1、真实延迟回执成功交付；但恢复请求 GPU hit=512、external hit=0，未覆盖原要求的 H2D，runner 在该断言停止，未执行后续 KV 验收。没有加恢复请求、重跑筛选或放宽门槛。隔离实例已关闭、GPU 回到 1 MiB；继续独立 `idle-finalize-receipt-failure-20261011a`，不将延迟 a 改写为通过。

失败回执 a 已通过：348 请求、9 个保护 STORE、864/864 层回载全等，26 个 reservation/transfer 与 909 free block 闭合；真实 STORE 成功后一次失败回执，原审计通过。idle drop=0，明确只算兼容性，不声称共同覆盖。当前开始独立 `idle-finalize-shared-cancel-20261011a`，前一轮已经正常退出。

共享取消 a 已通过：347 个完整响应＋1 个取消，32 个引用 2→1→0，936/936 层 KV 相同，原目标严格来源通过。idle drop=0，仅记兼容性。三格及局部失败审计见 IDLE_RECEIPT_MATRIX.md；不是矩阵整体通过。

## 当前进行：冻结后 2.5 GiB 容量试点

已保存 IDLE_CAPACITY_PREREGISTRATION.md。新 `run_idle_capacity.py` 复用无诊断日志的原测量 runner，额外要求 session/temporary 清零；pilot 3 格全通过才允许 formal 9 格。相同源码清单、相同 trace、所有模式同 15 秒 idle grace，历史扫描不改写。专项 runner/原 ablation 测试本地 3、服务器组合 30 项通过。

当前远程目录 `idle-capacity-pilot-2p5g-20261011a`，shell session **43111**；zero→one→two 串行。截止 21:50 不启动新格。**在 pilot/formal 完成前禁止改写服务器 scripts/configs**，runner 每格和 formal 准入均验证源码清单。可并行整理已完成原始证据与文档，不能另起 GPU 实验。

## 后续顺序

1. raw ID 专项通过后，归档、专项测试、更新 TASKS/PROJECT_PLAN 并提交推送。
2. 优先复核空闲收尾与实际取消/受控失败回执的交互；所有诊断独立实例，原始失败保留。
3. 生命周期门槛足够后冻结候选，预注册修改后栈的独立容量对照；不可混入旧扫描。随后补同所有权栈立即卸载/固定参数调优与新到达模式。
4. 22:00 前预留收尾时间；不为赶时间扩大支持范围或宣称稳定性能收益。


## 2026-10-11 19:16–19:18（北京时间）：2.5 GiB 容量 pilot 准入完成

- 远程目录：`/root/CachePilot/artifacts/idle-capacity-pilot-2p5g-20261011a`；按 zero→one→two 串行执行，各 1 格。
- 三格均通过 `result.passed`、idle-validation、运行期/退出后 `active_sessions=0` 与 `temporary_count=0`；每格 `idle_drop_count=1`。
- 每格签名均锁定同一 trace SHA `079c9507c9585a5e5397affc8750c42603ee7efeeca3127d1cafafc2b1c54eb5`、2.5 GiB GPU KV、15 秒宽限和全源码 SHA 清单。
- 按预注册规则，已启动 formal 九格：`/root/CachePilot/artifacts/idle-capacity-formal-2p5g-20261011a`，顺序为 r0 zero/one/two、r1 one/two/zero、r2 two/zero/one；任一格失败即停止，21:50 后不启动新格。

19:21 独立复算 pilot：204/204 token 合约通过；zero/one/two H2D 分别为 1,358,954,496 / 1,698,693,120 / 1,509,949,440 B，保护提交/回执为 0/0、5/5、4/4。one 关闭后有 8 行精确分类的已知 teardown traceback，运行期无 fatal error；该分类原始日志保留。当前 formal shell session **45268**，禁止改服务器 scripts/configs。

下一阶段预注册和本地实现已完成：固定基线 3 horizon × 2 drain 上限、one/two 同期对照；pilot 8 格→formal 24 格→锁定最佳固定参数后新文本验证 9/12 格。新输入已在服务器 artifacts 生成，不修改在途 scripts/configs。四个新 CPU 测试与三个 idle-capacity 测试本地共 7 passed；当前尚未上传新 runner 或执行该扫描。

## 19:34–19:42：新栈容量 formal 独立复核与固定参数 pilot

- formal 九格完成；612 请求及源码/trace/token/H2D/保护回执/资源独立检查均通过，每格 idle drop=1。one/two TTFT P95 均值相对 zero 为 -3.07%/-2.00%；首组重复均慢于 zero，输出 hash 差异和两格退出 traceback 保留，见 IDLE_CAPACITY.md。
- 原始归档已下载，服务器/本地 SHA256 均为 e27f1969d119c48b2c0022e7d27823ea92cd20f1525ed7e93e7d5a84a8fd52a8。原始目录不改写。
- 确认原 runner 不在运行且 GPU 回到 1 MiB 后，上传已提交的固定参数 runner/统计器，启动 fixed-baseline-pilot-20261011a；本地 shell session 5289。当前按预注册串行扫描，21:50 后不启新格。源目录再次冻结，pilot/formal/validation 完成前不更新服务器 scripts/configs。
- 六点固定 baseline + one/two 一共八配置，pilot 不计入均值。等待完整准入后才允许 formal。

该检查点本地专项测试：`python -m pytest tests/test_fixed_baselines.py tests/test_policy_matrix.py tests/test_idle_capacity.py -q`，10 passed（0.54 秒）。未声称重跑完整 CUDA 回归。

19:47：新增离线 `audit_replay_measurements.py`，直接从 SSE/client 时间戳重建 TTFT、入口等待、端到端和会话指标；独立 inclusive quantile 与原结果核对，额外检查输入、实际 token 和输出 hash。四个篡改/分位数测试通过，新栈容量 formal 612 请求复算全通过。新脚本仅在本地使用，未改变在途服务器源码签名。

19:51：固定参数 pilot 8/8 格通过，544 请求的独立 source/trace/资源/回执与原始时间戳复算通过；归档 SHA256 为 9b0e34eaa483b333150536e07d58c4e2146b4d2c73117e7fa5840cc7efe53460。已开始 fixed-baseline-formal-20261011a，24 格、各配置三次、按冻结顺序轮转；本地 shell session 19961。源码继续冻结，未同步离线审计器或后续 reference runner。

19:55：固定参数 formal 已完成前 4 格（`h1p25-d1-r0`、`h1p25-d64-r0`、`h2p5-d1-r0`、`h2p5-d64-r0`），当前仍按冻结顺序运行；远端进程和 GPU 服务健康，未发现新的失败格。同步完成本地 10 项固定矩阵/容量/审计测试；离线 `audit_replay_measurements.py` 的测试文件名为 `tests/test_replay_measurement_audit.py`，不能使用不存在的 `test_replay_audit.py`。本地新增的 reference 预注册和 formal timing audit 文件尚未提交，待本轮 formal/归档完成后与进度记录一并提交。

20:10：检查后续 reference runner 时确认 formal→validation 的源码签名必须严格相同；任何新增或修改 `scripts/`、`configs/` 文件都会使 validation 不能复用 formal 计划。因此后续 reference 审计器只在 validation 完成后恢复，当前服务器与本地源码保持冻结。

20:25：固定参数 formal 24/24 格全部通过；远程独立统计和本地复算均为 `matrix_complete=true`、`passed_cells=24`，原始归档 SHA256 为 `ba9e66d1a8097e77d5b3f9dc647ae5563202e732bc5028acceb7d99f8a956e52`。24 格共 1,632 请求，独立时间戳/token/hash 审计 24/24 通过；两格已知关闭竞态按精确规则分类，未见运行期 fatal error。六个固定 zero 基线依开发集 TTFT P95 均值选择 `h5p0-d1`（7119.688 ms，个体 7182.285/7169.758/7007.021 ms，SD 97.773）；one/two 未参与选择。各点搬运与输出 hash 差异完整保留于 `fixed-baseline-evidence/`。已按预注册启动 validation，当前 `h2p5-d64-r0` 正在运行，服务器源码仍冻结。

20:31–20:34：validation 第三格 `protect-one-r0` 因零保护覆盖停止。68 请求与资源均通过，但 8 次 capacity fallback，保护提交/回执 0/0；原失败不改写，后续九格未运行。部分归档 SHA256 `554e4e371737625f2f78d7739f77864de0a55ee37193135ca82100238ba010be`，独立矩阵审计 2/12 通过、无均值；三个已有格的原始测量复算均通过。进程已退出，GPU 1 MiB。详见 FIXED_BASELINES.md。原六组 reference 的 validation 前置条件未满足；不直接启动它。

20:37：按 UNPROTECTED_REFERENCES_PREREGISTRATION 独立启动四组参考 pilot；新 scope 不包含保护候选，不改变旧矩阵。四项 reference CPU 契约和四项原始测量审计本地/服务器都通过。服务器新增三个后续 runner/auditor，旧 formal 已冻结源文件 hash 保持一致；本轮再冻结完整新清单。目录 `unprotected-references-pilot-20261011a`，session 45480；只允许完整 pilot 独立审计通过后启动 formal。原生组已完成，立即卸载运行中。

21:00 左右：unprotected reference formal 完成 12/12 格、816 请求；服务器 summarize 与 timing audit 均通过，本地下载归档后独立复算一致。原始归档 SHA256 为 `20bbabfa7a307e5f76fa299c6266d0b8454237b075c9e947ce594b5eb6138ca1`。native TTFT P95 均值 6732.95 ms；默认 h2.5/d64 为 7250.16 ms；开发集选中 h5.0/d1 为 7187.49 ms。报告见 `UNPROTECTED_REFERENCES.md`；该 scope 不评价保护策略。

21:04：完成到达时间压缩 follow-up 的 runner、预注册和 18 项 CPU/审计测试；唯一干预为把同一 reference trace 的开放环 `arrival_ms` 乘 0.5。服务器 pilot 4/4 格独立审计通过，formal 已按轮转启动；21:50 后不启动新 cell。首次 pilot 启动曾因非交互 SSH PATH 缺少 `bash` 而无请求失败，按环境启动错误单列，修正 PATH 后重启为 `arrival-half-pilot-20261011b`，不把 a 纳入结果。

21:27–21:31：压缩到达 formal 12/12 格、816 请求完成，服务器与本地独立资源/测量报告一致；归档 SHA256 `8ccb222f931101818eefe5b49237ee809c5ca71906e1effe55d0e6c5cd6577ea`。native TTFT P95 9503.55 ms，另外三组高 3.47–5.81%；原与新矩阵单独聚合。冻结源码逐字节匹配 reference 173 项、arrival 174 项。

21:29–21:31：单次零覆盖诊断复现保护 0/0，9 个首次容量回退，free 423–704、reserve 972–2208；保护预算未占用，所有观察随后有默认 STORE。原 performance gate 仍 false，诊断请求/资源/原始事件审计通过。真实 CUDA/native 全量回归 734 passed、115 subtests，17.30 秒；本地全量因缺 lmcache/msgspec 收集失败，单独保留。

21:37：继续原计划的长 prefill 准入试点，新增每 session 固定前置材料，不改变到达和 48 decode tokens。四组各一次，今晚不启动正式矩阵。输出 `long-prefill-pilot-20261011a`，本地 SSH session 1009。此前压缩到达源码已归档；新 runner 签名单独冻结，禁止在该 pilot 期间改远程 scripts/configs。新增服务器 CPU 测试 4 passed。README、接入文档与实现地图同步到实际 Connector/所有权/空闲收尾关系。

21:42–21:47：长 prefill pilot 第三格失败并停止，前两格通过，第三格 68 请求/token/H2D 均通过，但宽限收尾触发 `Idle pending differs from current wire identity`，EngineCore fatal；1 owned job/11 sessions 未清。不是正常关闭竞态，没有运行 h5.0/d1 或 formal。部分归档 SHA256 `15a2e9e7ed86fcf75ba52f925f519880c83f20e4f8e143a521e4351cc139f0f2`，原矩阵 2/4、无均值。新增真实 manager 的缺失/新代映射特征测试，737 passed/115 subtests 全量通过；并未修复该 GPU 失败。

随后准备仅在原 guard 抛错后记录身份的观察器及 `diagnose_idle_identity.py`；4 项 CPU 测试、编译与 CLI 检查通过，没有启动 GPU 观察。下一轮先区分当前 mapping 缺失还是新代替换，再追完整生命周期因果，不能直接删 guard。

21:50 起不再启动 GPU 工作。21:52 独立收尾快照确认实验进程为空、8000/8080/5555 均关闭、GPU 1 MiB/0%、无计算进程。容器保持开机，已停止的是自有实验服务；退出后空闲不改写失败轮内的 1 job/11 sessions 残留。源码/环境快照、原始失败与全部新文档链接均已核对。见 `long-prefill-evidence/closeout-2152-20261011.json`。
