# 接入位置与基线启动

运行环境和验收记录见 [ENVIRONMENT.md](ENVIRONMENT.md)。以下脚本在容器内激活 `cachepilot` 环境后使用；它们负责前台启动服务，自动验收脚本负责依次启停自己的测试进程。

## 进程与职责

```text
Agent / 回放客户端 --HTTP--> vLLM API
                               |
                          Scheduler
                               |
                   LMCacheMPConnector（外部模块）
                               |
                      LazyOffloadManager
                               |
                 OffloadPolicy：默认 / 未来 CachePilot

vLLM GPU worker <---- 现有 IPC/传输路径 ----> LMCache MP server
     GPU KV                                      CPU KV
```

CachePilot 策略在调度器侧被调用，不新增服务端口。manager 负责 pin/unpin 和执行生命周期。LMCache MP server 处理存储/传输。第一版 Agent 无须改变模型 API。

## 已有与待开发

已有：EVICTION_AWARE、FIFO、压力信号、前缀闭合、哈希复核、异步提交和回执。

待开发：只有基线分析证实需要时，新增 CachePilot 策略/工厂分支、反馈采集和配置。当前 `CACHEPILOT` 不是合法上游配置，不应直接用于启动。

## 基线命令（Linux）

前提：已安装兼容运行时、外部 MP connector 可加载、模型文件可用。同一 GPU 节点运行两端；起步可同一容器内分终端启动，避免先引入跨容器 IPC 问题。

终端一：

```bash
bash scripts/lmcache-server.sh
```

此处 L1 指 LMCache 自身存储层命名，不等于 vLLM GPU KV 池。

终端二，从项目根目录执行：

```bash
bash scripts/serve.sh eviction
```

`eviction` 使用固定研究提交中的 `EVICTION_AWARE`；`fifo` 使用 FIFO，`immediate` 不启用 lazy-offload，`baseline` 仅运行原生 vLLM。FIFO 和 immediate 配置文件名中的 `0.5.5` 保留了原始测试来源，不表示 FIFO 已在 PyPI 0.5.5 上通过。源码来源见 [lmcache-source.json](../configs/lmcache-source.json)。

实验扩展：`adaptive` 切换 horizon，`allocation` 以真实物理块分配量替换历史压力信号；两者均不启用逐步 JSON 诊断日志。`decision` 观测默认策略，`allocation-decision` 观测修正信号后的策略，诊断模式不用于计时对比。计数修正的零 token 步累计规则与限制见 [实际分配信号消融](experiments/2026-09-26/ALLOCATION_SIGNAL.md)。这些外部 Connector 只适用于当前固定源码版本，尚不是上游正式策略。

启动脚本固定 8K 上下文、2 GiB GPU KV 池和最多 4 个序列；这是功能验收预算，便于触发淘汰，不是建议的最终性能配置。`KV_CACHE_BYTES` 和 `MODEL_PATH` 可覆盖默认值。LMCache L1 为 16 GiB，5555/8080 和 vLLM 8000 都绑定回环地址。

脚本设置 `--shutdown-timeout 10`，给 EngineCore 时间注销 LMCache GPU IPC 映射；不要以默认 0 秒超时强杀后立即重启来模拟正常回载。`--gpu-memory-utilization 0.80` 为启动检查保留余量，实际 GPU KV 大小由 `--kv-cache-memory-bytes` 显式固定。

客户端请求 `http://127.0.0.1:8000/v1/chat/completions`。从本地访问远程机器时使用 SSH 隧道。第一次只做小输入冷/热请求，再逐步增加压力。

最大模型长度包含输入与输出；8K 输入不能再额外生成而不超限。BF16 KV 的实际解析结果需要在启动日志确认。

## 原生 vLLM 对照

使用 `bash scripts/serve.sh baseline`，保留相同模型、dtype、前缀缓存和资源预算。

## 自动功能验收

停止占用测试端口的服务后，在项目根目录运行：

```bash
python scripts/validate_environment.py --output artifacts/validation-run-01
```

输出目录必须尚不存在，避免覆盖实验。默认开启 vLLM 编译优化；加 `--eager` 可切换到 eager。默认检查四种模式，各模式重新创建 LMCache 服务；同一模式内部重启 vLLM 时保留 CPU 缓存。脚本对冷/热输出、GPU hit、真实 store、重启后的 CPU retrieve 和单条 prompt 的输出一致性做断言，保存原始响应、指标与进程日志，并在结束或失败时停止自己的服务。

`eviction` 会提交 16 个不同前缀的请求，令工作集超过 GPU KV 容量。此验收不是性能 benchmark，也不覆盖取消、并发抢占、请求 ID 重用或所有输出正确性情形。

## 将来 CachePilot 的接入改动

1. 实现 `OffloadPolicy`，保留 add/drain/drop/reset/failure 等合约。
2. 在 `create_offload_policy` 添加明确选择分支。
3. 按需从 manager/worker 收集在途字节与完成时间；不混同 scheduler 回执耗时和 GPU 拷贝耗时。
4. 确认进程加载的是开发 checkout 的 LMCache，而不是另一个 site-packages 副本。
5. 同一代码版本顺序切换默认与新策略做 A/B；不同时运行两个大模型服务抢卡测性能。

`scripts/build-lmcache.sh` 针对已安装的 Python 3.12 / PyTorch 2.13.0 / CUDA 13.0 构建固定 LMCache 提交；默认仅编译 RTX 4090 所需的 SM 8.9。它不从空系统安装完整依赖，更换 GPU 架构时需修改 `TORCH_CUDA_ARCH_LIST`。
