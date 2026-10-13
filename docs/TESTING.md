# 新包框架下的测试

全请求CPU候选最新回归：Windows688 passed、313 skipped、26 subtests；服务器强制native/CUDA1200 passed、零跳过、115 subtests，121条上游warning。新增23项，日志见 `validation/whole-step-2026-10-13/`。候选bind禁止服务接线，无新增模型性能实验。以下为前轮记录。

最新三项推进：剩余跨测试导入全部去除，支持层的历史Setup类不包含test方法。
Windows全量678 passed、312 skipped、26 subtests；固定服务器强制native/CUDA
1177 passed、零跳过、115 subtests、121条上游弃用warning。
九个受影响历史入口各自新进程运行，共136 passed、78 subtests，包含真实H2D/D2H。
writer入口的native路径隐式依赖已显式修复。全量首次AST一致性失败与后续成功
日志见 `validation/runtime-boundaries-2026-10-13/`；独立入口首次writer collection失败
保存在工具记录，后续九个日志全部落盘。没有新增模型服务性能实验。
运行时新增9项纯决策、1项独立导入架构、5项真实Scheduler连续准入契约。
详细结论见 [三项推进](experiments/2026-10-13/RUNTIME_ADMISSION_REVIEW.md)。

在仓库根目录执行 `python -m pytest -q`。唯一pytest配置为`pyproject.toml`，
由它提供`src`路径；旧`pytest.ini`已删除，因为它会覆盖pyproject配置。

主线24个测试文件已改为直接导入`cachepilot.runtime`、`cachepilot.integration`，
对应monkeypatch目标也随之更新。原测试断言保持不变。
`tests/architecture/test_package_contract.py`单独检查旧入口与新包的模块身份一致、
测试直接使用新包、包不能反向导入研究脚本，以及配置与源码快照契约。
历史实验/故障探针的测试继续引用`scripts`，不将它们混入主线包。

本地缺vLLM/LMCache时，相关测试明确跳过；这不代表已通过真实上游验证。
完整Linux固定环境使用：

```bash
CACHEPILOT_REQUIRE_RESERVATION_NATIVE=1 CACHEPILOT_RUN_CUDA_CONTRACT=1 python -m pytest -q
```

native产物和build.json需位于`artifacts/reservation-native`，与当前源码和环境匹配。
GPU命令运行真实native copy契约，不等于模型服务端到端或性能cell验收。

按模块修复时先定向运行，例如：

```bash
python -m pytest -q tests/test_shared_prefix_protection.py tests/test_bounded_protection.py
python -m pytest -q tests/test_generation_session_ledger.py tests/test_generation_rpc_client.py
python -m pytest -q tests/test_owned_service_state.py tests/test_owned_service_protocol.py
python -m pytest -q tests/architecture
```

2026-10-13适配验证：Windows全量665 passed、312 skipped、26 subtests；
独立Linux目录`/root/cachepilot-package-tests-20261013`使用固定Conda环境，强制native/CUDA：
**1158 passed、零跳过、115 subtests passed**，121条上游弃用警告。
新包入口下未发现原契约回归。最终仅清理测试中的未使用旧路径bootstrap，不改运行时代码。
证据见`validation/package-tests-2026-10-13/`。

## 公共测试支持层（2026-10-13）

`tests/cachepilot_test_support/` 不随产品包安装，测试显式导入所需 fixture：

| 模块 | 职责 | 依赖 |
| --- | --- | --- |
| rpc.py | Key、Client、Transport | 轻量协议，无 torch/vLLM/LMCache |
| pool.py | case、pool_state | 真实 BlockPool、LMCache manager |
| generation.py | combined | pool 与 generation bridge |
| connector.py | wired、step | pool、RPC double 与 Connector |

16个测试文件已适配，公共 fixture 不再从其他测试文件导入；无需在全局
conftest 提前导入上游库。八个入口分别启动新 Python 进程验证，共141项通过，
证明这些入口不依赖全量收集顺序。架构测试约束支持层不得反向导入测试模块，
RPC 支持模块必须可在不加载上游栈的独立进程中使用。

此前历史 capacity diagnostic 与 native research 的跨测试依赖已在后续
收敛中去除；下述旧计数保留为当时验证记录。

本轮首次全量1160项通过；额外副本回归出现1项旧TTL断言失败（1159项通过）：
20ms内必须释放新reader的假设不成立，第二个reservation可合法返回STALE_EPOCH。
测试现显式覆盖新reader也过期，保留旧reader不得释放新pin、重复释放拒绝和最终
资源闭合检查。运行时未改动。原失败日志与最终验证记录见
`validation/test-support-2026-10-13/`。第一次独立检查早于上传完成，目录不存在，
没有执行测试；上传结束后八个入口均完成。本次并非新的模型端到端或性能实验。

最终验证：Windows 667 passed、312 skipped、26 subtests；服务器强制native/CUDA
1161 passed、零跳过、115 subtests，121条上游弃用警告。

## 历史契约构件收敛（2026-10-13，第二轮）

进一步适配11个测试文件，共用实现位于：

| 模块 | 用途 |
| --- | --- |
| protection_contracts.py | 生产/容量诊断共用生命周期、拒绝与generation回执断言 |
| native_allocation.py | 固定源码SHA的真实KVCacheManager fixture |
| measurement.py | 合成回放测量文件及固定时间字段 |
| gpu_buffer.py | H2D/D2H共用PinnedBuffer，构造时才加载torch |

测试函数仍在原文件，参数化仍由原测试入口承担；容量诊断不再导入并调用其他
测试函数。六段抽取函数体经过AST核对保持一致，见extraction.txt。
PinnedBuffer仍分配uint8 pinned tensor，dtype由该tensor读取。
RPC、measurement、gpu_buffer导入不应提前加载torch/vLLM/LMCache。
架构测试对剩余8条跨测试模块导入列出明确白名单，禁止新增这类依赖。

剩余债务是OwnedLookupTests → LeasedLookupTests → TransferPlanTests初始化链，
以及ReusingAllocator。它们服务于历史native研究契约；下一轮提取不含test方法的
支持类，再由原unittest类组合这些支持类，避免继承时重复收集旧测试。
本轮运行时策略、native算法及既有实验归档均未修改。

Windows全量668 passed、312 skipped、26 subtests；隔离服务器目录
`/root/cachepilot-research-test-support-20261013`强制native/CUDA全量
1162 passed、零跳过、115 subtests，121条上游弃用警告。
证据见`validation/research-test-support-2026-10-13/`。

本轮11个受影响文件分别新进程运行，90项全部通过（含真实H2D/D2H CUDA）。
对应文件名与结果逐一记录于isolated.txt。
