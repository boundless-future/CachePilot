# 下一窗口：原生准入边界与候选设计

2026-10-13 14:21更新：匿名结构pilot **3/4**，双chunk0提交；formal未运行。随后windows/allocation/native三组独立诊断各2格通过并归档，均有观测开销，不能替换原失败。真实Scheduler最终6项序列测试通过，详见 [准入报告](SHARED_STRUCTURE_NATIVE.md)。下面旧计划保留为历史，不能再次照单启动pilot。

当前下一主线：

1. 核对服务器状态和最新TASKS，复用本轮四个完整证据包；不重复运行原pilot，不改输入/预算/reserve追求通过。
2. 在真实Scheduler中补GPU/外部混合命中、共享owner离开与full-sequence门槛的连续准入契约。已有冷输入/共享前缀/外部回载/reservation测试只是首批边界。
3. 按 [设计约束](NATIVE_ADMISSION_BOUNDARY.md) 明确整步容量、临时引用、零token和原回执交接，决定能否提出独立opt-in候选；释放引用不恢复被驱逐hash，单次allocate_slots余量不够。
4. 候选依次通过真实库CPU/异常契约、独立GPU KV bitwise/资源、固定开发pilot与新输入验证，才能讨论性能。若无法得到足够安全窗口，记录适用范围受限，不无限重跑。

## 历史窗口计划（本轮已执行）

当前历史合成输入共享候选pilot4/4、formal12/12完成，见 [正式报告](SHARED_STRATEGY_FORMAL.md)。TTFT均值约低1.5%，但prefill/H2D相同、two一轮退化；不重跑这组挑选结果，不改变旧失败结论。

## 下一主线

1. 检查SSH、GPU/端口和运行任务。上轮结束240项脚本/配置源未漂移，三个端口空闲、GPU1MiB/0%，无实验残留；容器保持开机。先读最新TASKS，不盲目重复旧窗口。
2. 沿用现有六session匿名结构trace，先核对2026-10-12/REAL_STRUCTURE_PLAN.md、REAL_STRUCTURE_FORMAL.md的trace/source/tokenizer/到达语义。它以前用于零保护测试，属于历史负载扩展，不是未见验证集。不改到达/内容追求保护覆盖。
3. 为共享候选四组独立预注册：default-zero h2.5/d64、selected-zero/shared-one/shared-two h5/d1；GPU KV2GiB、CPU16GiB、idle15秒、Qwen3-4B、同步eager/TP1。所有组共用相同SharedPrefixConnector/generation；不要直接复用硬编码68请求历史SHA的run_shared_strategy_matrix.py冒充新输入。
4. 明确新输入数量和token门槛、源码差异、资源/原回执、非零提交、H2D、原始SSE复算及输出差异，再实现独立runner/auditor。关闭热路径诊断，各格独立启动服务。
5. 新pilot四组全部通过才同源码formal三次轮转；任一零覆盖/资源/来源/计时失败就停止并保存未运行格，不降reserve、不重跑筛结果。三重复只做描述性结果，保留退化和关闭警告。
6. 若保护虽触发却没有影响复用，先离线统计受保护前缀后续复用机会，再独立预注册机制诊断；不要把专门构造的正例当原工作负载收益。暂不扩大卡/模型。

## 独立正确性支线

- 同一先共享再保护批次的owner取消、完整抢占恢复、旧代回执组合GPU验证。分立GPU与CPU契约不等于全部组合已验收。
- 现有稀疏探针只严格恢复了3保护批次中的1个；性能轮总体H2D>0不扩大逐批bitwise范围。
- 实际DMA/driver fault、多worker/异步、永久I/O自动恢复、生产默认启用均未准入。
- 输出hash差异的数值/调度归因和关闭output_handler traceback仍开放。

## 复现

本轮两包共258文件，见evidence/shared-strategy-pilot-evidence.tar.gz和shared-strategy-formal-evidence.tar.gz。每包有逐文件SHA与启动前240项frozen-source.tar.gz。用verify_shared_strategy_evidence.py展开并复算，不能拿当前目录冒充历史源码。入口缺测试文件失败和不同范围的27/37测试分别保留；上一阶段961 CUDA/native是历史正确性基线。
