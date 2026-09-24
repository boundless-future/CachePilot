# 2026-09-24 环境验收

本轮目标是验证研究所需的 LMCache lazy-offload 路径和 vLLM 默认编译模式，不是测量 CachePilot 的性能收益。

## 最终结果

`optimized-v3` 四种模式均通过。自动断言结果见 [summary.json](summary.json)，日志摘录及停机状态见 [evidence.json](evidence.json)。

| 模式 | GPU 热命中 tokens | CPU store 完成次数 | 重启后 CPU hit tokens | 重启后 GPU hit tokens | 输出回归 |
|---|---:|---:|---:|---:|---|
| 原生 vLLM | 1536 | 不适用 | 不适用 | 不适用 | 参考输出 |
| immediate | 1536 | 3 | 1536 | 0 | 一致 |
| FIFO | 1536 | 3 | 1536 | 0 | 一致 |
| EVICTION_AWARE | 1536 | 14 | 1536 | 0 | 一致 |

三个 LMCache 模式的回载均增加 1 次真实 CPU→GPU transfer。EVICTION_AWARE 冷阶段最终 ledger：admitted=18、emitted=14、pending=4、dropped_evicted=0、emitted_overdue=0。这里只记录该工作负载的结果，不据此推导普遍零丢弃率。

三种模式最终停机快照中 GPU 注册为空、读写锁和临时对象均为 0；FIFO/EVICTION_AWARE 的活跃会话记录分别为 1/4，immediate 为 0。尚未等待并验证 TTL 回收，P1 的生命周期检查保留这一待办。最终关闭本次测试服务后 GPU 占用回到 1 MiB，服务器本身保持开机。

日志复核：三种 LMCache 模式的最终运行没有 ERROR/Traceback；原生 vLLM 在所有生成断言通过后的停机阶段出现一次 `AsyncLLM output_handler` / `EngineDeadError`。因此这里的 PASS 只表示生成与缓存断言通过，不表示关闭流程的日志完全无错误；该停机行为也保留在 evidence.json 中。

## 版本与来源

- RTX 4090 24GB，Qwen3-4B；模型 revision 沿用 2026-09-23 记录。
- Python 3.12.14，PyTorch 2.13.0 / CUDA 13.0，vLLM 0.30.0。
- LMCache 从固定提交 `1a4b40b1d79b0e76244f127f96ee0982f8bd270f` 编译并安装，来源和校验值见 [lmcache-source.json](../../../configs/lmcache-source.json)。
- `0.5.5+g1a4b40b1d` 是归档源码构建时显式指定的本地标签，不等于 PyPI 0.5.5 发布包。
- 实际包版本见 [runtime.json](runtime.json) 和 [pip-freeze.txt](pip-freeze.txt)。后者是环境快照，包含本地 wheel 路径，不能直接视作可移植安装锁文件。

## 测试方法

在相同模型、8K 最大上下文、2 GiB GPU KV 预算、16 GiB CPU L1 池和最多 4 个序列的配置下，依次运行原生 vLLM、immediate、FIFO、EVICTION_AWARE。默认 torch.compile 与 CUDA Graph 启用。

固定 prompt 为 1,550 tokens，生成 32 tokens，temperature=0、seed=42。检查同一 prompt 的冷请求、GPU 热命中以及与原生 vLLM 的输出一致性。然后保持 LMCache 服务运行、重启 vLLM，要求 GPU hit=0、CPU retrieve 计数增加且 external hit≥1,536 tokens。最后再次重复请求，覆盖原 wheel 曾崩溃的请求完成路径。

EVICTION_AWARE 使用默认 horizon=2.5、max_drain_per_step=64，不设置超时强制卸载；通过 16 个不同前缀的长请求令工作集超过 GPU 容量，检查真实压力触发的 store 和回载。FIFO 的 threshold=1/select_count=1 仅为功能测试设置。

复现：

```bash
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate cachepilot
cd /root/CachePilot
python scripts/validate_environment.py --output artifacts/new-validation
```

## 排查记录

1. PyPI LMCache 0.5.5 在请求完成时会直接调用旧 FIFO 的 `mark_req_finished`，对不存在的 pending 项抛出异常。研究提交采用 `LazyOffloadManager` 管理请求生命周期；本轮使用该上游实现，未修改 vendor 源码，也不将此记作 CachePilot 自研策略。
2. 第一轮测试的 FIFO 重启失败于显存启动检查，而非请求完成回调。vLLM 默认 `shutdown_timeout=0` 强杀 EngineCore 时，旧 GPU IPC 注册可能未释放。启动脚本改为 `--shutdown-timeout 10`，验收脚本要求旧 GPU 注册清空后再启动新引擎。另设 GPU utilization=0.80 留出启动余量；实际 KV 池仍由显式字节数固定。
3. 第二轮发现测试脚本向整个进程组发 SIGTERM，vLLM 主进程又向 EngineCore 发一次 SIGTERM。EngineCore 在 teardown 前将信号处理器恢复为默认值，第二次信号可能打断清理。脚本改为仅通知主进程；超时后才对自己的进程组强杀清理。这是测试进程管理修正，不是上游策略改动。
4. 部分排查运行在关闭时输出 Python `resource_tracker` 的 semaphore 警告；FIFO 停机后曾有 1 个会话残留，但对象读写锁均为 0。该会话层源码的默认 TTL 为 600 秒、清理间隔为 60 秒，本轮未验证等待 TTL 后的清理行为。不能仅凭 HTTP 200 宣称所有生命周期场景无资源问题。

完整原始输出保存在远端 `/root/CachePilot/artifacts/environment-2026-09-24/`；`optimized` 和 `optimized-v2` 为排查运行，`optimized-v3` 使用仅通知主进程的关闭流程。

原始包已同时下载到本地忽略目录 `artifacts/environment-2026-09-24.tar.gz`，SHA-256：`ad8f27a15b24f3f5bef555435519d2eebd537c7a1892abba19593c0651bcd709`。包中保留失败运行，避免只保留成功证据。可迁移的摘要与日志摘录纳入 Git；大模型、wheel 和完整原始包不纳入 Git。

## 适用边界

这是串行、单模型、单 prompt 输出回归及有限压力的功能验收，不证明所有并发、取消、抢占、请求 ID 重用或错误恢复场景正确。不把这些耗时当作吞吐/TTFT 性能对比；下一阶段需单独固定 workload、采集流式 TTFT 和排队/搬运指标，并重复实验。
