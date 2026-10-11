# 空闲收尾候选栈的独立 2.5 GiB 容量对照

日期：2026-10-11。在 pilot 运行前冻结本方案。与旧 `capacity-pilot-2p5g-20261011a` 相区分；旧 zero 资源失败不改写，诊断和性能不混合。

## 准入依据与限制

已验证 normal/zero、two 共同覆盖、真实 raw ID 完整回收后重入，以及同步 KV 探针下的失败回执/共享取消兼容性。原延迟无恢复流量轮资源回收通过但 GPU hit=512/external hit=0，H2D 覆盖失败保留；失败回执和共享取消轮 idle drop=0，不扩大其覆盖。这些足以支持与已通过的普通流量相同边界的隔离研究试点，不能宣布完整故障恢复或生产安全。旧 30 秒延迟、实际 DMA/driver fault、同请求先保护后丢弃后缀等仍开放。

## 固定项

- Qwen3-4B/BF16、RTX 4090 24GB、同步 eager；GPU KV 2,684,354,560 B，CPU KV 16 GiB。
- 同一 IdleFinalizeConnector 和相同 15 秒空闲宽限，zero/one/two 分别最多 0/1/2 个 256-token chunk；除保护预算之外完全相同。
- 固定原 68-request trace `capacity-pilot-2p5g-20261011a/trace.json`，规范化 SHA256 `079c9507c9585a5e5397affc8750c42603ee7efeeca3127d1cafafc2b1c54eb5`。每请求固定 48 output tokens，完整 prompt、到达时间和模型/种子一致。禁用诊断固定 request ID。
- 沿用原 `run_protection_ablation.py` 测量流程，关闭逐步 handoff、KV probe 和 owned-service 逐事件日志；只在正常关闭时输出聚合 counters。idle drop 计数单独报告。
- 源码/配置目录 SHA 清单冻结。formal 必须匹配完整通过的 pilot signature；每格开始前检查源码未变化。原性能 runner 默认路径不改。

## 顺序与门槛

1. `idle-capacity-pilot-2p5g-20261011a`：zero→one→two 各 1 次，目的为验证 gate/实际保护/回载覆盖，不计入正式均值。
2. 仅三格全部通过才运行 `idle-capacity-formal-2p5g-20261011a`：每模式 3 次；顺序 r0 zero/one/two、r1 one/two/zero、r2 two/zero/one，单卡串行隔离实例。
3. 沿用每格 token/H2D/保护 commit/receipt、运行期和退出后 owner/pin/transfer/日志门槛。额外要求 active_sessions=0、temporary_count=0；这是新实验更严格的 gate，不回改历史结果。
4. 任一格服务/资源/覆盖失败即停止后续格，保留原始结果，不追加压力、不延长 45 秒 drain、不丢弃退化点。截止北京时间 21:50 不再启动新格，为 22:00 收尾预留时间。

## 报告

TTFT P50/P95/P99、ready-to-first/end、会话完成时间、排队、实际 prefill、GPU/CPU hit、H2D/D2H、保护 pin/inflight 峰值、策略方法开销与 idle drop。保留每次原始值、均值和样本标准差，n=3 不作显著性保证；保存所有 output hash 差异，不以固定 token 或 HTTP 200 声称 decode 一致。报告只比较本次同栈的 zero/one/two，不把历史不同运行时段/源码/容量的数字拼成总体收益。

```bash
python scripts/run_idle_capacity.py --stage pilot --output artifacts/idle-capacity-pilot-2p5g-20261011a --trace artifacts/capacity-pilot-2p5g-20261011a/trace.json --stop-starting-at 2026-10-11T13:50:00+00:00
python scripts/run_idle_capacity.py --stage formal --pilot artifacts/idle-capacity-pilot-2p5g-20261011a --output artifacts/idle-capacity-formal-2p5g-20261011a --trace artifacts/capacity-pilot-2p5g-20261011a/trace.json --stop-starting-at 2026-10-11T13:50:00+00:00
```
