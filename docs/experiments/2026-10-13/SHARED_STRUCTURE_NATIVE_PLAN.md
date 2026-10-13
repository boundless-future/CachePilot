# 匿名结构：原生单次准入边界诊断

2026-10-13北京时间13:42预注册。前一allocation两格闭合，248项源、51文件证据三处复算一致。原无探针pilot仍3/4，formal继续禁止。

保持60请求trace、Qwen3-4B、4090、2GiB/16GiB、同步eager/TP1、one16/two32、h5/d1和所有reserve不变。按one→two各运行一次；仅新增native observer/connector/runner/auditor四项，旧248源不修改。14:25后不启动新格。此为独立有开销诊断，不能计性能或替代原失败。

在原KVCacheManager.allocate_slots调用外围只读记录参数及结果，在真实coordinator.get_num_blocks_to_allocate返回处记录原需求/free；保留full_sequence_must_fit与最终容量检查各自调用。BlockPool包装分别记录成功新分配数与touch之前ref=0的空闲块数；未处在该调用中的inflight-reservation查询和保护touch不算入。原分配参数、对象、异常、引用操作和准入决定不改。

验收：调用编号连续、每次属于本调度步、逐步gross总量与前一observer一致；限定单FullAttentionManager下free消耗等于新分配+零引用touch。成功调用最终native需求必须等于该容量消耗，且需求+watermark+reserved不超过当时free；拒绝必须满足对应full-sequence或最终容量门槛，并没有物理新分配/touch。任何非覆盖失败停止后续格，完整证据保留。

远端真实manager观测器4项、native容量8项、审计6项合计18 passed/14条依赖弃用warning。本地两类审计12 passed；没有运行全量GPU/native基线。所有行为测试使用真实CPU BlockPool，不执行模型计算。

报告纯外部回载/混合命中/零计算分配、命中touch消费、原生reserved/watermark覆盖。原生单调用需求是某个已确定请求/命中状态下的数值，不能直接当整个schedule需求、额外安全pin预算，不能遗漏后续等待请求和抢占。暂不加入新的保护候选、不改reserve、不在本轮启动formal。
