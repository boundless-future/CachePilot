# 容量压力自然抢占与迟到 STORE 回执

## 负载和范围

RTX 4090 24GB、Qwen3-4B、eager、GPU KV 1 GiB、CPU KV 16 GiB。8 个并发请求，每个 1,536 prompt tokens + 固定 2,048 decode tokens；vLLM 最大4个运行请求，最大批token数2,048、上下文4,096。采用只读事件 Connector 和默认 EVICTION_AWARE；不调用 reset API，也不主动修改 scheduler 的抢占决定。随着 decode 增长，可用 GPU KV 不足，真实 scheduler 触发重算抢占。

```bash
python scripts/natural_preemption_smoke.py --output artifacts/natural-preemption-2026-09-30-r2
python scripts/natural_preemption_smoke.py --receipt-delay 5 \
  --output artifacts/natural-preemption-held5-2026-09-30-r1
```

第二条命令通过 `HeldStoreResult` 延后查询到真实 STORE 结果的时机，延迟5秒但不改结果；原future已完成时单独记录 `actual_store_ready`。它制造更长的 scheduler pin 所有权窗口，**不是 GPU DMA 执行5秒，也不是自然传输慢5秒**。

## 已完成结果

| 轮次 | 请求完成 | 抢占次数 / 涉及请求 | orphan 回执 | 审计释放的pin引用 | 最终空闲块 |
|---|---|---|---|---:|---:|
| 原始 r1 | 8/8 | 11 / 6 | 未记录逐pin回执 | 未记录 | 454/454 |
| 原始 r2 | 8/8 | 12 / 6 | 3 | 1,040 | 454/454 |
| 回执延迟5秒 r1 | 8/8 | 16 / 6 | 4 | 1,120 | 454/454 |
| 回执延迟5秒 r2 + 630秒观察 | 8/8 | 16 / 6 | 4 | 1,120 | 454/454 |

四轮请求都生成2,048 tokens并以 length 结束，scheduler请求表/deferred frees/最终跟踪块引用清零；LMCache锁、STORE队列、prefetch job/result清零，引擎退出后GPU注册清零。[r2逐回执审计](natural-preemption-2026-09-30-r2/receipt-audit.json)确认每个回执前后的物理block引用各减1，3个orphan批次合计336个pin引用，分别在对应抢占之后77–124ms收到回执。

两次延迟轮各13批真实STORE均返回True，13批均在延迟期内观察到原future已ready，4批由orphan路径结算。r1的 `passed` 只包含锁/job/GPU资源门槛，不包括session TTL。r2在引擎退出后继续观察630秒：退出时仍有2个session，405秒采样仍为2，420秒采样首次归零，随后直到630秒都为0；锁/job/result/注册也为0，`ttl_passed=true`。这是从引擎退出时起算，session在此前生成阶段已存在一段时间，不能由420秒反推其配置TTL。原始数据见 [结果](natural-preemption-held5-2026-09-30-r2/result.json) 和 [完整采样](natural-preemption-held5-2026-09-30-r2/post-stop-observations.json)。

## 不能合并的证据

四轮 `preempt_before` 钩子记录的 `store_inflight` 都为0，但稍后有orphan回执。r2新增事件已经解释这两个时点的差异：[逐请求时序](natural-preemption-held5-2026-09-30-r2/reset-receipt-timeline.json)中，4次相关抢占发生在manager reset前37–42ms，worker提交发生在reset前约1–2ms；reset时已有112/32/32/64个pin，reset后批次标为orphan，约5.03–5.05秒后scheduler收到终结回执。因而是真实抢占后提交旧代STORE，随后恢复tracker触发reset；不是抢占钩子漏报在途批次。当前证据支持“自然抢占之后旧批次回执安全释放”，不称“DMA正执行时自然抢占已完整验证”。

本实验使用诊断日志且改变KV容量/输出长度，不纳入性能基线，也不宣称抢占次数越少性能越好。尚未逐条比较多请求并发输出与无抢占输出或逐层KV；仅检查HTTP、长度、状态和资源生命周期，不把它们当作内容正确性证明。


后续加入同步KV探针的独立负载已获得实际回载的有限内容证据，见 [PREEMPTION_KV_INTEGRITY.md](PREEMPTION_KV_INTEGRITY.md)。这不追溯改变上述四轮未进行内容比较的事实。
