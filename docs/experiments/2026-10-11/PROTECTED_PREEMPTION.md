# 保护窗口内的自然容量抢占

固定 RTX 4090 24GB、Qwen3-4B BF16、vLLM 0.30.0、LMCache 研究提交，eager、同步调度、单卡单 full-attention group。GPU KV 为 2 GiB / 909 个可用 block，L1 为 16 GiB。属于 3C 正确性专项，不是性能测试。

## 实验及结果

沿用原保护 STORE 的真实 future，用诊断门暂缓交付原回执。单次预热源前缀，确认保护 512 token / 32 block 后，提交四个不同前缀的 3500-token prompt、最多 2048-token decode 流；实际可分配容量不足，由原生 scheduler 自然抢占。探针只记录 `_preempt_request` 和 `BlockPool.get_new_blocks`，不改变调度决策。

r2 完整通过：347 个完整响应，另有四个容量压力流在观察到抢占后正常断流取消。记录一次自然抢占，受害请求从 RUNNING→PREEMPTED、抢占计数加一、computed tokens 清零。**受害请求不共享被保护前缀**，结论只覆盖抢占与保护并存，不覆盖共享前缀请求被抢占。

保护在途期间记录七次实际 block 分配，均未返回原 32 个保护 block；各观察点 block hash 不变、引用为 1、原 manager 所有权仍在。取消压力请求后才放行实际成功的 STORE 回执，原保护引用从 1→0，恰好释放一次。

全轮 17 个 STORE（其中四个保护 STORE），回载 14 chunk / **504 层 KV bitwise 一致**。目标原保护批次的两个 chunk / 72 层均有严格 STORE→实际 native transfer/terminal→H2D 来源，期间无同前缀中间 STORE。其他三个保护批次没有严格回载来源覆盖，不宣称所有保护批次均覆盖。

目标 external hit=512、GPU hit=0，恢复文本与冷请求一致。运行期 session、job/controller、transfer、锁、pin 归零，原 16 个 reservation 恰好释放、24 个 native terminal 审计通过；最终 909 free，关闭引擎后 GPU registration 清零。固定短尾请求是工作负载的一部分，不代表空闲延迟收尾已修复。

新增 18 项专项审计测试，拒绝虚假抢占、错误 victim、错误时序、保护 block 重分配、hash 变化、引用丢失和重复回执等。完整显式 CUDA 回归：**543 passed、115 subtests passed**，121 warnings，17.41 秒。

## 首轮支线及证据

r1 已完成真实 GPU 工作负载及其他审计，但独立专项审计使用包导入，直接以脚本启动时 `scripts` 不在导入路径，整轮保持 `passed=false`。修复双入口导入后，对 r1 原数据做独立审计通过；不改写原始结果。随后 r2 全流程重新运行通过。这是实验入口错误，不涉及保护算法修改。

证据保存在 [r1](protected-preemption-evidence/r1/result.json)、[r1 事后独立审计](protected-preemption-evidence/r1/protected-preemption-audit.json)、[r2](protected-preemption-evidence/r2/result.json)、[回归日志](protected-preemption-evidence/regression.txt)；各目录包括完整事件、KV 探针、请求、状态及配置。服务 `.log` 已下载但按仓库规则不提交。

```bash
python scripts/protection_zero_smoke.py --output artifacts/protected-preemption-new --bounded-workload --modes two --protected-preemption
python -m scripts.audit_protection_preemption artifacts/protected-preemption-new/two --output artifacts/protected-preemption-new/two/audit.json
```

后续继续实际 GPU 上的重复/错误代际回执拒绝、共享前缀 victim 抢占、在途预算/容量回退及一个 chunk 保护覆盖。诊断门只延迟回执，未模拟 DMA 执行中断或真实 STORE 执行失败。
