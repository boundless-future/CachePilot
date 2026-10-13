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
