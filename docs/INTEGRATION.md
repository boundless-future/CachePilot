# 接入位置与基线启动

状态：待 GPU 环境核验。以下命令是候选基线，不是已经测试的一键部署说明。

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

## 候选基线命令（Linux）

前提：已安装兼容运行时、外部 MP connector 可加载、模型文件可用。同一 GPU 节点运行两端；起步可同一容器内分终端启动，避免先引入跨容器 IPC 问题。

终端一：

```bash
lmcache server --l1-size-gb 16 --eviction-policy LRU
```

此处 L1 指 LMCache 自身存储层命名，不等于 vLLM GPU KV 池。

终端二，从项目根目录执行：

```bash
vllm serve Qwen/Qwen3-4B \
  --host 127.0.0.1 \
  --port 8000 \
  --dtype bfloat16 \
  --max-model-len 16384 \
  --enable-prefix-caching \
  --kv-transfer-config "$(cat configs/baseline-kv-transfer.json)"
```

上面的 JSON 配置显式指定外部模块。默认连接端口/地址必须结合所选版本 `--help` 和 quickstart 验证；更换部署拓扑后不可照搬。先记录默认 GPU KV 容量，再为各基线固定可比预算。

客户端请求 `http://127.0.0.1:8000/v1/chat/completions`。从本地访问远程机器时使用 SSH 隧道。第一次只做小输入冷/热请求，再逐步增加压力。

最大模型长度包含输入与输出；16K 实验不要同时发送 16K 输入和额外输出。BF16 KV 的实际解析结果需要在启动日志确认。

## 原生 vLLM 对照

去掉整个 `--kv-transfer-config`，保留相同模型、dtype、前缀缓存和资源预算。不要将关闭前缀缓存作为唯一基线。

## 将来 CachePilot 的接入改动

1. 实现 `OffloadPolicy`，保留 add/drain/drop/reset/failure 等合约。
2. 在 `create_offload_policy` 添加明确选择分支。
3. 按需从 manager/worker 收集在途字节与完成时间；不混同 scheduler 回执耗时和 GPU 拷贝耗时。
4. 确认进程加载的是开发 checkout 的 LMCache，而不是另一个 site-packages 副本。
5. 同一代码版本顺序切换默认与新策略做 A/B；不同时运行两个大模型服务抢卡测性能。

真正可用的安装、启动和版本锁文件在环境验证完成后补齐。
