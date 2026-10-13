# 工程收敛计划

2026-10-13。目标是让运行主线具有唯一源码位置和可逐模块修改的依赖边界。此轮仅做工程迁移，不改变保护预算、reserve、请求身份、pin/receipt语义或性能结论。

## 执行顺序

1. 将共享保护Connector的依赖闭包、所有权服务和服务观察器迁到`src/cachepilot`；旧`scripts`路径保留兼容入口。包内不得反向依赖实验脚本。
2. 增加安装描述、统一配置/CLI入口和最小示例。上游固定环境作为外部前提，不通过安装CachePilot升级torch/vLLM/LMCache。
3. 将源码快照覆盖扩展到新包及构建信息；历史实验使用其归档源码，新代码不能冒充原运行时。
4. 运行本地可用回归与远端隔离目录真实库契约，校验旧入口与新入口身份一致、源码迁移行为一致、安装产物可导入。
5. 更新README、架构图、模块修改顺序和TASKS。逐模块迁移实验工具另列后续，不复制旧实验结果到新签名。

## 本轮边界

- 保留现有Connector继承顺序，结构迁移通过后再拆分组合；同时改继承和搬目录会使回归难以归因。
- 历史探针、故障注入、矩阵入口留在`scripts/`并分类导航；稳定运行逻辑不再放在那里。
- 已归档实验包、原始失败、旧源码SHA不改。新源码检查应拒绝旧签名的formal续跑。
- 不以目录整理宣称生产可用，不新建空模块假装策略层已解耦。

## 初始验证记录

本地全量收集在迁移前已因缺少lmcache/msgspec报2个collection errors、25 skipped。保留`artifacts/engineering-baseline-local.txt`，本地和固定Linux环境测试范围分别记录，不把依赖缺失计作迁移回归。

## 本轮执行与验收

- 主线21个模块迁入`src/cachepilot`，旧路径仅转发到同一module对象；`tests/architecture`检查全映射的身份一致和包内没有反向导入研究脚本。诊断`OwnedRejectedRetrieveConnector`仍由旧入口按需提供，未混入主线类实现。
- `pyproject.toml`提供无上游自动升级的安装包；`cachepilot config/doctor/manifest/server`提供显式配置、依赖盘点、源码快照和带观察器的LMCache服务入口。reservation扩展继续按环境单独编译；wheel安装需`CACHEPILOT_NATIVE_DIR`。`examples/shared-prefix`给手工接线方式。
- 新`source_sha`纳入`src/cachepilot`递归Python源、native源和`pyproject.toml`，原`scripts/configs`继续覆盖。离线核对也能报告这些新增文件。新旧源码签名不可混用；未修改历史证据或失败记录。
- Windows可用测试：663 passed、310 skipped、26 subtests passed；本地缺完整LMCache/msgspec。固定Linux环境在`/root/cachepilot-engineering-20261013`独立目录复验：1147 passed、9 skipped、115 subtests passed。首轮漏复制native构建目录导致37个测试失败；补全原编译产物后全量重跑通过，原日志`remote-pytest.log`在隔离目录被覆盖，首轮错误输出保存在本轮工具记录。后续不能将该首轮当作代码回归。
- Windows构建`cachepilot-0.1.0-py3-none-any.whl`；远端仅安装至独立`wheel-install`目录，验证脱离checkout时可导入配置、vLLM Connector和LMCache服务，并定位native模块。远端原`/root/CachePilot`未pull或覆盖；未启动性能cell或GPU服务。

## 后续逐模块推进

1. 保持这一基线，修候选/预算或服务生命周期时只动所属包模块与对应契约测试，先跑全量CPU/native再申请GPU准入。
2. 分批归类历史实验、审计和故障注入脚本；保持旧路径、既有归档和源签名解释，避免一次性搬迁影响实验对比。
3. 继承链与`runtime`/`integration`耦合在此轮仅定位、未拆分；选择明确的状态所有者及接口后逐段重构。没有新的稳定性能收益证据。

## 测试迁移进度（2026-10-13）

主线测试已直接导入新包；旧pytest.ini覆盖pyproject的问题已修复。
公共支持层按RPC/pool/generation/connector及保护断言/native分配/测量/GPU buffer
分层，容量诊断不再通过调用其他文件的test函数复验契约。
剩余历史unittest初始化已抽成research_lookup/research_leased_lookup/research_transfer_plan与allocator支持模块；跨测试导入白名单已删除，全测试禁止导入其他test模块。
具体运行范围、计数和证据以[TESTING](TESTING.md)为准。此进度仅说明测试组织
收敛，候选排序与保守准入决策已分离；完整继承链解耦与3C收益验收仍未完成。
