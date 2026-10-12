# 共享前缀保护：独立引用 CPU 契约

2026-10-12。基于 [共享首块拒绝诊断](GENERATION_CAPACITY_DIAGNOSTIC.md)，实现独立 `SharedPrefixProtection` CPU 候选。服务器真实 vLLM BlockPool/LMCache manager/generation bridge 相关测试 **84 passed**，其中新增共享引用契约 **29项**；另本地两项源码差异约束测试通过。**尚未允许模型服务绑定，没有新GPU实验或性能收益结论。**

## 为什么不是简单删除引用检查

原策略假定所有被保护块都在空闲队列里，用free-rank恢复零步回滚顺序。共享首块ref>0时不在空闲队列里，单纯放开ref检查会在保存rank时找不到该块；若随后其他持有者释放引用，回滚也不能假造它以前的空闲队列位置。

候选采用三个明确变化，其他决策及回执路径复用原实现：

1. 允许已引用的完整哈希块参与候选，负引用数、null或缺hash仍拒绝。原候选哈希匹配、逐块token-prefix proof、范围/重复ID检查保持不变。调用真实BlockPool.touch为每个保护块增加**一份自己的引用**，不把外部引用当成自己的pin。
2. 只保存调度步原free queue中实际存在的block rank；共享块没有虚构rank。预算仍按完整16/32个保护引用计费，reserve仍按原保守公式及整个受保护块数检查，没有按净新增占用放宽容量门槛。
3. 提交前rollback释放自己的引用，只有最终ref=0且原rank存在的块才恢复前端次序；原共享块若其他owner已释放、此时归零，保留native free的尾部位置。提交后的释放仍完全由原manager回执执行，不另改队列顺序。

prepare/rollback先检查bridge健康，部分native注册后poison继续拒绝rollback/END。prefix/suffix票据、原batch identity、旧代隔离及commit/receipt逻辑没有改写。候选显式拒绝bind，原GenerationProtectionConnector、BoundedProtection、诊断Connector均未改变。

## 测试证据

29项新增真实库契约覆盖：

- 1/8/16个共享块、原引用1或4，成功/失败回执后只扣自己的引用，外部引用保留；实际free block数按净新增引用变化，预算仍保守。
- PREPARED和COMMITTED阶段，模拟外部owner释放/新增引用；保护引用在原回执或零步回滚前不消失。这里只直接使用真实BlockPool.touch/free模拟owner事件，不等于模型服务取消/抢占验证。
- 连续五次零步回滚保持原free queue顺序、pending票据和共享引用；token/hash校验不放松。
- 两个独立generation保护同一组16 blocks，正序/逆序原回执独立结算；多候选回滚不重复插入队列；第二次注册前/后故障保留全部未确定引用、禁止清理。
- 旧代成功/失败回执保留新代pending票据和共享引用；stale/duplicate receipt在原manager入口拒绝。
- metadata预检失败回滚只释放自己的pin；native注册后故障禁止误回滚和END；模型服务bind明确拒绝。

84项总数包含原bounded、generation bridge和Connector回归，不是全套native/CUDA重跑；66条依赖弃用警告单列。CPU fixture使用真实库及构造的token/block状态，不能替代Qwen服务KV bitwise准入。

两项本地源码契约把新prepare/rollback与原AST逐项比较，只允许上述引用判定、rank记录/恢复及健康检查差异；原reserve、排序、hash/proof、元数据提交等算法保持。它约束源码漂移，不是形式化并发证明。

## 首轮测试失败

首轮15通过、1失败：测试错误地把提交前rollback的前端恢复规则套到了COMMITTED回执上，要求首块rank大于最后保护块。实际native归还整批16块到尾部，rank为112–127，引用计数本身正确。修正测试区分两阶段，候选运行代码没有因此修改；随后22项、补充后29项候选契约通过。

[初次失败摘要](shared-prefix-contract/initial-test-failure.json)保留实际断言与rank。初次原始pytest输出仅在会话工具记录中，没有伪造完整日志；后续输出文件已归档。

## 源码和复现

- 实现：`scripts/shared_prefix_protection.py`。测试：`tests/test_shared_prefix_protection.py`、`tests/test_shared_prefix_source_contract.py`。
- [84项服务端测试](shared-prefix-contract/contract-final.txt)、[本地两项](shared-prefix-contract/source-contract-tests.txt)、[前一轮22项](shared-prefix-contract/contract-b.txt)。
- [231项源快照](shared-prefix-contract/shared-prefix-contract-source.tar.gz)，955,306 bytes，SHA256 `07b0ae89cc7a54d237a18e195f18805df6e78ef5bc0299f2fe28d6b5c56f6a1b`；[逐字节核对](shared-prefix-contract/source-byte-audit.json)通过。原诊断230项源没有变，仅新增candidate一项。测试源码随Git提交，源包scripts/configs不含tests。
- 服务器命令：`python -m pytest tests/test_shared_prefix_protection.py tests/test_bounded_protection.py tests/test_generation_protection_bridge.py tests/test_generation_protection_connector.py -q`。本地AST测试无native依赖。
- [收尾](shared-prefix-contract/closeout.json)：无新模型服务实验、端口空闲、GPU1MiB/0%，容器保持开机。

## 下一准入门槛

先实现独立opt-in服务Connector/绑定子类，保留当前CPU候选bind拒绝与旧策略默认。复用generation元数据/worker原回执路径，验证共享模式显式开关、支持环境限制及实际绑定类型；补服务callback/多候选故障契约后再GPU。

GPU诊断先固定此前新文本trace和h5/d1、one chunk，启用独立KV bitwise/原始transfer/资源审计；必须观察共享首块引用、真实保护提交、原回执释放及最终generation/资源闭合。随后覆盖共享owner取消/受害请求抢占、失败回执及zero-step rollback的真实服务路径。诊断文件与性能隔离，任何资源/身份/内容不一致停止。通过后才关闭探针重走固定四组pilot→formal；原零覆盖失败继续保留。

本次没有证明真实服务取消、异步/多worker、GPU DMA故障、部分pin获取异常或poison自动恢复，也没有因CPU通过而放开reserve或选择生产默认预算。
