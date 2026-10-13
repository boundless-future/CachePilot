# 匿名结构只读窗口诊断

2026-10-13约13:01–13:08，独立one/two两格诊断均通过请求、原回执、generation/server END及资源检查。各提交1批；这不改变无探针pilot的3/4失败，也不恢复formal资格。

保持原匿名60请求输入/到达、h5/d1、2GiB/16GiB和reserve，仅新增3项独立诊断文件（245源）。普通SharedPrefixConnector复用只读WindowProtection，不启用ProbeMixin、同步KV或物理分配快照；prepare前逐步JSONL仍有观察开销。详见 [方案](SHARED_STRUCTURE_WINDOW_PLAN.md)。

| 重复候选观测分类 | one | two |
|---|---:|---:|
| 调度窗口总数 | 1770 | 1769 |
| 请求live/未finished/在途等 | 2504 | 2455 |
| free < reserve + 16 | 5639 | 5243 |
| 不在当前pressure ranks | 8618 | 8499 |
| metadata不符合完整首段 | 3543 | 3570 |
| reserve未知（如无live请求） | 310 | 306 |
| 可见条件满足 | 2 | 1 |
| 满足最小16块但放不下选定预算 | 0 | 0 |

各格7次按wire去重的capacity fallback，实际提交/回执1/1、62代END，36/33原始batch链全部一致。候选观测会重复计数、分类有顺序，不能用表中次数直接当独立请求占比或损失。

唯一可见合格wire对应原1536-token待保存前缀。one在free909/reserve776/budget16出现两次可见合格窗口，最终1次提交；two在free662/reserve558/budget32出现一次并提交。两次保护前shared_blocks_before均0；本诊断也不能扩展先共享再保护异常组合范围。

本轮没有观察到“16可放、32放不下”的路径，不能据原pilot的0/1差异就推出应把32自动降到16。当前证据更支持**可见准入窗口很少**；但probe影响时序，不能把本轮分类当作原pilot的因果根因，也不量化日志效应。reserve是所有live请求完整footprint的两倍再加watermark/16块，不是下一步实际分配需求，因此经常远超909可用块；它是原有安全边界，不能凭此直接缩减。

## 证据

- [原始包](evidence/shared-structure-windows-evidence.tar.gz)：51文件/190,145,701原始bytes，压缩8,897,076bytes；SHA256 `c4004b273835e9d1e6b31f4cbbd7b3cd56bd98539ca8e3e3bdb95f991eef6c62`。
- [审计摘要](evidence/shared-structure-window-summary.json)（完整逐条审计在原始包内）、[字节与三处复算](evidence/shared-structure-windows-integrity.json)：服务器/本地/245冻结源逐字段一致。
- 3项分类测试验证最小容量与选定预算容量分开、共享首块不误拒绝、hash变化与live请求拒绝。没有修改原runtime。
- 复现：`python docs/experiments/2026-10-13/verify_shared_structure_windows.py <archive> <new-directory>`。

后续先离线量化reserve的保守程度及完整首段限制，形成独立设计与契约要求。任何更精确分配预测都必须覆盖异步回载物理分配、抢占和native保护引用，先CPU/真实scheduler测试再服务准入。不要重复性能实验寻找覆盖，不把改变安全reserve混入已有候选。
