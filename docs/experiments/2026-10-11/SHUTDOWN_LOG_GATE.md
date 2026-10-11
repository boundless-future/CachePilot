# 正式消融中的 vLLM 停机日志分类

日期：2026-10-11。代码提交：`c643a1b`。这项改动只调整实验审计，不修改 vLLM、LMCache、保护策略或计时路径，也不宣称修复了 vLLM 关闭竞态。

## 原始失败

`artifacts/protection-ablation-validation-20261011a/zero-r0` 完成 68/68 个响应，H2D 为 1,811,939,328 B，运行期与退出后资源闭合，两个 Connector summary 均关闭完成。但严格日志门槛因下列停机异常使整轮失败，并停止余下 cell：

```text
08:09:32 API server: shutdown triggered
08:09:32 Process manager: send sigterm to process EngineCore
08:09:32 EngineCore: trigger received signal=SIGTERM
08:09:32 EngineCore: request processing complete; starting resource teardown
08:09:35 MPClient: engine manager stopped
08:09:35 MPClient: complete
08:09:35 AsyncLLM output_handler failed.
          ... get_output_async() ... EngineDeadError ...
08:09:35 Application shutdown complete.
```

同 trace 的前一轮 pilot 没有这一 ERROR。此前原生 vLLM 和多种 Connector 的历史实验也记录过同类主动关闭后异常，不能直接将其归因于保护策略。两轮均有 CUDA IPC producer 退出 WARNING；本次没有定位或修复该 WARNING。

## 安装源码证据

固定 vLLM 0.30.0 的 `vllm/v1/engine/async_llm.py:277` 在 `shutdown()` 中先调用 `engine_core.shutdown()`，再取消 `output_handler`；该 handler 在 789 行等待 `get_output_async()`，843 行对异常写日志。

`core_client.py:740` 的 MPClient shutdown 先关闭 engine manager，再清理后台资源；输出队列协程被取消时向队列放入 `EngineDeadError()`（约 1158 行），`get_output_async()` 在 1175 行将取出的异常抛出。这个顺序与观测到的退出窗口一致：上层 handler 尚未取消时可先消费底层关闭产生的异常。本次不把日志推断扩大为任意 EngineDeadError 都可忽略。

## 新门槛

仅同时满足以下条件时，将**精确匹配的 8 行 traceback**单独记录为 `shutdown_teardown_errors`：

- runner 已完成全部请求及 token 合约、确认运行期资源闭合；记录主动 stop 前日志行数、进程 PID、存活状态和时间。
- 正常进程退出码为 0；日志在 stop 边界之后按顺序出现主动关闭、发送 SIGTERM、EngineCore 接收 SIGTERM、完成请求处理并开始 teardown、engine manager 停止。
- 8 行都是同一个 API PID 的 `async_llm.py` 错误，调用链和最终异常内容精确匹配，随后出现同 PID 的 application shutdown complete。

任何额外/交错 ERROR、其他 traceback、请求期异常、不同进程或不完整关闭仍进入 `service_errors` 并使 cell 失败。资源、回载、保护提交和两个 Connector summary 的原门槛保留。汇总工具重新读取原始日志并使用同一规则；原始日志没有删除或改写。

历史失败轮没有当时记录的 runner stop 边界，因此继续保持 `passed=false`，不追溯改判、不混入正式聚合。基于历史日志构造 context 的检查仅测试解析签名，不是补造历史实验成功证据。

## 验证

- 本地和服务器定向测试各 27 项通过，覆盖缺字段、异常退出、错误 PID、停机边界、标记乱序、缺行、运行期异常和相邻额外 ERROR。
- 服务器 `python -m pytest tests -q`：633 passed、9 skipped、115 subtests passed。这不是显式开启 CUDA 专项测试的全通过声明。本机全套测试因缺少 LMCache/msgspec 等依赖且工作目录不在项目根目录，收集失败，未计入通过数。
- `protection-ablation-validation-shutdown-pilot-20261011a/zero-r0`：68/68 请求完成，H2D 1,811,939,328 B，运行期与退出后资源闭合，无 ERROR、无分类后的停机异常。该轮验证新 runner 正常路径，没有重新触发竞态。

正式验证使用新的独立实验目录 `protection-ablation-validation-20261011b`。分类异常数量、失败轮及性能结果应随该批报告一起公开；允许明确分类的停机异常不等于全部关闭日志无错或达到生产故障恢复要求。

失败轮、两个 pilot、已安装 vLLM 源码快照及 SHA-256 已归档至 [shutdown-gate evidence](ablation-evidence/shutdown-gate-evidence-20261011a.tar.gz)。
