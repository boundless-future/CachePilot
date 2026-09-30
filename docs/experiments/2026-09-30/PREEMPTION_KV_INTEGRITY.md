# 自然抢占后实际回载的 KV 内容核对

## 方法和审计

沿用8并发容量压力、Qwen3-4B、eager、1GiB GPU KV的自然抢占负载，组合已有生命周期观察器、受控延迟真实STORE回执和同步KV探针。保存前在worker采集各层完整chunk快照；请求完成后按原完成次序重发8条1536-token prompt，各生成32tokens，遇到GPU miss后由LMCache真实回载，完成后逐字节比较快照。

原始STORE/RETRIEVE执行方法不改写。探针使用CUDA同步和CPU tensor副本，会改变时序与吞吐，**不得纳入性能对照**。KV布局严格限定当前单dense group；APC命中导致retrieve的首chunk只写了一部分时，明确跳过这个chunk，剩余完整chunk才能计入回载比较。没有实际回载、缺参考、内容不同或不能关联orphan批次都不算该实验通过。

```bash
python scripts/natural_preemption_smoke.py --kv-probe --receipt-delay 5 \
  --output artifacts/natural-preemption-kv-probe-2026-09-30-r1
python scripts/natural_preemption_smoke.py --kv-probe --receipt-delay 1 \
  --output artifacts/natural-preemption-kv-probe-held1-2026-09-30-r2
```

## 首轮结果

r1完成16次自然抢占、8/8原请求；原负载1120 pin引用闭合，加入8条follow-up后累计1408引用闭合，GPU最终454free恢复。引擎退出后session、GPU注册、读写锁、job/result和队列均为0。

40个实际回载的完整chunk，各36层，共1440次逐字节比较全部相等、无缺失参考；有一次APC部分chunk排除事件，不把它算作全chunk回载。6个比较chunk的首次保存参考来自被标为orphan的旧代批次。

后处理进一步发现其中5个chunk在回载前再次发过STORE，所以不能把这5个当成旧批次持久化来源证明。新增严格审计要求没有中间STORE，仍有1个chunk（token范围1280–1536）、36层完全相等，来源batch的pin、orphan状态和成功终结回执对应。原始 [运行结果](natural-preemption-kv-probe-2026-09-30-r1/result.json) 保留未覆盖这一额外条件的旧审计；追加 [严格审计](natural-preemption-kv-probe-2026-09-30-r1/kv-audit-strict.json)，不覆盖原始实验文件。

## 结论范围

这补上了“自然抢占后旧代STORE批次保存的部分KV能正确回载”的有限内容证据。没有比较8条长生成输出与无抢占参考，没有证明所有decode KV或所有orphan batch，也不证明DMA正执行时被抢占的行为。严格来源审计只在此次同一worker/sidecar实例、无L2且完整保存/回载事件观察的条件下成立；不是跨重启或多worker协议。

范围修剪3项、来源审计4项测试覆盖无回载、错误pin、非orphan/失败回执、回执晚于回载、中间重复STORE和内容不一致等假通过情形。更短1秒延迟重复轮结果如下。


## 1秒延迟轮：内容相等，但目标路径未覆盖

r2有12次自然抢占、8/8原请求完成；7个真实STORE回执、其中1个orphan，原负载656 pin引用闭合，加入follow-up后累计1008。36个回载chunk、1296层比较全部相等，无缺参考；退出后session/注册/锁/job/result全部0。

专项 `passed=false`，原因是 `orphan_source_chunks=[]`，不能把普通STORE的回载相等用于证明目标orphan路径。该轮唯一orphan批次只有16个pin，其涉及的前缀此前已在别的批次保存，首次参考不是该orphan批次；因此不把它归入严格来源。见 [r2结果](natural-preemption-kv-probe-held1-2026-09-30-r2/result.json)。这不是发现KV字节损坏，也不能把“内容全等”改写成专项覆盖通过。5秒设置追加r3重复已完成，所有失败和未覆盖轮均保留。


## 5秒延迟重复轮

r3重复通过：16次抢占，8/8原请求及8条follow-up完成；原负载1120 pin引用、含follow-up共1408引用闭合，GPU454free恢复。40个完整回载chunk/1440层逐字节相等，6个首存参考可关联orphan batch，其中1个chunk（1280–1536）没有中间再次STORE，36层严格来源比较通过。与r1对应的是不同请求/前缀hash，不能把两轮视为覆盖了所有chunk。退出时session、注册、锁、job/result均0。见 [r3结果](natural-preemption-kv-probe-held5-2026-09-30-r3/result.json) 和 [严格审计](natural-preemption-kv-probe-held5-2026-09-30-r3/kv-audit.json)。

| 设置 | 抢占 | 回载chunk / 层比较 | 不等/缺参考 | 无中间STORE的orphan源chunk | 专项 |
|---|---:|---:|---:|---:|---|
| 5秒 r1 | 16 | 40 / 1440 | 0 / 0 | 1（追加严格后处理） | 通过 |
| 1秒 r2 | 12 | 36 / 1296 | 0 / 0 | 0 | 覆盖不足失败 |
| 5秒 r3 | 16 | 40 / 1440 | 0 / 0 | 1 | 通过 |
