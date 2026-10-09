# 3C 保护批次回执专项

在 [两 chunk 正常路径](../2026-10-09/PROTECTION_TWO.md) 基础上，仅对第一个真正产生 worker future 的保护 STORE 注入回执诊断，默认 manager 的其他批次不变。继续使用 Qwen3-4B、4090 24GB、2 GiB GPU KV、16 GiB L1、eager、同步模型调度和完整资源审计。

`ReceiptProbeConnector` 保留原 batch identity，在 scheduler 的 schedule/update 边界检查在途保护 block 引用均至少为 1；只有原回执到达后才能由 manager 释放。独立审计要求同一 batch 的安装、真实 future ready、交付、worker receipt、protection release、scheduler receipt 全部恰好一次且顺序正确。

## 注入语义

- `delayed`：真实 STORE future 先完成，观察结果延迟至提交后 5 秒。它延长的是 scheduler 持有引用的时间，没有延长或中断 CUDA DMA。
- `failed-after-success`：要求真实 STORE 结果为 True，随后只把一次 worker 回执结果改为 False。这是失败回执处理诊断，不是实际保存失败、CPU 对象丢失或设备故障。

## 延迟 r1：失败，保留

原 348 请求工作负载在目标恢复前等待资源清零时失败，实际只执行了前 174 条请求。真实保护 STORE 成功，回执约 5 秒后交付，原保护 32 block 已释放；但负载结束后仍有 1 个 pending STORE、1 个 session/owner。读写锁、controller、transfer 均为 0。服务停止，结果保留 `passed=false`，没有执行后续恢复与完整 KV 验收。

这证明两 chunk 正常路径通过不等于延迟后的空闲收尾已解决。现象与旧 r6 的 pending/END 边界相似，但尚不能断言两者根因完全相同，也不能用 TTL 或关闭时回收覆盖该失败。

后续单独使用 `--recovery-traffic`：完成原第一段固定短尾后等待 6 秒，再发送固定 160 个 224-token 短请求，然后执行原资源检查、目标恢复、压力前缀回放及最终收尾。它明确验证继续有流量时的恢复，不作为空闲 drain 修复证据。成功结果只能按这个负载范围报告。

## 复现

```bash
python scripts/protection_zero_smoke.py --output artifacts/protection-delay-new --bounded-workload --modes two --receipt-probe delayed
python scripts/protection_zero_smoke.py --output artifacts/protection-delay-recovery-new --bounded-workload --modes two --receipt-probe delayed --recovery-traffic
python scripts/protection_zero_smoke.py --output artifacts/protection-failed-receipt-new --bounded-workload --modes two --receipt-probe failed-after-success --recovery-traffic
```

每轮必须新目录、隔离实例、串行运行。失败轮不重写为成功；server 保持开机，实验结束停止服务。真实 STORE 执行失败、GPU 取消/抢占、重复/跨代回执及自然 DMA 中断仍需要独立验证，不能由这两个诊断替代。
