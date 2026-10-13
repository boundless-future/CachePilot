# 工程收敛后的三项推进

2026-10-13。策略与测试组织已收敛，匿名结构双 chunk 零提交的后续调查完成到真实 Scheduler 连续准入契约；尚未实现新的容量策略，也未恢复原 pilot 的 formal 资格。

## 1. 测试依赖收尾

CPUBuffer/allocator 与历史 lookup、leased lookup、transfer plan 的初始化和辅助操作抽到 `tests/cachepilot_test_support/`。支持类不继承 unittest.TestCase，也没有 test 方法；原测试仍保留断言、跳过条件和测试身份。架构检查删除跨测试导入白名单，对全部测试禁止导入其他 test 模块。

独立入口发现 writer 测试曾依靠全量收集中其他文件提前加入 native 扩展路径。已显式调用统一 `configure_native_path()`，避免靠测试运行顺序建立环境；不在全局 conftest 加载上游库。

## 2. 决策与接线分离

`runtime/admission.py` 管理完整 live footprint reserve、完整 chunk 预算和最终容量判定；`runtime/candidates.py` 管理 finished/在途/已保护过滤及空闲队列压力排序。两个模块均可在不加载 torch/vLLM/LMCache 的独立进程使用。

BoundedProtection 和共享保护实际调用这些模块；默认策略仍是 ConservativeAdmission。既有 conservative_reserve 导出保持兼容，预算、排序、完整前缀和安全门槛的数值语义保持不变。pin/commit/receipt/rollback、身份与前缀证明仍由原运行时生命周期负责，connector/manager 接线仍在 integration。

这是一段真实决策拆分，不是完整依赖反转：共享保护仍继承 integration.lmcache 的 generation 保护桥，服务绑定也依赖现有继承链。下一步如拆该耦合，必须先明确状态所有者和 bind 回调协议，不能直接改 MRO。

历史 capacity diagnostic 的 AST 一致性检查首轮失败，因为其复制的 prepare 仍写着原内联算法。现改为调用同一决策函数，保留去掉观测后的 AST 等价检查；历史冻结归档未修改。新源签名不能续跑旧签名 formal。

## 3. 零提交调查与安全边界

原无探针匿名结构 pilot 仍为 3/4，two 零提交、7 次 capacity fallback。后续窗口诊断只见 one/two 2/1 个可见准入机会，各提交一次；没有出现“16 可放而选定 32 放不下”的窗口。观察改变时序，因此不能把诊断成功算成原 pilot 成功，也不能证明原失败的因果根因。

对已经归档的 allocation/native 分析做可复现汇总，见 `summarize_admission_limits.py` 和 `evidence/admission-limits-summary.json`，输入 SHA256 随输出保存：

| 描述性指标 | one | two |
| --- | ---: | ---: |
| reserve 超过909池容量 / reserve有定义窗口 | 25.82% | 26.21% |
| 上述超池窗口中仍有gross新分配的比例 | 25.11% | 25.00% |
| free local-hit touch总量 / gross新分配总量 | 2.46 | 2.22 |
| 零计算token且有新分配的native调用 | 7 | 5 |
| 完整序列门槛拒绝的native调用 | 90 | 85 |

前两行来自 allocation 诊断，后三行来自另一轮 native 诊断，不能跨轮拼成同一窗口。比例不是安全 reserve 上界或性能收益；touch/new 是累计量，不能相加当峰值占用。窗口分类有顺序且重复观察，不能当独立请求失败占比。

真实固定版 Scheduler 新增5项契约，使用实际 Qwen3-4B 配置及32可用块池，不加载权重或启动 worker：

- 混合32本地/32外部token、零计算token仍消耗4空闲块，并保留1个原生prefill reservation。
- 共享 owner 留下或离开两种路径均可连续准入；离开使local-hit touch重新计入free消耗，最终引用闭合。
- 下一16-token chunk能放下、完整65-token序列放不下时，原生 Scheduler 拒绝准入；额外保护引用的有/无两种情况均覆盖。

这些补足既有冷输入、同一步共享和异步零计算的连续准入契约，但外部命中回调仍是fixture；它们不是LMCache真实DMA、服务取消/抢占组合或新策略GPU验收。

## 验证及下一步

Windows全量678 passed、312 skipped、26 subtests；固定服务器全量强制native/CUDA 1177 passed、零跳过、115 subtests、121条上游弃用warning。本轮新增15项收集：9项纯决策、1项架构、5项真实Scheduler契约；旧测试无重复收集。日志和初始失败保存在 `docs/validation/runtime-boundaries-2026-10-13/`。

服务器使用独立 `/root/cachepilot-runtime-boundaries-20261013`，未覆盖原工作目录。真实native/CUDA内核已回归；没有新增模型服务性能实验。独立入口复验及后续记录见 TESTING。

下一步应在明确原生完整调度序列的边界后设计独立opt-in容量候选。候选需要处理local-hit touch、remote物理分配、in-flight reservation、watermark、完整序列门槛及后续请求准入；不能只根据当前调用的剩余容量批准保护，也不能按历史分位数降低reserve。零步回滚不能撤销已驱逐hash和调度历史。先异常/连续准入契约，再独立GPU KV与资源准入，通过后才冻结新pilot；原失败及无稳定收益结论保持。
