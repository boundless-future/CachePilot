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

配置测试与原生组合 MRO 已核查；完整显式 CUDA 回归 703 passed、115 subtests。当前远程运行 `idle-finalize-receipt-delay-20261011a`，shell session 18104；在它退出并审计前，不启动下一 GPU 轮。

## 后续顺序

1. raw ID 专项通过后，归档、专项测试、更新 TASKS/PROJECT_PLAN 并提交推送。
2. 优先复核空闲收尾与实际取消/受控失败回执的交互；所有诊断独立实例，原始失败保留。
3. 生命周期门槛足够后冻结候选，预注册修改后栈的独立容量对照；不可混入旧扫描。随后补同所有权栈立即卸载/固定参数调优与新到达模式。
4. 22:00 前预留收尾时间；不为赶时间扩大支持范围或宣称稳定性能收益。
