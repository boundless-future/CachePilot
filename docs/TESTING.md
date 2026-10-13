# 新包框架下的测试

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

历史 capacity diagnostic 仍有复用测试函数的依赖；更早的 native research
测试也尚未全部解耦。本轮没有修改这些历史验证的断言或引入全局路径注入。

本轮首次全量1160项通过；额外副本回归出现1项旧TTL断言失败（1159项通过）：
20ms内必须释放新reader的假设不成立，第二个reservation可合法返回STALE_EPOCH。
测试现显式覆盖新reader也过期，保留旧reader不得释放新pin、重复释放拒绝和最终
资源闭合检查。运行时未改动。原失败日志与最终验证记录见
`validation/test-support-2026-10-13/`。第一次独立检查早于上传完成，目录不存在，
没有执行测试；上传结束后八个入口均完成。本次并非新的模型端到端或性能实验。

最终验证：Windows 667 passed、312 skipped、26 subtests；服务器强制native/CUDA
1161 passed、零跳过、115 subtests，121条上游弃用警告。
