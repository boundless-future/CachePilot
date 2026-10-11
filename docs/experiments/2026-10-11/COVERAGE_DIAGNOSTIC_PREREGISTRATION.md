# 保护零覆盖的独立观测预注册

2026-10-11 21:25 前登记；在压缩到达 formal 结束、实例完全关闭后，且北京时间 21:50 前才启动一次独立诊断。

原新文本 validation 的 one 配置有 8 个首次容量回退、保护 0/0。保持原 trace SHA、h2.5/d64、one chunk、2 GiB GPU KV、16 GiB CPU KV、Qwen3-4B、单卡同步 eager、15 秒 idle grace。唯一变化为开启已有 handoff JSONL 观察（不修改策略函数），记录 `free`、保守 `reserve`、所需 block、剩余预算、在途 pin 和同 generation 随后的默认 STORE。

只执行一次。观察日志会扰动时序，若不复现则如实记录，不能再次采样直到失败重现。原 validation 文件与失败状态不可改写。本轮结果不纳入任何性能均值，不将诊断通过写作原性能覆盖通过。

要求原请求/token/时间戳、H2D、资源、关闭与日志门槛通过；容量事件必须满足 acquired_pins=0、预算足够、free < reserve + required。已有记录每 wire 仅保留首次容量回退，不是全部尝试。报告保守 reserve 与 free 的缺口，不把 reserve 当作真实准入需求或充分的最优性证明；不削减 reserve 以获得保护覆盖。

结果用独立 `coverage-diagnostic.json` 标明诊断审计、原 performance gate 与零覆盖是否复现。若其它错误或资源残留，停止并保留，不继续更改实现。
