# 共享受保护前缀请求的自然容量抢占

固定 RTX 4090 24GB、Qwen3-4B BF16、vLLM 0.30.0、LMCache 研究提交，eager、同步调度、单卡单 full-attention group。GPU KV 为 2 GiB / 909 个可用 block。本轮是 3C 正确性专项，不是性能消融。

## 实验及结果

原保护 STORE 的真实 future 成功后，诊断门暂缓交付原回执。源请求已结束，留下 512-token / 32-block 保护前缀；随后四个并发长请求制造真实容量压力，其中一个请求共享这 32 个 block。原生 scheduler 对该共享请求发生一次自然抢占，状态 RUNNING→PREEMPTED、抢占计数加一、computed tokens 清零。共享请求释放其引用后，32 个 block 的引用从 2→1；原 STORE 回执放行后从 1→0。

r2 整轮通过：保护窗口内 39 次实际分配均未复用这 32 个 block，hash 和 manager 在途状态保持。347 个完整响应，另有四个压力流在抢占观察后正常断流取消。17 个 STORE 中四个由保护产生；14 chunk / **504 层回载 KV bitwise 全等**。指定原保护批次的两个 chunk / 72 层具有严格 STORE→native transfer/terminal→H2D 来源，中间没有同前缀 STORE。其余保护批次未全部获得严格来源覆盖。

目标恢复时 external hit=512、GPU hit=0，输出与冷请求相同。运行期 session、job/controller、transfer、锁、pin 均归零，18 个原 reservation 恰好释放、24 个 native terminal 审计通过，最终 909 free；引擎退出后 GPU registration 清零。固定尾部流量属于工作负载，不证明空闲延迟收尾问题已解决。

## 失败轮与边界

r1 的 GPU 工作负载和事后独立审计通过，但主脚本以直接文件入口调用包导入，导致最后的专项审计 `ModuleNotFoundError`，原轮 `passed=false` 保留。修复审计双入口导入后，r2 全流程通过。新增 13 项专项审计测试，检查共享 victim 身份、抢占前后引用、hash、分配和回执时序。

本实验中的抢占由真实容量压力触发；受保护源请求已经结束，不能说源请求本身被抢占。诊断门仅延迟真实成功 future 的回执，不是 DMA 执行中断，也不是实际 STORE 执行失败。

证据：[r1 原结果](shared-preemption-evidence/r1/result.json)、[r1 事后审计](shared-preemption-evidence/r1/shared-preemption-audit.json)、[r2 结果](shared-preemption-evidence/r2/result.json)、[r2 独立审计](shared-preemption-evidence/r2/shared-preemption-audit.json)；各轮保留事件、KV 探针、请求、状态和配置。原始 `.log` 按仓库规则不提交。

```bash
python scripts/protection_zero_smoke.py --output artifacts/shared-preemption-new --bounded-workload --modes two --shared-preemption
python -m scripts.audit_protection_shared_preemption artifacts/shared-preemption-new/two --output artifacts/shared-preemption-new/two/shared-preemption-audit.json
```
