# 匿名结构共享候选：pilot零覆盖反例

2026-10-13 12:45–12:57，固定四组pilot完成但仅**3/4通过**，shared-two因0保护提交失败，正式三轮不启动。原请求、资源和回载通过不等于策略覆盖通过；不将失败格的较低耗时当作收益。

## 方法

见 [预注册](SHARED_STRUCTURE_PREREGISTRATION.md)。历史六session匿名结构60请求/209,661输入tokens、每请求48输出；来源21/22/26/57/58/86全部保留。原输入缩比、到达、hash重建token数组未改。Qwen3-4B BF16、4090 24GB、2GiB GPU KV/16GiB CPU KV、同步eager/TP1、idle15秒；default h2.5/d64，其余h5/d1，保护预算0/0/16/32 blocks。没有KV同步、逐步JSONL、额外metrics采样。

原240源不改，仅新runner/auditor两项，共242。结构输入是token IDs，不再encode；tokenizer文件SHA、词元范围和源重建检查通过。每格61唯一raw请求（含warmup），generation总数按61+实测自然抢占核对，wire尾部raw ID绑定及精确END/操作终态检查通过。

## 单格观察（不聚合）

| 配置 | 准入 | 提交/回执 | capacity回退 | 抢占 | generation | TTFT P95 ms | prefill tokens | H2D bytes |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| default-zero | 通过 | 0/0 | 0 | 1 | 62 | 2317.42 | 33,677 | 2,189,426,688 |
| selected-zero | 通过 | 0/0 | 0 | 1 | 62 | 2231.30 | 33,677 | 2,189,426,688 |
| shared-one | 通过 | 1/1 | 6 | 2 | 63 | 2193.59 | 36,477 | 2,000,683,008 |
| shared-two | **覆盖失败** | 0/0 | 7 | 1 | 62 | 2153.27 | 33,677 | 2,189,426,688 |

共240请求/11,520输出tokens完整、无请求错误，所有原始SSE时间戳审计通过；服务session/job/transfer、pending/inflight/pin、generation与原回执均闭合。shared-two只是coverage/result两项false，不是资源泄漏或请求崩溃。one多一次抢占、prefill更高不能单轮归因于保护，也不能以P95略低宣称收益。

同selected-zero输出hash差异default5条、one7条、two1条；two比较标为ineligible。无未分类运行fatal及关闭traceback，原始warning完整保留。没有新增KV bitwise或任务质量评价。

## 离线复用机会

四格原请求时间均显示52/60已完成请求之后还有相同首256/512token需求，两种长度各涉及6个唯一前缀，见 [汇总](evidence/structure-pilot-reuse-summary.json)。已发送但尚未完成的重叠请求不计为后续需求；6项边界测试通过。

这只是输入匹配与客户端时间的事后分析；没有把具体保护批次关联到命中，也不知道该前缀是否仍留GPU、已在CPU或已经错过保存窗口。它表明不能把零保护简单解释为“输入没有复用”，但不是可获收益的上界或因果估计。

## 证据与下一步

- [完整包](evidence/shared-structure-pilot-evidence.tar.gz)：80文件，20,250,570原始bytes，压缩1,982,987 bytes；SHA256 `8869581a276bc390c4bc18e265de6e58927d651d7485f207bdd29457150fbdda`。
- [审计](evidence/shared-structure-pilot-audit.json)、[字节与三处复算](evidence/shared-structure-pilot-integrity.json)：服务器/本地/242项冻结源报告完全相等，matrix_complete=false、passed_cells=3、aggregates空。
- 原始失败result/stopped.json、全部四格、输入来源、启动前源码与预注册、30项本地入口/历史回归和6项离线测试日志均保留。首次raw ID篡改测试失败暴露仅计数不足，GPU前增加wire身份检查后通过；没有伪造首次工具输出为文件日志。
- 复算：`python docs/experiments/2026-10-13/verify_shared_structure_evidence.py <archive> <new-directory>`。

下一步已另立 [只读窗口诊断](SHARED_STRUCTURE_WINDOW_PLAN.md)，不重跑pilot挑选结果、不降低reserve、不修改输入；诊断不能替换本失败，也不能因有日志后覆盖成功而启动原formal。
