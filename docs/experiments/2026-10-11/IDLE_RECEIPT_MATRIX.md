# 空闲收尾与回执/取消组合

日期：2026-10-11。遵循 [晚间预注册](EVENING_PROGRESS.md)，只组合已测试 probe 与 IdleFinalizeConnector；不修改旧 smoke 负载、20 秒 drain、模型/资源、同步 KV 探针或验收断言。使用 two-chunk、15 秒 idle grace、GPU KV 2 GiB、CPU KV 16 GiB、Qwen3-4B/4090。没有 recovery-traffic。

## 延迟回执 a：资源局部通过，H2D 覆盖失败

目录 `idle-finalize-receipt-delay-20261011a/two`。175 个请求后在原恢复断言停止，`passed=false`。实际 STORE 成功，原回执延迟 5.016880339 秒后成功交付，32 个保护 pin 按原回执释放；1 个 `[0,512)` pending 条目经空闲收尾丢弃。`before_restore` 的 active_sessions、owned job/controller/transfer/read pin/STORE batch 以及读写锁均为零。

恢复请求 GPU hit=512、external hit=0，未达到原要求 GPU miss 后 CPU→GPU 回载。它仍命中 GPU，不能把该结果解读为数据丢失或重算；也不能据资源通过声称完整正确性通过。没有追加压力、reset 或放宽 H2D 门槛；原 runner 中后续 KV/严格保护来源验收未执行。

失败后隔离实例已停止。独立局部事件审计确认 2 个原 reservation 恰好释放、10 个 transfer/native terminal 闭合，BlockPool 最终 909 free；原受控延迟回执审计通过。这些仅作为失败轮的部分诊断，不覆盖或改写 result.passed=false。原先没有空闲候选的延迟 r1 在 before_restore 资源门槛停止，两者失败位置不同，历史结果均保留。

证据：[局部审计](capacity-evidence/idle-finalize-delay-partial-audit.json)、[原始失败归档](capacity-evidence/idle-finalize-receipt-delay-20261011a.tar.gz)。本例是回执延迟，不是实际 DMA 执行中断。

## 失败回执 a：兼容性通过，未触发 idle drop

目录 `idle-finalize-receipt-failure-20261011a/two`，348 请求，target GPU hit=0、external hit=512。真实 STORE 成功后交付一次失败回执；worker/protection/scheduler 三条原回执边一致，原保护 pin 释放。9 个保护 STORE、峰值 32 block，26 个原 reservation 与 26 个 transfer/native terminal 闭合，最终 BlockPool=909 free。

真实 H2D 共 24 个完整 chunk、864/864 层 bitwise 全等，无缺失参考或不等层；14 个 chunk 通过独立无中间 STORE 的严格来源审计。原 smoke 所有门槛通过，`passed=true`。idle drop=0，因此只证明开启候选后原失败回执场景兼容，不声称失败回执与空闲丢弃共同发生。注入不是实际 STORE/DMA 执行失败。

## 共享取消 a：兼容性通过，未触发 idle drop

目录 `idle-finalize-shared-cancel-20261011a/two`，347 个完整响应和 1 个取消请求。同一组 32 个共享 block 引用按 2→1→0 变化，取消仅释放请求自身引用，原保护回执后解除最后引用。26 个回载 chunk、936/936 层 bitwise 全等，12 个 chunk 具有严格保护 STORE 来源；原取消目标两个 chunk 的严格来源门槛也通过。原所有权、handoff、保护、共享取消及 KV 审计全部通过，idle drop=0，仅记兼容性。

本地再次从原始日志运行独立所有权/保护/严格 KV 来源/具体 probe 审计，通过结果见 [独立复算](capacity-evidence/idle-finalize-probe-independent-audit.json)。[失败回执原始归档](capacity-evidence/idle-finalize-receipt-failure-20261011a.tar.gz)、[共享取消原始归档](capacity-evidence/idle-finalize-shared-cancel-20261011a.tar.gz) 和 [组合入口源码](capacity-evidence/idle-finalize-probe-source-20261011.tar.gz) 保留；归档 hash 见 [清单](capacity-evidence/idle-finalize-archive-manifest.json)。

## 对下一步的影响

没有将整个矩阵标为全通过：延迟 a 仍然失败，另两格没有实际 idle drop。结合已通过的普通流量、真实 raw ID 重入和 two/2.5 GiB 共同覆盖，可以在该有限单卡同步范围继续新栈的独立容量研究试点；其准入和停止规则另见 [预注册](IDLE_CAPACITY_PREREGISTRATION.md)。缺失 terminal、driver fault、30 秒历史 r6 和同请求保护后后缀丢弃仍开放，不扩大生产保证。
